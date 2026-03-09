#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"

namespace hogak::input {

struct ReaderSnapshot {
    bool has_frame = false;
    std::int64_t latest_seq = 0;
    std::int64_t latest_timestamp_ns = 0;
    double fps = 0.0;
    std::int64_t frames_total = 0;
    std::int64_t stale_drops = 0;
    std::string last_error;
};

class FfmpegRtspReader {
public:
    FfmpegRtspReader() = default;
    ~FfmpegRtspReader();

    FfmpegRtspReader(const FfmpegRtspReader&) = delete;
    FfmpegRtspReader& operator=(const FfmpegRtspReader&) = delete;

    bool start(const hogak::engine::StreamConfig& config, const std::string& ffmpeg_bin, const std::string& input_runtime);
    void stop();
    ReaderSnapshot snapshot() const;
    bool copy_latest_frame(cv::Mat* frame_out, std::int64_t* seq_out = nullptr, std::int64_t* ts_out = nullptr) const;
    bool running() const noexcept;

private:
    void run();
    std::string build_command_line() const;
    static std::int64_t now_ns();

    mutable std::mutex mutex_;
    hogak::engine::StreamConfig config_{};
    std::string ffmpeg_bin_;
    std::string input_runtime_ = "ffmpeg-cpu";
    std::thread thread_{};
    std::atomic<bool> running_{false};
    ReaderSnapshot snapshot_{};
    cv::Mat latest_frame_{};
};

}  // namespace hogak::input
