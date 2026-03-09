#pragma once

#include <cstdint>
#include <string>

namespace hogak::engine {

struct StreamConfig {
    std::string name;
    std::string url;
    std::string transport = "tcp";
    double timeout_sec = 10.0;
    double reconnect_cooldown_sec = 1.0;
    std::string video_codec = "h264";
    int32_t width = 1920;
    int32_t height = 1080;
};

struct OutputConfig {
    std::string runtime = "none";
    std::string target;
    std::string codec = "h264_nvenc";
    std::string bitrate = "12M";
    std::string preset = "p4";
    std::string muxer;
};

struct EngineConfig {
    StreamConfig left;
    StreamConfig right;
    OutputConfig output;
    std::string ffmpeg_bin;
    std::string homography_file;
    std::string input_runtime = "ffmpeg-cuda";
    std::string sync_pair_mode = "none";
    double sync_match_max_delta_ms = 35.0;
    double sync_manual_offset_ms = 0.0;
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
