#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <memory>
#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>
#include <vector>

#include "engine/engine_config.h"
#include "engine/engine_metrics.h"
#include "input/ffmpeg_rtsp_reader.h"

namespace hogak::output {
class OutputWriter;
}

namespace hogak::engine {

class StitchEngine {
public:
    StitchEngine();
    ~StitchEngine();

    bool start(const EngineConfig& config);
    void stop();
    bool reload_config(const EngineConfig& config);
    void reset_calibration();
    EngineConfig current_config() const;
    EngineMetrics snapshot_metrics() const;

    bool running() const noexcept;
    void tick();

private:
    struct SelectedPair {
        cv::Mat left_frame;
        cv::Mat right_frame;
        std::int64_t left_seq = 0;
        std::int64_t right_seq = 0;
        std::int64_t left_ts_ns = 0;   // Legacy alias for left_arrival_ts_ns.
        std::int64_t right_ts_ns = 0;  // Legacy alias for right_arrival_ts_ns.
        std::int64_t left_arrival_ts_ns = 0;
        std::int64_t right_arrival_ts_ns = 0;
        std::int64_t left_source_pts_ns = 0;
        std::int64_t right_source_pts_ns = 0;
        std::int64_t left_source_dts_ns = 0;
        std::int64_t right_source_dts_ns = 0;
        std::int64_t left_source_wallclock_ns = 0;
        std::int64_t right_source_wallclock_ns = 0;
        bool left_source_time_valid = false;
        bool right_source_time_valid = false;
        bool left_source_time_comparable = false;
        bool right_source_time_comparable = false;
        hogak::input::SourceTimeKind left_source_time_kind = hogak::input::SourceTimeKind::kNone;
        hogak::input::SourceTimeKind right_source_time_kind = hogak::input::SourceTimeKind::kNone;
        hogak::input::FrameTimeDomain pair_time_domain = hogak::input::FrameTimeDomain::kArrival;
        std::int64_t pair_time_ns = 0;
        std::int64_t scheduler_pair_time_ns = 0;
        std::int64_t arrival_skew_ns = 0;
        std::int64_t source_skew_ns = 0;
    };

    void clear_calibration_state_locked();
    void update_metrics_locked();
    bool restart_reader_locked(bool left_reader, const char* reason);
    bool ensure_calibration_locked(const cv::Size& left_size, const cv::Size& right_size);
    bool stitch_pair_locked(
        const cv::Mat& left_frame,
        const cv::Mat& right_frame,
        std::int64_t pair_ts_ns,
        const SelectedPair& selected_pair,
        bool left_reused,
        bool right_reused,
        double pair_age_ms,
        const cv::Mat* left_raw_input = nullptr,
        const cv::Mat* right_raw_input = nullptr,
        double output_scale = 1.0);
    bool load_homography_locked(cv::Mat* homography_out);
    bool prepare_output_frame_locked(
        const OutputConfig& output_config,
        const cv::Mat& stitched_cpu,
        const cv::cuda::GpuMat* stitched_gpu,
        cv::Mat* prepared_frame_out,
        const cv::cuda::GpuMat** prepared_gpu_frame_out);
    void annotate_output_debug_overlay_locked(
        cv::Mat* frame,
        const char* label,
        const SelectedPair& selected_pair,
        bool left_reused,
        bool right_reused,
        double pair_age_ms) const;
    void record_wait_paired_fresh_locked(bool left_missing_fresh, bool right_missing_fresh);
    bool select_pair_locked(
        const hogak::input::ReaderSnapshot& left_snapshot,
        const hogak::input::ReaderSnapshot& right_snapshot,
        SelectedPair* pair_out);

    mutable std::mutex mutex_;
    EngineConfig config_{};
    EngineMetrics metrics_{};
    std::atomic<bool> running_{false};
    std::int64_t last_left_seq_ = 0;
    std::int64_t last_right_seq_ = 0;
    std::int64_t last_service_pair_ts_ns_ = 0;
    std::int64_t last_worker_timestamp_ns_ = 0;
    hogak::input::FrameTimeDomain last_pair_time_domain_ = hogak::input::FrameTimeDomain::kArrival;
    std::int64_t last_stitched_count_ = 0;
    std::int64_t last_stitch_timestamp_ns_ = 0;
    std::int64_t last_output_frames_written_ = 0;
    std::int64_t last_output_timestamp_ns_ = 0;
    std::int64_t last_production_output_frames_written_ = 0;
    std::int64_t last_production_output_timestamp_ns_ = 0;
    std::int32_t consecutive_left_reuse_ = 0;
    std::int32_t consecutive_right_reuse_ = 0;
    double wait_paired_fresh_left_age_sum_ms_ = 0.0;
    double wait_paired_fresh_right_age_sum_ms_ = 0.0;
    std::int64_t left_reader_restart_count_ = 0;
    std::int64_t right_reader_restart_count_ = 0;
    std::int64_t last_left_reader_restart_ns_ = 0;
    std::int64_t last_right_reader_restart_ns_ = 0;
    bool calibrated_ = false;
    bool gpu_available_ = false;
    bool gpu_nv12_input_supported_ = true;
    cv::Mat homography_{};
    cv::Mat homography_adjusted_{};
    cv::Mat left_mask_template_{};
    cv::Mat right_mask_template_{};
    cv::Mat overlap_mask_{};
    cv::Mat overlap_mask_roi_{};
    cv::Mat only_left_mask_{};
    cv::Mat only_right_mask_{};
    cv::Mat weight_left_{};
    cv::Mat weight_right_{};
    cv::Mat weight_left_3c_{};
    cv::Mat weight_right_3c_{};
    cv::Mat previous_stitched_probe_gray_{};
    cv::Rect left_roi_{};
    cv::Rect overlap_roi_{};
    cv::Size output_size_{};
    bool full_overlap_ = false;
    cv::Mat latest_stitched_{};
    std::int64_t cached_left_cpu_seq_ = 0;
    std::int64_t cached_right_cpu_seq_ = 0;
    std::int64_t cached_left_gpu_input_seq_ = 0;
    std::int64_t cached_right_gpu_input_seq_ = 0;
    std::int64_t cached_left_canvas_seq_ = 0;
    std::int64_t cached_right_warped_seq_ = 0;
    cv::Mat cached_left_cpu_frame_{};
    cv::Mat cached_right_cpu_frame_{};
    cv::Mat cached_left_canvas_cpu_{};
    cv::Mat cached_right_warped_cpu_{};
    cv::cuda::GpuMat gpu_left_nv12_y_{};
    cv::cuda::GpuMat gpu_left_nv12_uv_{};
    cv::cuda::GpuMat gpu_left_decoded_{};
    cv::cuda::GpuMat gpu_left_input_{};
    cv::cuda::GpuMat gpu_left_canvas_{};
    cv::cuda::GpuMat gpu_stitched_{};
    cv::cuda::GpuMat gpu_right_nv12_y_{};
    cv::cuda::GpuMat gpu_right_nv12_uv_{};
    cv::cuda::GpuMat gpu_right_decoded_{};
    cv::cuda::GpuMat gpu_right_input_{};
    cv::cuda::GpuMat gpu_right_warped_{};
    cv::cuda::GpuMat gpu_overlap_mask_{};
    cv::cuda::GpuMat gpu_overlap_mask_roi_{};
    cv::cuda::GpuMat gpu_only_left_mask_{};
    cv::cuda::GpuMat gpu_only_right_mask_{};
    cv::cuda::GpuMat gpu_weight_left_3c_{};
    cv::cuda::GpuMat gpu_weight_right_3c_{};
    cv::cuda::GpuMat gpu_weight_left_roi_{};
    cv::cuda::GpuMat gpu_weight_right_roi_{};
    cv::cuda::GpuMat gpu_left_f_{};
    cv::cuda::GpuMat gpu_right_f_{};
    cv::cuda::GpuMat gpu_left_part_{};
    cv::cuda::GpuMat gpu_right_part_{};
    cv::cuda::GpuMat gpu_overlap_f_{};
    cv::cuda::GpuMat gpu_overlap_u8_{};
    cv::cuda::GpuMat gpu_output_scaled_{};
    cv::cuda::GpuMat gpu_output_canvas_{};
    std::vector<hogak::input::BufferedFrameInfo> left_buffered_infos_cache_{};
    std::vector<hogak::input::BufferedFrameInfo> right_buffered_infos_cache_{};
    std::unique_ptr<hogak::output::OutputWriter> output_writer_{};
    std::unique_ptr<hogak::output::OutputWriter> production_output_writer_{};
};

}  // namespace hogak::engine
