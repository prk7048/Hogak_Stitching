#include "output/ffmpeg_output_writer.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <sstream>
#include <sstream>
#include <utility>

#include "platform/win_process_sink.h"

namespace hogak::output {

namespace {

std::string build_size_text(int width, int height) {
    std::ostringstream out;
    out << width << "x" << height;
    return out.str();
}

std::string trim_copy(const std::string& text) {
    std::size_t begin = 0;
    std::size_t end = text.size();
    while (begin < end && std::isspace(static_cast<unsigned char>(text[begin])) != 0) {
        ++begin;
    }
    while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1])) != 0) {
        --end;
    }
    return text.substr(begin, end - begin);
}

std::int64_t parse_bitrate_bits(const std::string& text) {
    const auto trimmed = trim_copy(text);
    if (trimmed.empty()) {
        return 0;
    }

    double multiplier = 1.0;
    std::string number_text = trimmed;
    const char suffix = static_cast<char>(std::tolower(static_cast<unsigned char>(trimmed.back())));
    if (suffix == 'k' || suffix == 'm' || suffix == 'g') {
        number_text.pop_back();
        if (suffix == 'k') {
            multiplier = 1'000.0;
        } else if (suffix == 'm') {
            multiplier = 1'000'000.0;
        } else if (suffix == 'g') {
            multiplier = 1'000'000'000.0;
        }
    }

    char* end = nullptr;
    const double value = std::strtod(number_text.c_str(), &end);
    if (end == number_text.c_str() || (end != nullptr && *end != '\0') || !std::isfinite(value) || value <= 0.0) {
        return 0;
    }
    return std::max<std::int64_t>(1, static_cast<std::int64_t>(std::llround(value * multiplier)));
}

std::string format_bitrate_bits(std::int64_t bits) {
    if (bits <= 0) {
        return "";
    }
    if ((bits % 1'000'000) == 0) {
        return std::to_string(bits / 1'000'000) + "M";
    }
    if ((bits % 1'000) == 0) {
        return std::to_string(bits / 1'000) + "k";
    }
    return std::to_string(bits);
}

std::string build_low_latency_bufsize(const std::string& bitrate, double fps) {
    const std::int64_t bitrate_bits = parse_bitrate_bits(bitrate);
    if (bitrate_bits <= 0) {
        return bitrate;
    }
    const double safe_fps = std::max(1.0, fps);
    const auto per_frame_bits =
        std::max<std::int64_t>(1, static_cast<std::int64_t>(std::llround(static_cast<double>(bitrate_bits) / safe_fps)));
    const auto quarter_second_bits = std::max<std::int64_t>(1, bitrate_bits / 4);
    const auto chosen_bits = std::min<std::int64_t>(
        bitrate_bits,
        std::max<std::int64_t>(quarter_second_bits, per_frame_bits * 2));
    return format_bitrate_bits(chosen_bits);
}

bool starts_with(const std::string& text, const char* prefix) {
    return text.rfind(prefix, 0) == 0;
}

bool url_has_query_key(const std::string& url, const std::string& key) {
    const auto query_pos = url.find('?');
    if (query_pos == std::string::npos || key.empty()) {
        return false;
    }

    std::size_t item_begin = query_pos + 1;
    while (item_begin < url.size()) {
        const auto item_end = url.find('&', item_begin);
        const auto token = url.substr(item_begin, item_end == std::string::npos ? std::string::npos : item_end - item_begin);
        const auto equals_pos = token.find('=');
        const auto token_key = token.substr(0, equals_pos);
        if (token_key == key) {
            return true;
        }
        if (item_end == std::string::npos) {
            break;
        }
        item_begin = item_end + 1;
    }
    return false;
}

std::string append_url_query_key(std::string url, const std::string& key, const std::string& value) {
    if (key.empty() || value.empty() || url_has_query_key(url, key)) {
        return url;
    }
    url += (url.find('?') == std::string::npos) ? '?' : '&';
    url += key;
    url += '=';
    url += value;
    return url;
}

std::vector<std::string> split_text(const std::string& text, char delimiter) {
    std::vector<std::string> parts;
    std::size_t begin = 0;
    while (begin <= text.size()) {
        const auto end = text.find(delimiter, begin);
        parts.emplace_back(text.substr(begin, end == std::string::npos ? std::string::npos : end - begin));
        if (end == std::string::npos) {
            break;
        }
        begin = end + 1;
    }
    return parts;
}

std::string join_text(const std::vector<std::string>& parts, char delimiter) {
    std::ostringstream out;
    for (std::size_t index = 0; index < parts.size(); ++index) {
        if (index > 0) {
            out << delimiter;
        }
        out << parts[index];
    }
    return out.str();
}

std::int64_t build_udp_transport_bitrate_bits(const std::string& bitrate) {
    const auto bitrate_bits = parse_bitrate_bits(bitrate);
    if (bitrate_bits <= 0) {
        return 0;
    }
    const auto overhead_bits = std::max<std::int64_t>(500'000, bitrate_bits / 10);
    return bitrate_bits + overhead_bits;
}

std::int64_t build_udp_burst_bits(std::int64_t transport_bitrate_bits, double fps) {
    if (transport_bitrate_bits <= 0) {
        return 0;
    }
    const double safe_fps = std::max(1.0, fps);
    const auto frame_bits = std::max<std::int64_t>(
        1,
        static_cast<std::int64_t>(std::llround(static_cast<double>(transport_bitrate_bits) / safe_fps)));
    return std::max<std::int64_t>(262'144, frame_bits * 4);
}

std::string build_udp_output_target(std::string target, const std::string& bitrate, double fps) {
    if (!starts_with(target, "udp://")) {
        return target;
    }

    std::string result = std::move(target);
    const auto transport_bitrate_bits = build_udp_transport_bitrate_bits(bitrate);
    const auto burst_bits = build_udp_burst_bits(transport_bitrate_bits, fps);
    if (transport_bitrate_bits > 0) {
        result = append_url_query_key(result, "bitrate", std::to_string(transport_bitrate_bits));
    }
    if (burst_bits > 0) {
        result = append_url_query_key(result, "burst_bits", std::to_string(burst_bits));
        const auto buffer_bytes = std::max<std::int64_t>(1 * 1024 * 1024, (burst_bits / 8) * 4);
        result = append_url_query_key(result, "buffer_size", std::to_string(buffer_bytes));
    } else {
        result = append_url_query_key(result, "buffer_size", std::to_string(1 * 1024 * 1024));
    }
    return result;
}

std::string normalize_tee_leg_options(std::string options) {
    if (options.empty()) {
        return options;
    }
    if (options.find("f=mpegts") != std::string::npos) {
        if (options.find("mpegts_flags=") == std::string::npos) {
            options += ":mpegts_flags=resend_headers+pat_pmt_at_frames";
        } else if (options.find("pat_pmt_at_frames") == std::string::npos) {
            const std::string needle = "mpegts_flags=resend_headers";
            const auto pos = options.find(needle);
            if (pos != std::string::npos) {
                options.replace(pos, needle.size(), "mpegts_flags=resend_headers+pat_pmt_at_frames");
            }
        }
    }
    return options;
}

std::string build_tee_output_target(const hogak::engine::OutputConfig& config, double fps) {
    std::vector<std::string> legs = split_text(config.target, '|');
    for (auto& leg : legs) {
        std::string options;
        std::string leg_target = leg;
        if (!leg.empty() && leg.front() == '[') {
            const auto closing = leg.find(']');
            if (closing != std::string::npos) {
                options = leg.substr(1, closing - 1);
                leg_target = leg.substr(closing + 1);
            }
        }

        leg_target = build_udp_output_target(std::move(leg_target), config.bitrate, fps);
        options = normalize_tee_leg_options(std::move(options));
        if (!options.empty()) {
            leg = "[" + options + "]" + leg_target;
        } else {
            leg = leg_target;
        }
    }
    return join_text(legs, '|');
}

std::string build_output_target(const hogak::engine::OutputConfig& config, double fps, const std::string& muxer) {
    if (muxer == "tee" || config.target.find('|') != std::string::npos) {
        return build_tee_output_target(config, fps);
    }
    return build_udp_output_target(config.target, config.bitrate, fps);
}

bool requires_even_dimensions(const std::string& codec) {
    const auto lowered = codec;
    return lowered == "libx264" ||
        lowered == "h264_nvenc" ||
        lowered == "hevc_nvenc" ||
        lowered == "libx265";
}

std::string build_video_filter_chain(
    const hogak::engine::OutputConfig& config,
    int width,
    int height,
    const std::string& codec,
    bool input_prepared) {
    std::vector<std::string> filters;
    const bool force_size = !input_prepared && config.width > 0 && config.height > 0;
    if (force_size) {
        std::ostringstream scale_pad;
        scale_pad << "scale=" << config.width << ':' << config.height
                  << ":force_original_aspect_ratio=decrease"
                  << ",pad=" << config.width << ':' << config.height << ":(ow-iw)/2:(oh-ih)/2";
        filters.push_back(scale_pad.str());
        width = config.width;
        height = config.height;
    }
    if (requires_even_dimensions(codec) && ((width % 2) != 0 || (height % 2) != 0)) {
        filters.push_back("pad=ceil(iw/2)*2:ceil(ih/2)*2");
    }
    if (filters.empty()) {
        return "";
    }
    std::ostringstream out;
    for (std::size_t index = 0; index < filters.size(); ++index) {
        if (index > 0) {
            out << ',';
        }
        out << filters[index];
    }
    return out.str();
}

}  // namespace

FfmpegOutputWriter::~FfmpegOutputWriter() {
    stop();
}

bool FfmpegOutputWriter::start(
    const hogak::engine::OutputConfig& config,
    const std::string& ffmpeg_bin,
    int width,
    int height,
    double fps,
    bool input_prepared) {
    stop();
    if (config.runtime != "ffmpeg" || config.target.empty() || width <= 0 || height <= 0) {
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        config_ = config;
        ffmpeg_bin_ = resolve_ffmpeg_bin(ffmpeg_bin);
        effective_codec_ = resolve_output_codec(config.codec, width, height, config.target, config.profile);
        muxer_ = config.muxer.empty() ? infer_muxer(config.target) : config.muxer;
        width_ = width;
        height_ = height;
        fps_ = std::max(1.0, fps);
        input_prepared_ = input_prepared;
        latest_frame_.release();
        frame_pending_ = false;
        last_error_.clear();
        frames_written_ = 0;
        frames_dropped_ = 0;
        command_line_.clear();
    }

    running_.store(true);
    thread_ = std::thread(&FfmpegOutputWriter::run, this);
    return true;
}

void FfmpegOutputWriter::submit(const OutputFrame& frame, std::int64_t /*timestamp_ns*/) {
    if (!running_.load() || frame.empty()) {
        return;
    }

    cv::Mat cpu_frame;
    if (frame.cpu_frame != nullptr && !frame.cpu_frame->empty()) {
        if (frame.cpu_frame->isContinuous()) {
            frame.cpu_frame->copyTo(cpu_frame);
        } else {
            cpu_frame = frame.cpu_frame->clone();
        }
    } else if (frame.gpu_frame != nullptr && !frame.gpu_frame->empty()) {
        try {
            frame.gpu_frame->download(cpu_frame);
        } catch (const cv::Exception& e) {
            std::lock_guard<std::mutex> lock(mutex_);
            last_error_ = std::string("ffmpeg writer gpu download failed: ") + e.what();
            return;
        }
    }
    if (cpu_frame.empty()) {
        return;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (frame_pending_) {
        frames_dropped_ += 1;
    }
    latest_frame_ = std::move(cpu_frame);
    frame_pending_ = true;
    condition_.notify_one();
}

void FfmpegOutputWriter::stop() {
    running_.store(false);
    condition_.notify_all();
    if (thread_.joinable()) {
        thread_.join();
    }
}

bool FfmpegOutputWriter::active() const noexcept {
    return running_.load();
}

std::int64_t FfmpegOutputWriter::frames_written() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return frames_written_;
}

std::int64_t FfmpegOutputWriter::frames_dropped() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return frames_dropped_;
}

std::string FfmpegOutputWriter::last_error() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return last_error_;
}

std::string FfmpegOutputWriter::effective_codec() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return effective_codec_;
}

std::string FfmpegOutputWriter::command_line() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return command_line_;
}

std::string FfmpegOutputWriter::muxer() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return muxer_;
}

void FfmpegOutputWriter::run() {
    hogak::platform::WinProcessSink sink;
    const auto command_line = build_command_line();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        command_line_ = command_line;
    }
    std::string error_message;
    if (!sink.start(command_line, error_message)) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::ostringstream message;
        message << error_message
                << " codec=" << effective_codec_
                << " muxer=" << (muxer_.empty() ? "auto" : muxer_)
                << " size=" << width_ << 'x' << height_
                << " fps=" << fps_
                << " target=" << config_.target;
        last_error_ = message.str();
        running_.store(false);
        return;
    }

    cv::Mat current_frame;
    bool has_current_frame = false;
    bool wrote_first_frame = false;
    const auto frame_period = std::chrono::duration_cast<std::chrono::steady_clock::duration>(
        std::chrono::duration<double>(1.0 / std::max(1.0, fps_)));
    auto next_write_time = std::chrono::steady_clock::now();

    while (running_.load()) {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (!has_current_frame) {
                condition_.wait(lock, [this]() { return !running_.load() || frame_pending_; });
            } else {
                condition_.wait_until(lock, next_write_time, [this]() { return !running_.load() || frame_pending_; });
            }
            if (!running_.load()) {
                break;
            }
            if (frame_pending_) {
                std::swap(current_frame, latest_frame_);
                frame_pending_ = false;
                has_current_frame = !current_frame.empty();
            }
        }

        if (!has_current_frame) {
            continue;
        }

        const auto now = std::chrono::steady_clock::now();
        if (wrote_first_frame && now < next_write_time) {
            continue;
        }

        const cv::Mat* frame_to_write = &current_frame;
        cv::Mat contiguous_frame;
        if (!current_frame.isContinuous()) {
            contiguous_frame = current_frame.clone();
            if (contiguous_frame.empty()) {
                continue;
            }
            frame_to_write = &contiguous_frame;
        }

        std::string write_error;
        if (!sink.write_all(
                frame_to_write->ptr<std::uint8_t>(),
                frame_to_write->total() * frame_to_write->elemSize(),
                write_error)) {
            std::lock_guard<std::mutex> lock(mutex_);
            std::ostringstream message;
            message << write_error
                    << " codec=" << effective_codec_
                    << " muxer=" << (muxer_.empty() ? "auto" : muxer_)
                    << " size=" << width_ << 'x' << height_
                    << " fps=" << fps_
                    << " target=" << config_.target;
            last_error_ = message.str();
            running_.store(false);
            break;
        }

        std::lock_guard<std::mutex> lock(mutex_);
        frames_written_ += 1;
        wrote_first_frame = true;
        next_write_time = now + frame_period;
    }

    sink.stop();
    if (frames_written() <= 0) {
        const auto stderr_text = sink.stderr_tail();
        const auto exit_code = sink.exit_code();
        if (!stderr_text.empty() || exit_code != 0) {
            std::lock_guard<std::mutex> lock(mutex_);
            if (last_error_.empty()) {
                std::ostringstream message;
                message << "ffmpeg writer exited"
                        << " exit_code=" << exit_code
                        << " codec=" << effective_codec_
                        << " muxer=" << (muxer_.empty() ? "auto" : muxer_)
                        << " size=" << width_ << 'x' << height_
                        << " target=" << config_.target;
                if (!stderr_text.empty()) {
                    message << " stderr=" << stderr_text;
                }
                last_error_ = message.str();
            }
        }
    }
}

std::string FfmpegOutputWriter::build_command_line() const {
    std::ostringstream command;
    const auto output_target = build_output_target(config_, fps_, muxer_);
    const auto transport_bitrate_bits = build_udp_transport_bitrate_bits(config_.bitrate);
    command
        << quote_arg(ffmpeg_bin_)
        << " -hide_banner -loglevel warning -y"
        << " -flush_packets 1"
        << " -fflags +genpts"
        << " -f rawvideo"
        << " -pix_fmt bgr24"
        << " -s " << build_size_text(width_, height_)
        << " -framerate " << std::max(1.0, fps_)
        << " -i - -an"
        << " -c:v " << effective_codec_;

    if (muxer_ == "tee") {
        command << " -map 0:v:0";
    }

    if (effective_codec_.find("_nvenc") != std::string::npos) {
        command << " -preset " << config_.preset
                << " -tune ll"
                << " -rc cbr"
                << " -zerolatency 1"
                << " -bf 0"
                << " -forced-idr 1"
                << " -g " << std::max(1, static_cast<int>(std::round(fps_)))
                << " -keyint_min " << std::max(1, static_cast<int>(std::round(fps_)));
    }

    if (effective_codec_ == "libx264") {
        command << " -tune zerolatency"
                << " -bf 0"
                << " -g " << std::max(1, static_cast<int>(std::round(fps_)))
                << " -keyint_min " << std::max(1, static_cast<int>(std::round(fps_)))
                << " -sc_threshold 0";
    }

    const auto video_filter = build_video_filter_chain(config_, width_, height_, effective_codec_, input_prepared_);
    if (!video_filter.empty()) {
        command << " -vf " << quote_arg(video_filter);
    }

    command << " -pix_fmt yuv420p";

    if (!config_.bitrate.empty()) {
        const auto bufsize = build_low_latency_bufsize(config_.bitrate, fps_);
        command << " -b:v " << config_.bitrate
                << " -maxrate " << config_.bitrate
                << " -bufsize " << bufsize;
    }

    if (!muxer_.empty()) {
        command << " -f " << muxer_;
    }

    if (muxer_ == "mpegts") {
        command << " -mpegts_flags resend_headers+pat_pmt_at_frames"
                << " -muxdelay 0"
                << " -muxpreload 0";
        if (transport_bitrate_bits > 0) {
            command << " -muxrate " << transport_bitrate_bits;
        }
    }

    command << " " << quote_arg(output_target);
    return command.str();
}

std::string FfmpegOutputWriter::resolve_ffmpeg_bin(const std::string& explicit_path) {
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

std::string FfmpegOutputWriter::resolve_output_codec(
    const std::string& requested_codec,
    int width,
    int height,
    const std::string& /*target*/,
    const std::string& /*profile*/) {
    if ((requested_codec == "h264_nvenc") && (width > 4096 || height > 4096)) {
        return "hevc_nvenc";
    }
    return requested_codec;
}

std::string FfmpegOutputWriter::infer_muxer(const std::string& target) {
    const auto text = target;
    if (text.find('|') != std::string::npos) {
        return "tee";
    }
    if (text.rfind("rtsp://", 0) == 0) {
        return "rtsp";
    }
    if (text.rfind("rtmp://", 0) == 0) {
        return "flv";
    }
    if (text.rfind("srt://", 0) == 0) {
        return "mpegts";
    }
    if (text.rfind("udp://", 0) == 0) {
        return "mpegts";
    }
    return "";
}

std::string FfmpegOutputWriter::quote_arg(const std::string& text) {
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

}  // namespace hogak::output
