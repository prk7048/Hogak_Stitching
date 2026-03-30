#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace hogak::engine {

struct LensCorrectionConfig {
    bool enabled = false;
    std::string source = "off";
    std::string model = "opencv_pinhole";
    std::string left_profile_file;
    std::string right_profile_file;
    std::string left_source_hint = "off";
    std::string right_source_hint = "off";
    std::string camera_model = "DH-IPC-HFW4841T-ZAS";
    double horizontal_fov_deg = 0.0;
    double vertical_fov_deg = 0.0;
};

struct ProjectionConfig {
    std::string model = "cylindrical";
    double focal_px = 0.0;
    double center_x = 0.0;
    double center_y = 0.0;
    int32_t input_width = 0;
    int32_t input_height = 0;
    int32_t output_width = 0;
    int32_t output_height = 0;
};

struct AlignmentConfig {
    std::string model = "affine";
    std::array<double, 9> matrix = {1.0, 0.0, 0.0,
                                    0.0, 1.0, 0.0,
                                    0.0, 0.0, 1.0};
    bool has_matrix = false;
};

struct SeamConfig {
    std::string mode = "dynamic-path";
    int32_t transition_px = 64;
    double smoothness_penalty = 4.0;
    double temporal_penalty = 2.0;
};

struct ExposureConfig {
    bool enabled = true;
    double gain_min = 0.7;
    double gain_max = 1.4;
    double bias_abs_max = 35.0;
};

struct GeometryRuntimeConfig {
    std::string mode = "planar-homography";
    std::string artifact_file;
    LensCorrectionConfig lens_correction;
    ProjectionConfig projection;
    AlignmentConfig alignment;
    SeamConfig seam;
    ExposureConfig exposure;
};

struct StreamConfig {
    std::string name;
    std::string url;
    std::string transport = "tcp";
    double timeout_sec = 10.0;
    double reconnect_cooldown_sec = 1.0;
    std::string video_codec = "h264";
    std::string input_pipe_format = "nv12";
    int32_t width = 1920;
    int32_t height = 1080;
    int32_t max_buffered_frames = 8;
    bool enable_freeze_detection = true;
};

struct OutputConfig {
    std::string runtime = "none";
    std::string profile = "inspection";
    std::string target;
    std::string codec = "h264_nvenc";
    std::string bitrate = "12M";
    std::string preset = "p4";
    std::string muxer;
    int32_t width = 0;
    int32_t height = 0;
    double fps = 30.0;
    bool debug_overlay = false;
};

struct EngineConfig {
    StreamConfig left;
    StreamConfig right;
    OutputConfig output;
    OutputConfig production_output;
    GeometryRuntimeConfig geometry;
    std::string ffmpeg_bin;
    std::string homography_file;
    std::string distortion_mode = "off";
    bool use_saved_distortion = false;
    bool distortion_auto_save = false;
    std::string left_distortion_file = "data/runtime_distortion_left.json";
    std::string right_distortion_file = "data/runtime_distortion_right.json";
    std::string left_distortion_source_hint = "off";
    std::string right_distortion_source_hint = "off";
    std::string distortion_lens_model_hint = "pinhole";
    double distortion_horizontal_fov_deg = 0.0;
    double distortion_vertical_fov_deg = 0.0;
    std::string distortion_camera_model = "DH-IPC-HFW4841T-ZAS";
    std::string input_runtime = "ffmpeg-cuda";
    std::string sync_pair_mode = "none";
    bool allow_frame_reuse = false;
    double sync_match_max_delta_ms = 35.0;
    std::string sync_time_source = "pts-offset-auto";
    double sync_manual_offset_ms = 0.0;
    double sync_auto_offset_window_sec = 4.0;
    double sync_auto_offset_max_search_ms = 500.0;
    double sync_recalibration_interval_sec = 60.0;
    double sync_recalibration_trigger_skew_ms = 45.0;
    double sync_recalibration_trigger_wait_ratio = 0.50;
    double sync_auto_offset_confidence_min = 0.85;
    double pair_reuse_max_age_ms = 90.0;
    int32_t pair_reuse_max_consecutive = 2;
    double process_scale = 1.0;
    double stitch_output_scale = 1.0;
    int32_t stitch_every_n = 1;
    int32_t min_matches = 20;
    int32_t min_inliers = 8;
    double ratio_test = 0.82;
    double ransac_thresh = 6.0;
    int32_t max_features = 2800;
    int32_t manual_points = 4;
    std::string gpu_mode = "on";
    int32_t gpu_device = 0;
    int32_t cpu_threads = 0;
    bool headless_benchmark = false;
    double benchmark_log_interval_sec = 1.0;
};

}  // namespace hogak::engine
