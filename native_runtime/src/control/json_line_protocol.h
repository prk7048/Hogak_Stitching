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
        << "\"sync_pair_mode\":\"" << json_escape(metrics.sync_pair_mode) << "\","
        << "\"frame_index\":" << metrics.frame_index << ','
        << "\"left_fps\":" << std::fixed << std::setprecision(2) << metrics.left_fps << ','
        << "\"right_fps\":" << metrics.right_fps << ','
        << "\"left_avg_frame_interval_ms\":" << metrics.left_avg_frame_interval_ms << ','
        << "\"right_avg_frame_interval_ms\":" << metrics.right_avg_frame_interval_ms << ','
        << "\"left_last_frame_interval_ms\":" << metrics.left_last_frame_interval_ms << ','
        << "\"right_last_frame_interval_ms\":" << metrics.right_last_frame_interval_ms << ','
        << "\"left_max_frame_interval_ms\":" << metrics.left_max_frame_interval_ms << ','
        << "\"right_max_frame_interval_ms\":" << metrics.right_max_frame_interval_ms << ','
        << "\"left_late_frame_intervals\":" << metrics.left_late_frame_intervals << ','
        << "\"right_late_frame_intervals\":" << metrics.right_late_frame_intervals << ','
        << "\"left_buffer_span_ms\":" << metrics.left_buffer_span_ms << ','
        << "\"right_buffer_span_ms\":" << metrics.right_buffer_span_ms << ','
        << "\"left_avg_read_ms\":" << metrics.left_avg_read_ms << ','
        << "\"right_avg_read_ms\":" << metrics.right_avg_read_ms << ','
        << "\"left_max_read_ms\":" << metrics.left_max_read_ms << ','
        << "\"right_max_read_ms\":" << metrics.right_max_read_ms << ','
        << "\"left_buffer_seq_span\":" << metrics.left_buffer_seq_span << ','
        << "\"right_buffer_seq_span\":" << metrics.right_buffer_seq_span << ','
        << "\"left_age_ms\":" << metrics.left_age_ms << ','
        << "\"right_age_ms\":" << metrics.right_age_ms << ','
        << "\"left_source_age_ms\":" << metrics.left_source_age_ms << ','
        << "\"right_source_age_ms\":" << metrics.right_source_age_ms << ','
        << "\"selected_left_lag_ms\":" << metrics.selected_left_lag_ms << ','
        << "\"selected_right_lag_ms\":" << metrics.selected_right_lag_ms << ','
        << "\"selected_left_lag_frames\":" << metrics.selected_left_lag_frames << ','
        << "\"selected_right_lag_frames\":" << metrics.selected_right_lag_frames << ','
        << "\"left_motion_mean\":" << metrics.left_motion_mean << ','
        << "\"right_motion_mean\":" << metrics.right_motion_mean << ','
        << "\"stitched_motion_mean\":" << metrics.stitched_motion_mean << ','
        << "\"stitch_fps\":" << metrics.stitch_fps << ','
        << "\"stitch_actual_fps\":" << metrics.stitch_actual_fps << ','
        << "\"worker_fps\":" << metrics.worker_fps << ','
        << "\"output_written_fps\":" << metrics.output_written_fps << ','
        << "\"production_output_written_fps\":" << metrics.production_output_written_fps << ','
        << "\"pair_skew_ms_mean\":" << metrics.pair_skew_ms_mean << ','
        << "\"pair_source_skew_ms_mean\":" << metrics.pair_source_skew_ms_mean << ','
        << "\"source_time_valid_left\":" << (metrics.source_time_valid_left ? "true" : "false") << ','
        << "\"source_time_valid_right\":" << (metrics.source_time_valid_right ? "true" : "false") << ','
        << "\"source_time_mode\":\"" << json_escape(metrics.source_time_mode) << "\","
        << "\"sync_effective_offset_ms\":" << metrics.sync_effective_offset_ms << ','
        << "\"sync_offset_source\":\"" << json_escape(metrics.sync_offset_source) << "\","
        << "\"sync_offset_confidence\":" << metrics.sync_offset_confidence << ','
        << "\"sync_recalibration_count\":" << metrics.sync_recalibration_count << ','
        << "\"sync_estimate_pairs\":" << metrics.sync_estimate_pairs << ','
        << "\"sync_estimate_avg_gap_ms\":" << metrics.sync_estimate_avg_gap_ms << ','
        << "\"sync_estimate_score\":" << metrics.sync_estimate_score << ','
        << "\"gpu_enabled\":" << (metrics.gpu_enabled ? "true" : "false") << ','
        << "\"gpu_reason\":\"" << json_escape(metrics.gpu_reason) << "\","
        << "\"gpu_feature_enabled\":" << (metrics.gpu_feature_enabled ? "true" : "false") << ','
        << "\"gpu_feature_reason\":\"" << json_escape(metrics.gpu_feature_reason) << "\","
        << "\"gpu_errors\":" << metrics.gpu_errors << ','
        << "\"calibrated\":" << (metrics.calibrated ? "true" : "false") << ','
        << "\"output_width\":" << metrics.output_width << ','
        << "\"output_height\":" << metrics.output_height << ','
        << "\"production_output_width\":" << metrics.production_output_width << ','
        << "\"production_output_height\":" << metrics.production_output_height << ','
        << "\"stitched_count\":" << metrics.stitched_count << ','
        << "\"wait_both_streams_count\":" << metrics.wait_both_streams_count << ','
        << "\"wait_sync_pair_count\":" << metrics.wait_sync_pair_count << ','
        << "\"wait_next_frame_count\":" << metrics.wait_next_frame_count << ','
        << "\"wait_paired_fresh_count\":" << metrics.wait_paired_fresh_count << ','
        << "\"wait_paired_fresh_left_count\":" << metrics.wait_paired_fresh_left_count << ','
        << "\"wait_paired_fresh_right_count\":" << metrics.wait_paired_fresh_right_count << ','
        << "\"wait_paired_fresh_both_count\":" << metrics.wait_paired_fresh_both_count << ','
        << "\"wait_paired_fresh_left_age_ms_avg\":" << metrics.wait_paired_fresh_left_age_ms_avg << ','
        << "\"wait_paired_fresh_right_age_ms_avg\":" << metrics.wait_paired_fresh_right_age_ms_avg << ','
        << "\"realtime_fallback_pair_count\":" << metrics.realtime_fallback_pair_count << ','
        << "\"left_frames_total\":" << metrics.left_frames_total << ','
        << "\"right_frames_total\":" << metrics.right_frames_total << ','
        << "\"left_buffered_frames\":" << metrics.left_buffered_frames << ','
        << "\"right_buffered_frames\":" << metrics.right_buffered_frames << ','
        << "\"left_stale_drops\":" << metrics.left_stale_drops << ','
        << "\"right_stale_drops\":" << metrics.right_stale_drops << ','
        << "\"left_launch_failures\":" << metrics.left_launch_failures << ','
        << "\"right_launch_failures\":" << metrics.right_launch_failures << ','
        << "\"left_read_failures\":" << metrics.left_read_failures << ','
        << "\"right_read_failures\":" << metrics.right_read_failures << ','
        << "\"left_reader_restarts\":" << metrics.left_reader_restarts << ','
        << "\"right_reader_restarts\":" << metrics.right_reader_restarts << ','
        << "\"left_content_frozen\":" << (metrics.left_content_frozen ? "true" : "false") << ','
        << "\"right_content_frozen\":" << (metrics.right_content_frozen ? "true" : "false") << ','
        << "\"left_frozen_duration_sec\":" << metrics.left_frozen_duration_sec << ','
        << "\"right_frozen_duration_sec\":" << metrics.right_frozen_duration_sec << ','
        << "\"left_freeze_restarts\":" << metrics.left_freeze_restarts << ','
        << "\"right_freeze_restarts\":" << metrics.right_freeze_restarts << ','
        << "\"output_active\":" << (metrics.output_active ? "true" : "false") << ','
        << "\"output_frames_written\":" << metrics.output_frames_written << ','
        << "\"output_frames_dropped\":" << metrics.output_frames_dropped << ','
        << "\"output_pending_frames\":" << metrics.output_pending_frames << ','
        << "\"output_queue_capacity\":" << metrics.output_queue_capacity << ','
        << "\"output_drop_policy\":\"" << json_escape(metrics.output_drop_policy) << "\","
        << "\"output_target\":\"" << json_escape(metrics.output_target) << "\","
        << "\"output_command_line\":\"" << json_escape(metrics.output_command_line) << "\","
        << "\"output_effective_codec\":\"" << json_escape(metrics.output_effective_codec) << "\","
        << "\"output_runtime_mode\":\"" << json_escape(metrics.output_runtime_mode) << "\","
        << "\"output_last_error\":\"" << json_escape(metrics.output_last_error) << "\","
        << "\"production_output_active\":" << (metrics.production_output_active ? "true" : "false") << ','
        << "\"production_output_frames_written\":" << metrics.production_output_frames_written << ','
        << "\"production_output_frames_dropped\":" << metrics.production_output_frames_dropped << ','
        << "\"production_output_pending_frames\":" << metrics.production_output_pending_frames << ','
        << "\"production_output_queue_capacity\":" << metrics.production_output_queue_capacity << ','
        << "\"production_output_drop_policy\":\"" << json_escape(metrics.production_output_drop_policy) << "\","
        << "\"production_output_target\":\"" << json_escape(metrics.production_output_target) << "\","
        << "\"production_output_command_line\":\"" << json_escape(metrics.production_output_command_line) << "\","
        << "\"production_output_effective_codec\":\"" << json_escape(metrics.production_output_effective_codec) << "\","
        << "\"production_output_runtime_mode\":\"" << json_escape(metrics.production_output_runtime_mode) << "\","
        << "\"production_output_last_error\":\"" << json_escape(metrics.production_output_last_error) << "\","
        << "\"left_last_error\":\"" << json_escape(metrics.left_last_error) << "\","
        << "\"right_last_error\":\"" << json_escape(metrics.right_last_error) << "\","
        << "\"gpu_warp_count\":" << metrics.gpu_warp_count << ','
        << "\"cpu_warp_count\":" << metrics.cpu_warp_count << ','
        << "\"gpu_blend_count\":" << metrics.gpu_blend_count << ','
        << "\"cpu_blend_count\":" << metrics.cpu_blend_count << ','
        << "\"geometry_mode\":\"" << json_escape(metrics.geometry_mode) << "\","
        << "\"alignment_mode\":\"" << json_escape(metrics.alignment_mode) << "\","
        << "\"seam_mode\":\"" << json_escape(metrics.seam_mode) << "\","
        << "\"exposure_mode\":\"" << json_escape(metrics.exposure_mode) << "\","
        << "\"blend_mode\":\"" << json_escape(metrics.blend_mode) << "\","
        << "\"geometry_artifact_path\":\"" << json_escape(metrics.geometry_artifact_path) << "\","
        << "\"geometry_artifact_model\":\"" << json_escape(metrics.geometry_artifact_model) << "\","
        << "\"residual_alignment_error_px\":" << metrics.residual_alignment_error_px << ','
        << "\"seam_path_jitter_px\":" << metrics.seam_path_jitter_px << ','
        << "\"exposure_gain\":" << metrics.exposure_gain << ','
        << "\"exposure_bias\":" << metrics.exposure_bias << ','
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

inline std::string command_status_json(
    std::int64_t seq,
    double timestamp_sec,
    const std::string& status,
    const std::string& message) {
    std::ostringstream out;
    out << "{\"seq\":" << seq
        << ",\"type\":\"status\",\"timestamp_sec\":" << std::fixed << std::setprecision(3) << timestamp_sec
        << ",\"payload\":{\"status\":\"" << json_escape(status) << "\",\"message\":\"" << json_escape(message) << "\"}}";
    return out.str();
}

inline std::string command_error_json(
    std::int64_t seq,
    double timestamp_sec,
    const std::string& code,
    const std::string& message,
    const std::string& details = "") {
    std::ostringstream out;
    out << "{\"seq\":" << seq
        << ",\"type\":\"error\",\"timestamp_sec\":" << std::fixed << std::setprecision(3) << timestamp_sec
        << ",\"payload\":{\"code\":\"" << json_escape(code) << "\",\"message\":\"" << json_escape(message) << "\"";
    if (!details.empty()) {
        out << ",\"details\":\"" << json_escape(details) << "\"";
    }
    out << "}}";
    return out.str();
}

inline bool command_type_is(const std::string& line, const char* command_type) {
    const std::string needle1 = "\"type\":\"" + std::string(command_type) + "\"";
    const std::string needle2 = "\"type\": \"" + std::string(command_type) + "\"";
    return line.find(needle1) != std::string::npos || line.find(needle2) != std::string::npos;
}

}  // namespace hogak::control
