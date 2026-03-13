#include "input/ffmpeg_rtsp_reader.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <sstream>
#include <thread>
#include <vector>

#include <opencv2/imgproc.hpp>

#include "platform/win_process_pipe.h"

namespace hogak::input {

namespace {

constexpr int kFreezeProbeWidth = 64;
constexpr int kFreezeProbeHeight = 36;
constexpr int kFreezeProbeEveryN = 8;
constexpr double kFreezeMotionThreshold = 0.01;
constexpr double kFreezeRestartSec = 5.0;
constexpr double kLateFrameIntervalMs = 45.0;

bool input_pipe_format_is_nv12(const std::string& format) {
    return format == "nv12";
}

int input_frame_rows(int height, const std::string& format) {
    if (input_pipe_format_is_nv12(format)) {
        return height + (height / 2);
    }
    return height;
}

int input_frame_type(const std::string& format) {
    if (input_pipe_format_is_nv12(format)) {
        return CV_8UC1;
    }
    return CV_8UC3;
}

std::size_t input_frame_bytes(int width, int height, const std::string& format) {
    if (input_pipe_format_is_nv12(format)) {
        return static_cast<std::size_t>(width) * static_cast<std::size_t>(height + (height / 2));
    }
    return static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3ULL;
}

cv::Mat allocate_input_frame(int width, int height, const std::string& format) {
    return cv::Mat(input_frame_rows(height, format), width, input_frame_type(format));
}

cv::Mat nv12_luma_plane(const cv::Mat& nv12_frame, int width, int height) {
    if (nv12_frame.empty() || nv12_frame.type() != CV_8UC1 || nv12_frame.cols != width || nv12_frame.rows < height) {
        return {};
    }
    return cv::Mat(height, width, CV_8UC1, const_cast<std::uint8_t*>(nv12_frame.ptr<std::uint8_t>(0)), nv12_frame.step);
}

std::string quote_arg(const std::string& text) {
    std::string out;
    out.reserve(text.size() + 2);
    out.push_back('"');
    for (const char ch : text) {
        if (ch == '"') {
            out.push_back('\\');
        }
        out.push_back(ch);
    }
    out.push_back('"');
    return out;
}

double fps_from_count(std::size_t count, std::int64_t span_ns) {
    if (count < 2 || span_ns <= 0) {
        return 0.0;
    }
    return static_cast<double>(count - 1) * 1'000'000'000.0 / static_cast<double>(span_ns);
}

cv::Mat make_freeze_probe_gray(const cv::Mat& current_frame, const std::string& format, int width, int height) {
    if (current_frame.empty()) {
        return {};
    }
    if (input_pipe_format_is_nv12(format)) {
        cv::Mat gray = nv12_luma_plane(current_frame, width, height);
        if (gray.empty()) {
            return {};
        }
        cv::Mat resized;
        cv::resize(gray, resized, cv::Size(kFreezeProbeWidth, kFreezeProbeHeight), 0.0, 0.0, cv::INTER_AREA);
        return resized;
    }
    cv::Mat gray;
    cv::cvtColor(current_frame, gray, cv::COLOR_BGR2GRAY);
    cv::resize(gray, gray, cv::Size(kFreezeProbeWidth, kFreezeProbeHeight), 0.0, 0.0, cv::INTER_AREA);
    return gray;
}

double frame_motion_score(const cv::Mat& previous_probe_gray, const cv::Mat& current_probe_gray) {
    if (previous_probe_gray.empty() || current_probe_gray.empty()) {
        return 0.0;
    }
    if (current_probe_gray.size() != previous_probe_gray.size()) {
        return 0.0;
    }
    cv::Mat diff;
    cv::absdiff(previous_probe_gray, current_probe_gray, diff);
    return cv::mean(diff)[0];
}

bool is_effectively_identical_probe(const cv::Mat& previous_probe_gray, const cv::Mat& current_probe_gray) {
    if (previous_probe_gray.empty() || current_probe_gray.empty()) {
        return false;
    }
    if (current_probe_gray.empty() || current_probe_gray.size() != previous_probe_gray.size()) {
        return false;
    }
    return cv::countNonZero(current_probe_gray != previous_probe_gray) == 0;
}

bool can_reuse_frame_storage(const cv::Mat& frame, int width, int height, const std::string& format) {
    return !frame.empty() &&
        frame.rows == input_frame_rows(height, format) &&
        frame.cols == width &&
        frame.type() == input_frame_type(format) &&
        frame.isContinuous() &&
        frame.u != nullptr &&
        frame.u->refcount == 1;
}

std::string resolve_ffmpeg_bin(const std::string& explicit_path) {
    if (!explicit_path.empty()) {
        return explicit_path;
    }

    char* env_value = nullptr;
    std::size_t env_size = 0;
    if (_dupenv_s(&env_value, &env_size, "FFMPEG_BIN") == 0 && env_value != nullptr && env_size > 0) {
        std::string resolved(env_value);
        free(env_value);
        return resolved;
    }

    const auto local = std::filesystem::path(".") / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe";
    if (std::filesystem::exists(local)) {
        return local.string();
    }

    return "ffmpeg";
}

}  // namespace

FfmpegRtspReader::~FfmpegRtspReader() {
    stop();
}

bool FfmpegRtspReader::start(
    const hogak::engine::StreamConfig& config,
    const std::string& ffmpeg_bin,
    const std::string& input_runtime) {
    stop();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        config_ = config;
        ffmpeg_bin_ = resolve_ffmpeg_bin(ffmpeg_bin);
        input_runtime_ = input_runtime;
        snapshot_ = ReaderSnapshot{};
        frames_.clear();
        frames_.resize(static_cast<std::size_t>(std::max(1, config.max_buffered_frames)));
        frame_start_index_ = 0;
        frame_count_ = 0;
    }
    running_.store(true);
    thread_ = std::thread(&FfmpegRtspReader::run, this);
    return true;
}

void FfmpegRtspReader::stop() {
    running_.store(false);
    if (thread_.joinable()) {
        thread_.join();
    }
}

ReaderSnapshot FfmpegRtspReader::snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return snapshot_;
}

std::vector<BufferedFrameInfo> FfmpegRtspReader::buffered_frame_infos() const {
    std::vector<BufferedFrameInfo> infos;
    buffered_frame_infos(&infos);
    return infos;
}

void FfmpegRtspReader::buffered_frame_infos(std::vector<BufferedFrameInfo>* infos_out) const {
    if (infos_out == nullptr) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    infos_out->clear();
    infos_out->reserve(buffered_frame_count_locked());
    for (std::size_t index = 0; index < buffered_frame_count_locked(); ++index) {
        const auto* frame = buffered_frame_at_locked(index);
        if (frame == nullptr) {
            continue;
        }
        BufferedFrameInfo info;
        info.frame = frame->frame;
        info.seq = frame->seq;
        info.timestamp_ns = frame->timestamp_ns;
        infos_out->push_back(std::move(info));
    }
}

bool FfmpegRtspReader::running() const noexcept {
    return running_.load();
}

bool FfmpegRtspReader::copy_latest_frame(cv::Mat* frame_out, std::int64_t* seq_out, std::int64_t* ts_out) const {
    if (frame_out == nullptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto* latest = latest_buffered_frame_locked();
    if (!snapshot_.has_frame || latest == nullptr) {
        return false;
    }
    return copy_buffered_frame(*latest, frame_out, seq_out, ts_out);
}

bool FfmpegRtspReader::copy_oldest_frame(cv::Mat* frame_out, std::int64_t* seq_out, std::int64_t* ts_out) const {
    if (frame_out == nullptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto* oldest = oldest_buffered_frame_locked();
    if (!snapshot_.has_frame || oldest == nullptr) {
        return false;
    }
    return copy_buffered_frame(*oldest, frame_out, seq_out, ts_out);
}

bool FfmpegRtspReader::copy_frame_by_seq(
    std::int64_t seq,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out) const {
    if (frame_out == nullptr) {
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!snapshot_.has_frame || buffered_frame_count_locked() == 0) {
        return false;
    }

    for (std::size_t index = buffered_frame_count_locked(); index > 0; --index) {
        const auto* buffered = buffered_frame_at_locked(index - 1);
        if (buffered != nullptr && buffered->seq == seq) {
            return copy_buffered_frame(*buffered, frame_out, seq_out, ts_out);
        }
    }
    return false;
}

bool FfmpegRtspReader::copy_closest_frame(
    std::int64_t target_ts_ns,
    bool prefer_past,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out) const {
    if (frame_out == nullptr) {
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!snapshot_.has_frame || buffered_frame_count_locked() == 0) {
        return false;
    }

    const BufferedFrame* best = nullptr;
    const BufferedFrame* best_past = nullptr;
    std::int64_t best_delta = 0;
    std::int64_t best_past_delta = 0;

    for (std::size_t index = 0; index < buffered_frame_count_locked(); ++index) {
        const auto* buffered_ptr = buffered_frame_at_locked(index);
        if (buffered_ptr == nullptr) {
            continue;
        }
        const auto& buffered = *buffered_ptr;
        const auto delta = std::llabs(buffered.timestamp_ns - target_ts_ns);
        if (best == nullptr || delta < best_delta || (delta == best_delta && buffered.seq > best->seq)) {
            best = buffered_ptr;
            best_delta = delta;
        }
        if (buffered.timestamp_ns <= target_ts_ns) {
            const auto past_delta = target_ts_ns - buffered.timestamp_ns;
            if (best_past == nullptr || past_delta < best_past_delta || (past_delta == best_past_delta && buffered.seq > best_past->seq)) {
                best_past = buffered_ptr;
                best_past_delta = past_delta;
            }
        }
    }

    if (prefer_past && best_past != nullptr) {
        return copy_buffered_frame(*best_past, frame_out, seq_out, ts_out);
    }
    if (best == nullptr) {
        return false;
    }
    return copy_buffered_frame(*best, frame_out, seq_out, ts_out);
}

void FfmpegRtspReader::run() {
    const auto frame_bytes = input_frame_bytes(config_.width, config_.height, config_.input_pipe_format);
    std::deque<std::int64_t> receive_times_ns;
    std::deque<double> frame_intervals_ms;
    std::deque<double> read_durations_ms;
    cv::Mat read_frame = allocate_input_frame(config_.width, config_.height, config_.input_pipe_format);
    cv::Mat recycled_frame;
    cv::Mat previous_probe_gray;
    std::int64_t freeze_started_ns = 0;
    std::int64_t freeze_probe_index = 0;
    std::int64_t last_receive_ts_ns = 0;
    double last_motion_mean = 0.0;
    double last_frame_interval_ms = 0.0;
    double max_frame_interval_ms = 0.0;
    double frame_intervals_sum_ms = 0.0;
    double read_durations_sum_ms = 0.0;
    std::int64_t late_frame_intervals = 0;

    while (running_.load()) {
        platform::WinProcessPipe pipe;
        std::string process_error;
        if (!pipe.start(build_command_line(), process_error)) {
            {
                std::lock_guard<std::mutex> lock(mutex_);
                snapshot_.last_error = process_error;
                snapshot_.launch_failures += 1;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(
                static_cast<int>(std::max(0.2, config_.reconnect_cooldown_sec) * 1000.0)));
            continue;
        }

        {
            std::lock_guard<std::mutex> lock(mutex_);
            snapshot_.last_error.clear();
            snapshot_.content_frozen = false;
            snapshot_.frozen_duration_sec = 0.0;
        }

        while (running_.load() && pipe.running()) {
            std::size_t bytes_read = 0;
            if (read_frame.empty() ||
                read_frame.rows != input_frame_rows(config_.height, config_.input_pipe_format) ||
                read_frame.cols != config_.width ||
                read_frame.type() != input_frame_type(config_.input_pipe_format) ||
                !read_frame.isContinuous()) {
                read_frame = allocate_input_frame(config_.width, config_.height, config_.input_pipe_format);
            }
            const auto read_started_ns = now_ns();
            if (!pipe.read_exact(read_frame.data, frame_bytes, bytes_read) || bytes_read != frame_bytes) {
                std::lock_guard<std::mutex> lock(mutex_);
                snapshot_.read_failures += 1;
                if (snapshot_.last_error.empty()) {
                    snapshot_.last_error = "ffmpeg frame read failed";
                }
                break;
            }
            const auto read_finished_ns = now_ns();
            const double read_duration_ms =
                static_cast<double>(std::max<std::int64_t>(0, read_finished_ns - read_started_ns)) / 1'000'000.0;
            read_durations_ms.push_back(read_duration_ms);
            read_durations_sum_ms += read_duration_ms;
            if (read_durations_ms.size() > 90) {
                read_durations_sum_ms -= read_durations_ms.front();
                read_durations_ms.pop_front();
            }

            const auto ts_ns = read_finished_ns;
            receive_times_ns.push_back(ts_ns);
            if (receive_times_ns.size() > 90) {
                receive_times_ns.pop_front();
            }
            if (last_receive_ts_ns > 0) {
                last_frame_interval_ms =
                    static_cast<double>(std::max<std::int64_t>(0, ts_ns - last_receive_ts_ns)) / 1'000'000.0;
                max_frame_interval_ms = std::max(max_frame_interval_ms, last_frame_interval_ms);
                if (last_frame_interval_ms >= kLateFrameIntervalMs) {
                    late_frame_intervals += 1;
                }
                frame_intervals_ms.push_back(last_frame_interval_ms);
                frame_intervals_sum_ms += last_frame_interval_ms;
                if (frame_intervals_ms.size() > 90) {
                    frame_intervals_sum_ms -= frame_intervals_ms.front();
                    frame_intervals_ms.pop_front();
                }
            }
            last_receive_ts_ns = ts_ns;

            double frozen_duration_sec = 0.0;
            if (config_.enable_freeze_detection) {
                freeze_probe_index += 1;
                const bool sample_freeze_probe =
                    previous_probe_gray.empty() || (freeze_probe_index % kFreezeProbeEveryN) == 0;
                if (sample_freeze_probe) {
                    cv::Mat current_probe_gray = make_freeze_probe_gray(
                        read_frame,
                        config_.input_pipe_format,
                        config_.width,
                        config_.height);
                    const bool identical_frame = is_effectively_identical_probe(previous_probe_gray, current_probe_gray);
                    last_motion_mean = frame_motion_score(previous_probe_gray, current_probe_gray);
                    previous_probe_gray = current_probe_gray;
                    if (!previous_probe_gray.empty()) {
                        if (identical_frame && last_motion_mean <= kFreezeMotionThreshold) {
                            if (freeze_started_ns <= 0) {
                                freeze_started_ns = ts_ns;
                            }
                        } else {
                            freeze_started_ns = 0;
                        }
                    }
                }
                frozen_duration_sec =
                    (freeze_started_ns > 0) ? static_cast<double>(ts_ns - freeze_started_ns) / 1'000'000'000.0 : 0.0;
            } else {
                freeze_started_ns = 0;
                freeze_probe_index = 0;
                last_motion_mean = 0.0;
                previous_probe_gray.release();
            }

            std::lock_guard<std::mutex> lock(mutex_);
            const auto max_buffered_frames =
                static_cast<std::size_t>(std::max(1, config_.max_buffered_frames));
            if (frames_.size() != max_buffered_frames) {
                frames_.clear();
                frames_.resize(max_buffered_frames);
                frame_start_index_ = 0;
                frame_count_ = 0;
            }

            std::size_t insert_index = 0;
            if (frame_count_ < frames_.size()) {
                insert_index = (frame_start_index_ + frame_count_) % frames_.size();
                frame_count_ += 1;
            } else {
                insert_index = frame_start_index_;
                recycled_frame = std::move(frames_[insert_index].frame);
                frame_start_index_ = (frame_start_index_ + 1) % frames_.size();
                snapshot_.stale_drops += 1;
            }
            BufferedFrame& buffered = frames_[insert_index];
            buffered.frame = std::move(read_frame);
            buffered.seq = snapshot_.latest_seq + 1;
            buffered.timestamp_ns = ts_ns;
            if (can_reuse_frame_storage(recycled_frame, config_.width, config_.height, config_.input_pipe_format)) {
                read_frame = std::move(recycled_frame);
            } else {
                read_frame = allocate_input_frame(config_.width, config_.height, config_.input_pipe_format);
            }
            const auto* latest = latest_buffered_frame_locked();
            const auto* oldest = oldest_buffered_frame_locked();
            snapshot_.has_frame = true;
            snapshot_.latest_seq = (latest != nullptr) ? latest->seq : 0;
            snapshot_.latest_timestamp_ns = (latest != nullptr) ? latest->timestamp_ns : 0;
            snapshot_.oldest_seq = (oldest != nullptr) ? oldest->seq : 0;
            snapshot_.oldest_timestamp_ns = (oldest != nullptr) ? oldest->timestamp_ns : 0;
            snapshot_.buffer_seq_span =
                (latest != nullptr && oldest != nullptr && latest->seq >= oldest->seq)
                    ? (latest->seq - oldest->seq)
                    : 0;
            snapshot_.buffer_span_ms =
                (latest != nullptr && oldest != nullptr && latest->timestamp_ns >= oldest->timestamp_ns)
                    ? static_cast<double>(latest->timestamp_ns - oldest->timestamp_ns) / 1'000'000.0
                    : 0.0;
            snapshot_.buffered_frames = static_cast<std::int64_t>(buffered_frame_count_locked());
            snapshot_.frames_total += 1;
            snapshot_.motion_mean = last_motion_mean;
            snapshot_.frozen_duration_sec = frozen_duration_sec;
            snapshot_.content_frozen = config_.enable_freeze_detection && frozen_duration_sec >= kFreezeRestartSec;
            snapshot_.last_frame_interval_ms = last_frame_interval_ms;
            snapshot_.max_frame_interval_ms = max_frame_interval_ms;
            snapshot_.late_frame_intervals = late_frame_intervals;
            snapshot_.avg_frame_interval_ms =
                frame_intervals_ms.empty() ? 0.0 : (frame_intervals_sum_ms / static_cast<double>(frame_intervals_ms.size()));
            snapshot_.avg_read_ms =
                read_durations_ms.empty() ? 0.0 : (read_durations_sum_ms / static_cast<double>(read_durations_ms.size()));
            snapshot_.max_read_ms = 0.0;
            for (const double value : read_durations_ms) {
                snapshot_.max_read_ms = std::max(snapshot_.max_read_ms, value);
            }
            if (receive_times_ns.size() >= 2) {
                snapshot_.fps = fps_from_count(
                    receive_times_ns.size(),
                    receive_times_ns.back() - receive_times_ns.front());
            }
        }

        pipe.stop();
        if (!running_.load()) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(
            static_cast<int>(std::max(0.2, config_.reconnect_cooldown_sec) * 1000.0)));
    }
}

std::string FfmpegRtspReader::build_command_line() const {
    std::ostringstream command;
    command << quote_arg(ffmpeg_bin_)
            << " -hide_banner -loglevel warning -fflags nobuffer -flags low_delay"
            << " -rtsp_transport " << config_.transport
            << " -timeout " << static_cast<long long>(std::max(1.0, config_.timeout_sec) * 1'000'000.0);

    if (input_runtime_ == "ffmpeg-cuda") {
        command << " -hwaccel cuda";
        if (!config_.video_codec.empty()) {
            command << " -c:v " << config_.video_codec << "_cuvid";
        }
    }

    command << " -i " << quote_arg(config_.url)
            << " -an -vsync 0 -pix_fmt " << config_.input_pipe_format << " -f rawvideo -";
    return command.str();
}

std::int64_t FfmpegRtspReader::now_ns() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        clock::now().time_since_epoch()).count();
}

std::size_t FfmpegRtspReader::buffered_frame_count_locked() const noexcept {
    return frame_count_;
}

const FfmpegRtspReader::BufferedFrame* FfmpegRtspReader::buffered_frame_at_locked(std::size_t logical_index) const noexcept {
    if (logical_index >= frame_count_ || frames_.empty()) {
        return nullptr;
    }
    const std::size_t physical_index = (frame_start_index_ + logical_index) % frames_.size();
    return &frames_[physical_index];
}

const FfmpegRtspReader::BufferedFrame* FfmpegRtspReader::oldest_buffered_frame_locked() const noexcept {
    return buffered_frame_at_locked(0);
}

const FfmpegRtspReader::BufferedFrame* FfmpegRtspReader::latest_buffered_frame_locked() const noexcept {
    if (frame_count_ == 0) {
        return nullptr;
    }
    return buffered_frame_at_locked(frame_count_ - 1);
}

bool FfmpegRtspReader::copy_buffered_frame(
    const BufferedFrame& buffered,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out) const {
    // Share the immutable buffered frame storage instead of deep-copying per selection.
    // cv::Mat keeps the backing storage alive via ref-counting even if the deque advances.
    *frame_out = buffered.frame;
    if (seq_out != nullptr) {
        *seq_out = buffered.seq;
    }
    if (ts_out != nullptr) {
        *ts_out = buffered.timestamp_ns;
    }
    return true;
}

}  // namespace hogak::input
