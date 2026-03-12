#pragma once

#include <atomic>
#include <cstdint>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"

namespace hogak::output {

class FfmpegOutputWriter {
public:
    FfmpegOutputWriter() = default;
    ~FfmpegOutputWriter();

    FfmpegOutputWriter(const FfmpegOutputWriter&) = delete;
    FfmpegOutputWriter& operator=(const FfmpegOutputWriter&) = delete;

    bool start(
        const hogak::engine::OutputConfig& config,
        const std::string& ffmpeg_bin,
        int width,
        int height,
        double fps);
    void submit(const cv::Mat& frame, std::int64_t timestamp_ns);
    void stop();

    bool active() const noexcept;
    std::int64_t frames_written() const noexcept;
    std::int64_t frames_dropped() const noexcept;
    std::string last_error() const;
    std::string effective_codec() const;
    std::string command_line() const;
    std::string muxer() const;

private:
    void run();
    std::string build_command_line() const;
    static std::string resolve_ffmpeg_bin(const std::string& explicit_path);
    static std::string resolve_output_codec(const std::string& requested_codec, int width, int height);
    static std::string infer_muxer(const std::string& target);
    static std::string quote_arg(const std::string& text);

    mutable std::mutex mutex_;
    std::condition_variable condition_{};
    std::thread thread_{};
    std::atomic<bool> running_{false};
    hogak::engine::OutputConfig config_{};
    std::string ffmpeg_bin_{};
    std::string effective_codec_{};
    std::string muxer_{};
    std::string command_line_{};
    int width_ = 0;
    int height_ = 0;
    double fps_ = 30.0;
    cv::Mat latest_frame_{};
    bool frame_pending_ = false;
    std::string last_error_{};
    std::int64_t frames_written_ = 0;
    std::int64_t frames_dropped_ = 0;
};

}  // namespace hogak::output
