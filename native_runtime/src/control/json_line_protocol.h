#pragma once

#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>

#include "engine/engine_metrics.h"

namespace hogak::control {

inline std::string json_escape(const std::string& input) {
    std::ostringstream out;
    for (const char ch : input) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                out << ch;
                break;
        }
    }
    return out.str();
}

inline std::string hello_event_json(double timestamp_sec) {
    std::ostringstream out;
    out << "{\"seq\":0,\"type\":\"hello\",\"timestamp_sec\":" << std::fixed << std::setprecision(3)
        << timestamp_sec
        << ",\"payload\":{\"runtime\":\"stitch_runtime\",\"protocol\":\"jsonl-v1\",\"mode\":\"native-runtime\"}}";
    return out.str();
}

inline std::string metrics_event_json(std::int64_t seq, double timestamp_sec, const hogak::engine::EngineMetrics& metrics) {
    std::ostringstream out;
    out << "{\"seq\":" << seq
        << ",\"type\":\"metrics\",\"timestamp_sec\":" << std::fixed << std::setprecision(3) << timestamp_sec
        << ",\"payload\":{"
        << "\"status\":\"" << json_escape(metrics.status) << "\","
        << "\"frame_index\":" << metrics.frame_index << ','
        << "\"left_fps\":" << std::fixed << std::setprecision(2) << metrics.left_fps << ','
        << "\"right_fps\":" << metrics.right_fps << ','
        << "\"left_age_ms\":" << metrics.left_age_ms << ','
        << "\"right_age_ms\":" << metrics.right_age_ms << ','
        << "\"left_motion_mean\":" << metrics.left_motion_mean << ','
        << "\"right_motion_mean\":" << metrics.right_motion_mean << ','
        << "\"stitched_motion_mean\":" << metrics.stitched_motion_mean << ','
        << "\"stitch_fps\":" << metrics.stitch_fps << ','
        << "\"worker_fps\":" << metrics.worker_fps << ','
        << "\"output_written_fps\":" << metrics.output_written_fps << ','
        << "\"production_output_written_fps\":" << metrics.production_output_written_fps << ','
        << "\"pair_skew_ms_mean\":" << metrics.pair_skew_ms_mean << ','
        << "\"gpu_enabled\":" << (metrics.gpu_enabled ? "true" : "false") << ','
        << "\"gpu_reason\":\"" << json_escape(metrics.gpu_reason) << "\","
        << "\"gpu_errors\":" << metrics.gpu_errors << ','
        << "\"calibrated\":" << (metrics.calibrated ? "true" : "false") << ','
        << "\"output_width\":" << metrics.output_width << ','
        << "\"output_height\":" << metrics.output_height << ','
        << "\"production_output_width\":" << metrics.production_output_width << ','
        << "\"production_output_height\":" << metrics.production_output_height << ','
        << "\"stitched_count\":" << metrics.stitched_count << ','
        << "\"left_frames_total\":" << metrics.left_frames_total << ','
        << "\"right_frames_total\":" << metrics.right_frames_total << ','
        << "\"left_buffered_frames\":" << metrics.left_buffered_frames << ','
        << "\"right_buffered_frames\":" << metrics.right_buffered_frames << ','
        << "\"left_stale_drops\":" << metrics.left_stale_drops << ','
        << "\"right_stale_drops\":" << metrics.right_stale_drops << ','
        << "\"left_content_frozen\":" << (metrics.left_content_frozen ? "true" : "false") << ','
        << "\"right_content_frozen\":" << (metrics.right_content_frozen ? "true" : "false") << ','
        << "\"left_frozen_duration_sec\":" << metrics.left_frozen_duration_sec << ','
        << "\"right_frozen_duration_sec\":" << metrics.right_frozen_duration_sec << ','
        << "\"left_freeze_restarts\":" << metrics.left_freeze_restarts << ','
        << "\"right_freeze_restarts\":" << metrics.right_freeze_restarts << ','
        << "\"output_active\":" << (metrics.output_active ? "true" : "false") << ','
        << "\"output_frames_written\":" << metrics.output_frames_written << ','
        << "\"output_frames_dropped\":" << metrics.output_frames_dropped << ','
        << "\"output_target\":\"" << json_escape(metrics.output_target) << "\","
        << "\"output_effective_codec\":\"" << json_escape(metrics.output_effective_codec) << "\","
        << "\"output_last_error\":\"" << json_escape(metrics.output_last_error) << "\","
        << "\"production_output_active\":" << (metrics.production_output_active ? "true" : "false") << ','
        << "\"production_output_frames_written\":" << metrics.production_output_frames_written << ','
        << "\"production_output_frames_dropped\":" << metrics.production_output_frames_dropped << ','
        << "\"production_output_target\":\"" << json_escape(metrics.production_output_target) << "\","
        << "\"production_output_effective_codec\":\"" << json_escape(metrics.production_output_effective_codec) << "\","
        << "\"production_output_last_error\":\"" << json_escape(metrics.production_output_last_error) << "\","
        << "\"left_last_error\":\"" << json_escape(metrics.left_last_error) << "\","
        << "\"right_last_error\":\"" << json_escape(metrics.right_last_error) << "\","
        << "\"gpu_warp_count\":" << metrics.gpu_warp_count << ','
        << "\"cpu_warp_count\":" << metrics.cpu_warp_count << ','
        << "\"gpu_blend_count\":" << metrics.gpu_blend_count << ','
        << "\"cpu_blend_count\":" << metrics.cpu_blend_count << ','
        << "\"blend_mode\":\"" << json_escape(metrics.blend_mode) << "\","
        << "\"overlap_diff_mean\":" << metrics.overlap_diff_mean << ','
        << "\"stitched_mean_luma\":" << metrics.stitched_mean_luma << ','
        << "\"left_mean_luma\":" << metrics.left_mean_luma << ','
        << "\"right_mean_luma\":" << metrics.right_mean_luma << ','
        << "\"warped_mean_luma\":" << metrics.warped_mean_luma << ','
        << "\"only_left_pixels\":" << metrics.only_left_pixels << ','
        << "\"only_right_pixels\":" << metrics.only_right_pixels << ','
        << "\"overlap_pixels\":" << metrics.overlap_pixels
        << "}}";
    return out.str();
}

inline bool command_type_is(const std::string& line, const char* command_type) {
    const std::string needle1 = "\"type\":\"" + std::string(command_type) + "\"";
    const std::string needle2 = "\"type\": \"" + std::string(command_type) + "\"";
    return line.find(needle1) != std::string::npos || line.find(needle2) != std::string::npos;
}

}  // namespace hogak::control
