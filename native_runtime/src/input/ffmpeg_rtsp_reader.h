#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"

namespace hogak::input {

struct ReaderSnapshot {
    bool has_frame = false;
    std::int64_t latest_seq = 0;
    std::int64_t latest_timestamp_ns = 0;
    std::int64_t oldest_seq = 0;
    std::int64_t oldest_timestamp_ns = 0;
    std::int64_t buffer_seq_span = 0;
    double buffer_span_ms = 0.0;
    double fps = 0.0;
    double avg_frame_interval_ms = 0.0;
    double last_frame_interval_ms = 0.0;
    double max_frame_interval_ms = 0.0;
    std::int64_t late_frame_intervals = 0;
    double avg_read_ms = 0.0;
    double max_read_ms = 0.0;
    std::int64_t frames_total = 0;
    std::int64_t stale_drops = 0;
    std::int64_t launch_failures = 0;
    std::int64_t read_failures = 0;
    std::int64_t buffered_frames = 0;
    double motion_mean = 0.0;
    double frozen_duration_sec = 0.0;
    bool content_frozen = false;
    std::int64_t freeze_restarts = 0;
    std::string last_error;
};

struct BufferedFrameInfo {
    cv::Mat frame;
    std::int64_t seq = 0;
    std::int64_t timestamp_ns = 0;
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
    std::vector<BufferedFrameInfo> buffered_frame_infos() const;
    void buffered_frame_infos(std::vector<BufferedFrameInfo>* infos_out) const;
    bool copy_latest_frame(cv::Mat* frame_out, std::int64_t* seq_out = nullptr, std::int64_t* ts_out = nullptr) const;
    bool copy_oldest_frame(cv::Mat* frame_out, std::int64_t* seq_out = nullptr, std::int64_t* ts_out = nullptr) const;
    bool copy_frame_by_seq(
        std::int64_t seq,
        cv::Mat* frame_out,
        std::int64_t* seq_out = nullptr,
        std::int64_t* ts_out = nullptr) const;
    bool copy_closest_frame(
        std::int64_t target_ts_ns,
        bool prefer_past,
        cv::Mat* frame_out,
        std::int64_t* seq_out = nullptr,
        std::int64_t* ts_out = nullptr) const;
    bool running() const noexcept;

private:
    struct BufferedFrame {
        cv::Mat frame;
        std::int64_t seq = 0;
        std::int64_t timestamp_ns = 0;
    };

    void run();
    std::string build_command_line() const;
    static std::int64_t now_ns();
    std::size_t buffered_frame_count_locked() const noexcept;
    const BufferedFrame* buffered_frame_at_locked(std::size_t logical_index) const noexcept;
    const BufferedFrame* oldest_buffered_frame_locked() const noexcept;
    const BufferedFrame* latest_buffered_frame_locked() const noexcept;
    bool copy_buffered_frame(
        const BufferedFrame& buffered,
        cv::Mat* frame_out,
        std::int64_t* seq_out,
        std::int64_t* ts_out) const;

    mutable std::mutex mutex_;
    hogak::engine::StreamConfig config_{};
    std::string ffmpeg_bin_;
    std::string input_runtime_ = "ffmpeg-cpu";
    std::thread thread_{};
    std::atomic<bool> running_{false};
    ReaderSnapshot snapshot_{};
    std::vector<BufferedFrame> frames_{};
    std::size_t frame_start_index_ = 0;
    std::size_t frame_count_ = 0;
};

}  // namespace hogak::input
