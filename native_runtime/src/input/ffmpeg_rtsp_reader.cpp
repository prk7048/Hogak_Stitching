#include "input/ffmpeg_rtsp_reader.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <sstream>
#include <thread>
#include <vector>

#include <opencv2/imgproc.hpp>

#include "platform/win_process_pipe.h"

namespace hogak::input {

namespace {

constexpr std::size_t kMaxBufferedFrames = 8;
constexpr int kFreezeProbeWidth = 64;
constexpr int kFreezeProbeHeight = 36;
constexpr double kFreezeMotionThreshold = 0.01;
constexpr double kFreezeRestartSec = 5.0;

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

cv::Mat make_freeze_probe_gray(const cv::Mat& current_bgr) {
    if (current_bgr.empty()) {
        return {};
    }
    cv::Mat gray;
    cv::cvtColor(current_bgr, gray, cv::COLOR_BGR2GRAY);
    cv::resize(gray, gray, cv::Size(kFreezeProbeWidth, kFreezeProbeHeight), 0.0, 0.0, cv::INTER_AREA);
    return gray;
}

double frame_motion_score(const cv::Mat& previous_probe_gray, const cv::Mat& current_bgr) {
    if (previous_probe_gray.empty() || current_bgr.empty()) {
        return 0.0;
    }
    cv::Mat current_probe_gray = make_freeze_probe_gray(current_bgr);
    if (current_probe_gray.empty()) {
        return 0.0;
    }
    cv::Mat diff;
    cv::absdiff(previous_probe_gray, current_probe_gray, diff);
    return cv::mean(diff)[0];
}

bool is_effectively_identical_frame(const cv::Mat& previous_probe_gray, const cv::Mat& current_bgr) {
    if (previous_probe_gray.empty() || current_bgr.empty()) {
        return false;
    }
    cv::Mat current_probe_gray = make_freeze_probe_gray(current_bgr);
    if (current_probe_gray.empty() || current_probe_gray.size() != previous_probe_gray.size()) {
        return false;
    }
    return cv::countNonZero(current_probe_gray != previous_probe_gray) == 0;
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

bool FfmpegRtspReader::running() const noexcept {
    return running_.load();
}

bool FfmpegRtspReader::copy_latest_frame(cv::Mat* frame_out, std::int64_t* seq_out, std::int64_t* ts_out) const {
    if (frame_out == nullptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!snapshot_.has_frame || frames_.empty()) {
        return false;
    }
    return copy_buffered_frame(frames_.back(), frame_out, seq_out, ts_out);
}

bool FfmpegRtspReader::copy_oldest_frame(cv::Mat* frame_out, std::int64_t* seq_out, std::int64_t* ts_out) const {
    if (frame_out == nullptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!snapshot_.has_frame || frames_.empty()) {
        return false;
    }
    return copy_buffered_frame(frames_.front(), frame_out, seq_out, ts_out);
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
    if (!snapshot_.has_frame || frames_.empty()) {
        return false;
    }

    const BufferedFrame* best = nullptr;
    const BufferedFrame* best_past = nullptr;
    std::int64_t best_delta = 0;
    std::int64_t best_past_delta = 0;

    for (const auto& buffered : frames_) {
        const auto delta = std::llabs(buffered.timestamp_ns - target_ts_ns);
        if (best == nullptr || delta < best_delta || (delta == best_delta && buffered.seq > best->seq)) {
            best = &buffered;
            best_delta = delta;
        }
        if (buffered.timestamp_ns <= target_ts_ns) {
            const auto past_delta = target_ts_ns - buffered.timestamp_ns;
            if (best_past == nullptr || past_delta < best_past_delta || (past_delta == best_past_delta && buffered.seq > best_past->seq)) {
                best_past = &buffered;
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
    const auto frame_bytes = static_cast<std::size_t>(config_.width) *
        static_cast<std::size_t>(config_.height) * 3ULL;
    std::vector<std::uint8_t> frame_buffer(frame_bytes);
    std::vector<std::int64_t> receive_times_ns;
    receive_times_ns.reserve(128);
    cv::Mat previous_probe_gray;
    std::int64_t freeze_started_ns = 0;

    while (running_.load()) {
        platform::WinProcessPipe pipe;
        std::string process_error;
        if (!pipe.start(build_command_line(), process_error)) {
            {
                std::lock_guard<std::mutex> lock(mutex_);
                snapshot_.last_error = process_error;
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
            if (!pipe.read_exact(frame_buffer.data(), frame_bytes, bytes_read) || bytes_read != frame_bytes) {
                std::lock_guard<std::mutex> lock(mutex_);
                if (snapshot_.last_error.empty()) {
                    snapshot_.last_error = "ffmpeg frame read failed";
                }
                break;
            }

            const auto ts_ns = now_ns();
            receive_times_ns.push_back(ts_ns);
            if (receive_times_ns.size() > 90) {
                receive_times_ns.erase(receive_times_ns.begin());
            }

            cv::Mat frame_view(
                config_.height,
                config_.width,
                CV_8UC3,
                frame_buffer.data());
            const bool identical_frame = is_effectively_identical_frame(previous_probe_gray, frame_view);
            const double motion_mean = frame_motion_score(previous_probe_gray, frame_view);
            previous_probe_gray = make_freeze_probe_gray(frame_view);
            if (!previous_probe_gray.empty()) {
                if (identical_frame && motion_mean <= kFreezeMotionThreshold) {
                    if (freeze_started_ns <= 0) {
                        freeze_started_ns = ts_ns;
                    }
                } else {
                    freeze_started_ns = 0;
                }
            }
            const double frozen_duration_sec =
                (freeze_started_ns > 0) ? static_cast<double>(ts_ns - freeze_started_ns) / 1'000'000'000.0 : 0.0;

            std::lock_guard<std::mutex> lock(mutex_);
            if (frames_.size() >= kMaxBufferedFrames) {
                frames_.pop_front();
                snapshot_.stale_drops += 1;
            }
            BufferedFrame buffered;
            frame_view.copyTo(buffered.frame);
            buffered.seq = snapshot_.latest_seq + 1;
            buffered.timestamp_ns = ts_ns;
            frames_.push_back(std::move(buffered));
            snapshot_.has_frame = true;
            snapshot_.latest_seq = frames_.back().seq;
            snapshot_.latest_timestamp_ns = frames_.back().timestamp_ns;
            snapshot_.oldest_seq = frames_.front().seq;
            snapshot_.oldest_timestamp_ns = frames_.front().timestamp_ns;
            snapshot_.buffered_frames = static_cast<std::int64_t>(frames_.size());
            snapshot_.frames_total += 1;
            snapshot_.motion_mean = motion_mean;
            snapshot_.frozen_duration_sec = frozen_duration_sec;
            snapshot_.content_frozen = frozen_duration_sec >= kFreezeRestartSec;
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
            << " -an -vsync 0 -pix_fmt bgr24 -f rawvideo -";
    return command.str();
}

std::int64_t FfmpegRtspReader::now_ns() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        clock::now().time_since_epoch()).count();
}

bool FfmpegRtspReader::copy_buffered_frame(
    const BufferedFrame& buffered,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out) const {
    buffered.frame.copyTo(*frame_out);
    if (seq_out != nullptr) {
        *seq_out = buffered.seq;
    }
    if (ts_out != nullptr) {
        *ts_out = buffered.timestamp_ns;
    }
    return true;
}

}  // namespace hogak::input
