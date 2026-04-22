#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"

namespace hogak::input {

enum class SourceTimeKind {
    kNone = 0,
    kStreamPts,
    kWallclock,
};

enum class FrameTimeDomain {
    kArrival = 0,
    kSourceWallclock,
    kSourceComparable,
    kSourcePtsOffset,
};

inline const char* source_time_kind_name(SourceTimeKind kind) noexcept {
    switch (kind) {
        case SourceTimeKind::kStreamPts:
            return "stream_pts";
        case SourceTimeKind::kWallclock:
            return "wallclock";
        case SourceTimeKind::kNone:
        default:
            return "none";
    }
}

inline const char* frame_time_domain_name(FrameTimeDomain domain) noexcept {
    switch (domain) {
        case FrameTimeDomain::kSourceWallclock:
            return "wallclock";
        case FrameTimeDomain::kSourceComparable:
            return "stream_pts";
        case FrameTimeDomain::kSourcePtsOffset:
            return "stream_pts_offset";
        case FrameTimeDomain::kArrival:
        default:
            return "fallback-arrival";
    }
}

struct ReaderSnapshot {
    bool has_frame = false;
    std::int64_t latest_seq = 0;
    std::int64_t latest_timestamp_ns = 0;  // Legacy alias for latest_arrival_timestamp_ns.
    std::int64_t oldest_seq = 0;
    std::int64_t oldest_timestamp_ns = 0;  // Legacy alias for oldest_arrival_timestamp_ns.
    std::int64_t latest_arrival_timestamp_ns = 0;
    std::int64_t oldest_arrival_timestamp_ns = 0;
    std::int64_t latest_source_pts_ns = 0;
    std::int64_t oldest_source_pts_ns = 0;
    std::int64_t latest_source_dts_ns = 0;
    std::int64_t oldest_source_dts_ns = 0;
    std::int64_t latest_source_wallclock_ns = 0;
    std::int64_t oldest_source_wallclock_ns = 0;
    std::int64_t latest_comparable_source_timestamp_ns = 0;
    std::int64_t oldest_comparable_source_timestamp_ns = 0;
    std::int64_t source_valid_frames = 0;
    std::int64_t source_comparable_frames = 0;
    bool latest_source_time_valid = false;
    bool latest_source_time_comparable = false;
    SourceTimeKind latest_source_time_kind = SourceTimeKind::kNone;
    std::int64_t buffer_seq_span = 0;
    double buffer_span_ms = 0.0;
    double source_buffer_span_ms = 0.0;
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
    std::int64_t timestamp_ns = 0;  // Legacy alias for arrival_timestamp_ns.
    std::int64_t arrival_timestamp_ns = 0;
    std::int64_t source_pts_ns = 0;
    std::int64_t source_dts_ns = 0;
    std::int64_t source_wallclock_ns = 0;
    bool source_time_valid = false;
    bool source_time_comparable = false;
    SourceTimeKind source_time_kind = SourceTimeKind::kNone;
    double motion_score = 0.0;
    double luma_mean = 0.0;

    [[nodiscard]] bool has_time(FrameTimeDomain domain) const noexcept {
        switch (domain) {
            case FrameTimeDomain::kSourceWallclock:
                return source_wallclock_ns > 0;
            case FrameTimeDomain::kSourceComparable:
                if (source_wallclock_ns > 0) {
                    return true;
                }
                return source_time_comparable && source_time_valid && source_pts_ns > 0;
            case FrameTimeDomain::kSourcePtsOffset:
                return source_time_valid && source_pts_ns > 0;
            case FrameTimeDomain::kArrival:
            default:
                return arrival_timestamp_ns > 0;
        }
    }

    [[nodiscard]] std::int64_t resolve_time_ns(FrameTimeDomain domain) const noexcept {
        switch (domain) {
            case FrameTimeDomain::kSourceWallclock:
                return source_wallclock_ns;
            case FrameTimeDomain::kSourceComparable:
                if (source_wallclock_ns > 0) {
                    return source_wallclock_ns;
                }
                return (source_time_comparable && source_time_valid) ? source_pts_ns : 0;
            case FrameTimeDomain::kSourcePtsOffset:
                return (source_time_valid && source_pts_ns > 0) ? source_pts_ns : 0;
            case FrameTimeDomain::kArrival:
            default:
                return arrival_timestamp_ns;
        }
    }
};

class FfmpegRtspReader {
public:
    FfmpegRtspReader() = default;
    ~FfmpegRtspReader();

    FfmpegRtspReader(const FfmpegRtspReader&) = delete;
    FfmpegRtspReader& operator=(const FfmpegRtspReader&) = delete;

    bool start(
        const hogak::engine::StreamConfig& config,
        const std::string& ffmpeg_bin,
        const std::string& input_runtime,
        bool require_initial_session = false);
    void stop();
    ReaderSnapshot snapshot() const;
    std::vector<BufferedFrameInfo> buffered_frame_infos() const;
    void buffered_frame_infos(std::vector<BufferedFrameInfo>* infos_out) const;
    bool copy_latest_frame(
        cv::Mat* frame_out,
        std::int64_t* seq_out = nullptr,
        std::int64_t* ts_out = nullptr,
        BufferedFrameInfo* info_out = nullptr) const;
    bool copy_oldest_frame(
        cv::Mat* frame_out,
        std::int64_t* seq_out = nullptr,
        std::int64_t* ts_out = nullptr,
        BufferedFrameInfo* info_out = nullptr) const;
    bool copy_frame_by_seq(
        std::int64_t seq,
        cv::Mat* frame_out,
        std::int64_t* seq_out = nullptr,
        std::int64_t* ts_out = nullptr,
        BufferedFrameInfo* info_out = nullptr) const;
    bool copy_closest_frame(
        std::int64_t target_ts_ns,
        bool prefer_past,
        cv::Mat* frame_out,
        std::int64_t* seq_out = nullptr,
        std::int64_t* ts_out = nullptr,
        FrameTimeDomain time_domain = FrameTimeDomain::kArrival,
        BufferedFrameInfo* info_out = nullptr) const;
    bool running() const noexcept;

private:
    struct BufferedFrame {
        cv::Mat frame;
        std::int64_t seq = 0;
        std::int64_t timestamp_ns = 0;  // Legacy alias for arrival_timestamp_ns.
        std::int64_t arrival_timestamp_ns = 0;
        std::int64_t source_pts_ns = 0;
        std::int64_t source_dts_ns = 0;
        std::int64_t source_wallclock_ns = 0;
        bool source_time_valid = false;
        bool source_time_comparable = false;
        SourceTimeKind source_time_kind = SourceTimeKind::kNone;
        double motion_score = 0.0;
        double luma_mean = 0.0;
    };

    void run();
    static std::int64_t now_ns();
    std::size_t buffered_frame_count_locked() const noexcept;
    const BufferedFrame* buffered_frame_at_locked(std::size_t logical_index) const noexcept;
    const BufferedFrame* oldest_buffered_frame_locked() const noexcept;
    const BufferedFrame* latest_buffered_frame_locked() const noexcept;
    bool copy_buffered_frame(
        const BufferedFrame& buffered,
        cv::Mat* frame_out,
        std::int64_t* seq_out,
        std::int64_t* ts_out,
        BufferedFrameInfo* info_out) const;

    mutable std::mutex mutex_;
    hogak::engine::StreamConfig config_{};
    std::string ffmpeg_bin_;
    std::string input_runtime_ = "ffmpeg-cpu";
    std::thread thread_{};
    std::atomic<bool> running_{false};
    bool require_initial_session_ = false;
    std::condition_variable start_condition_{};
    bool start_attempt_completed_ = false;
    bool start_attempt_succeeded_ = false;
    std::string start_attempt_error_{};
    ReaderSnapshot snapshot_{};
    std::vector<BufferedFrame> frames_{};
    std::size_t frame_start_index_ = 0;
    std::size_t frame_count_ = 0;
};

}  // namespace hogak::input
