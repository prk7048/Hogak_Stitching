#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>

#include "engine/engine_config.h"
#include "engine/engine_metrics.h"

namespace hogak::engine {

class StitchEngine {
public:
    StitchEngine();
    ~StitchEngine();

    bool start(const EngineConfig& config);
    void stop();
    bool reload_config(const EngineConfig& config);
    EngineMetrics snapshot_metrics() const;

    bool running() const noexcept;
    void tick();

private:
    void update_metrics_locked();
    bool ensure_calibration_locked(const cv::Mat& left_frame, const cv::Mat& right_frame);
    bool stitch_pair_locked(const cv::Mat& left_frame, const cv::Mat& right_frame, std::int64_t pair_ts_ns);
    bool load_homography_locked(cv::Mat* homography_out);

    mutable std::mutex mutex_;
    EngineConfig config_{};
    EngineMetrics metrics_{};
    std::atomic<bool> running_{false};
    std::int64_t last_left_seq_ = 0;
    std::int64_t last_right_seq_ = 0;
    std::int64_t last_worker_timestamp_ns_ = 0;
    bool calibrated_ = false;
    bool gpu_available_ = false;
    cv::Mat homography_{};
    cv::Mat homography_adjusted_{};
    cv::Mat left_mask_template_{};
    cv::Mat right_mask_template_{};
    cv::Mat overlap_mask_{};
    cv::Mat only_left_mask_{};
    cv::Mat only_right_mask_{};
    cv::Mat weight_left_{};
    cv::Mat weight_right_{};
    cv::Mat weight_left_3c_{};
    cv::Mat weight_right_3c_{};
    cv::Rect left_roi_{};
    cv::Size output_size_{};
    cv::Mat latest_stitched_{};
    cv::cuda::GpuMat gpu_right_input_{};
    cv::cuda::GpuMat gpu_right_warped_{};
};

}  // namespace hogak::engine
