#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <memory>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>

#include "engine/engine_config.h"
#include "engine/engine_metrics.h"

namespace hogak::output {
class FfmpegOutputWriter;
}

namespace hogak::input {
struct ReaderSnapshot;
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
        std::int64_t left_ts_ns = 0;
        std::int64_t right_ts_ns = 0;
    };

    void clear_calibration_state_locked();
    void update_metrics_locked();
    bool ensure_calibration_locked(const cv::Mat& left_frame, const cv::Mat& right_frame);
    bool stitch_pair_locked(const cv::Mat& left_frame, const cv::Mat& right_frame, std::int64_t pair_ts_ns);
    bool load_homography_locked(cv::Mat* homography_out);
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
    std::int64_t last_worker_timestamp_ns_ = 0;
    std::int64_t last_output_frames_written_ = 0;
    std::int64_t last_output_timestamp_ns_ = 0;
    bool calibrated_ = false;
    bool gpu_available_ = false;
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
    cv::Rect left_roi_{};
    cv::Rect overlap_roi_{};
    cv::Size output_size_{};
    bool full_overlap_ = false;
    cv::Mat latest_stitched_{};
    cv::cuda::GpuMat gpu_left_input_{};
    cv::cuda::GpuMat gpu_left_canvas_{};
    cv::cuda::GpuMat gpu_stitched_{};
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
    std::unique_ptr<hogak::output::FfmpegOutputWriter> output_writer_{};
};

}  // namespace hogak::engine
