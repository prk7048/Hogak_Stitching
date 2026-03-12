#include "output/ffmpeg_output_writer.h"

#include <algorithm>
#include <chrono>
#include <cmath>
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
    const std::string& codec) {
    std::vector<std::string> filters;
    const bool force_size = config.width > 0 && config.height > 0;
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
    double fps) {
    stop();
    if (config.runtime != "ffmpeg" || config.target.empty() || width <= 0 || height <= 0) {
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        config_ = config;
        ffmpeg_bin_ = resolve_ffmpeg_bin(ffmpeg_bin);
        effective_codec_ = resolve_output_codec(config.codec, width, height);
        muxer_ = config.muxer.empty() ? infer_muxer(config.target) : config.muxer;
        width_ = width;
        height_ = height;
        fps_ = std::max(1.0, fps);
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

void FfmpegOutputWriter::submit(const cv::Mat& frame, std::int64_t /*timestamp_ns*/) {
    if (!running_.load() || frame.empty()) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (frame_pending_) {
        frames_dropped_ += 1;
    }
    if (frame.isContinuous()) {
        frame.copyTo(latest_frame_);
    } else {
        latest_frame_ = frame.clone();
    }
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

    while (running_.load()) {
        cv::Mat frame;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            condition_.wait(lock, [this]() { return !running_.load() || frame_pending_; });
            if (!running_.load()) {
                break;
            }
            latest_frame_.copyTo(frame);
            frame_pending_ = false;
        }

        if (frame.empty()) {
            continue;
        }

        if (!frame.isContinuous()) {
            frame = frame.clone();
        }

        std::string write_error;
        if (!sink.write_all(frame.ptr<std::uint8_t>(), frame.total() * frame.elemSize(), write_error)) {
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
    command
        << quote_arg(ffmpeg_bin_)
        << " -hide_banner -loglevel warning -y"
        << " -flush_packets 1"
        << " -f rawvideo"
        << " -pix_fmt bgr24"
        << " -s " << build_size_text(width_, height_)
        << " -r " << std::max(1.0, fps_)
        << " -i - -an"
        << " -c:v " << effective_codec_;

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

    const auto video_filter = build_video_filter_chain(config_, width_, height_, effective_codec_);
    if (!video_filter.empty()) {
        command << " -vf " << quote_arg(video_filter);
    }

    command << " -pix_fmt yuv420p";

    if (!config_.bitrate.empty()) {
        command << " -b:v " << config_.bitrate
                << " -maxrate " << config_.bitrate
                << " -bufsize " << config_.bitrate;
    }

    if (!muxer_.empty()) {
        command << " -f " << muxer_;
    }

    if (muxer_ == "mpegts") {
        command << " -mpegts_flags resend_headers"
                << " -muxdelay 0"
                << " -muxpreload 0";
    }

    command << " " << quote_arg(config_.target);
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

std::string FfmpegOutputWriter::resolve_output_codec(const std::string& requested_codec, int width, int height) {
    if ((requested_codec == "h264_nvenc") && (width > 4096 || height > 4096)) {
        return "hevc_nvenc";
    }
    return requested_codec;
}

std::string FfmpegOutputWriter::infer_muxer(const std::string& target) {
    const auto text = target;
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
