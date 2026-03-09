#pragma once

#include <cstdint>
#include <string>

namespace hogak::engine {

struct EngineMetrics {
    std::string status = "idle";
    int64_t frame_index = 0;
    double left_fps = 0.0;
    double right_fps = 0.0;
    double stitch_fps = 0.0;
    double worker_fps = 0.0;
    double pair_skew_ms_mean = 0.0;
    int32_t matches = 0;
    int32_t inliers = 0;
    int64_t stitched_count = 0;
    int64_t reused_count = 0;
    bool gpu_enabled = false;
    std::string gpu_reason = "-";
    bool gpu_feature_enabled = false;
    std::string gpu_feature_reason = "-";
    int64_t gpu_warp_count = 0;
    int64_t cpu_warp_count = 0;
    int64_t gpu_match_count = 0;
    int64_t cpu_match_count = 0;
    int64_t gpu_blend_count = 0;
    int64_t cpu_blend_count = 0;
    int64_t gpu_errors = 0;
    int64_t gpu_feature_errors = 0;
    std::string blend_mode = "-";
    double overlap_diff_mean = 0.0;
    bool manual_mode = false;
    int32_t manual_left = 0;
    int32_t manual_right = 0;
    int32_t manual_target = 0;
    int64_t left_frames_total = 0;
    int64_t right_frames_total = 0;
    int64_t left_stale_drops = 0;
    int64_t right_stale_drops = 0;
    bool calibrated = false;
    int32_t output_width = 0;
    int32_t output_height = 0;
    std::string left_last_error;
    std::string right_last_error;
};

}  // namespace hogak::engine
