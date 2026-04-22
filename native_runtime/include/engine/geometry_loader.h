#pragma once

#include <string>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"

namespace hogak::engine {

struct RuntimeGeometryArtifactData {
    std::string model = "virtual-center-rectilinear";
    std::string alignment_model = "rigid";
    std::string residual_model = "rigid";
    std::string artifact_path;
    cv::Mat alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
    cv::Size output_size{};
    cv::Size left_input_size{};
    cv::Size right_input_size{};
    std::string left_projection_model = "rectilinear";
    std::string right_projection_model = "rectilinear";
    double left_focal_px = 0.0;
    double left_center_x = 0.0;
    double left_center_y = 0.0;
    double right_focal_px = 0.0;
    double right_center_x = 0.0;
    double right_center_y = 0.0;
    double left_virtual_focal_px = 0.0;
    double left_virtual_center_x = 0.0;
    double left_virtual_center_y = 0.0;
    double right_virtual_focal_px = 0.0;
    double right_virtual_center_x = 0.0;
    double right_virtual_center_y = 0.0;
    cv::Mat left_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
    cv::Mat right_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
    bool mesh_fallback_used = false;
    int mesh_grid_cols = 0;
    int mesh_grid_rows = 0;
    cv::Mat mesh_control_displacement_x{};
    cv::Mat mesh_control_displacement_y{};
    double mesh_max_displacement_px = 0.0;
    double mesh_max_local_scale_drift = 0.0;
    double mesh_max_local_rotation_drift = 0.0;
    double residual_alignment_error_px = 0.0;
    int seam_transition_px = 64;
    double seam_smoothness_penalty = 4.0;
    double seam_temporal_penalty = 2.0;
    bool exposure_enabled = true;
    double exposure_gain_min = 0.7;
    double exposure_gain_max = 1.4;
    double exposure_bias_abs_max = 35.0;
    bool crop_enabled = false;
    cv::Rect crop_rect{};
};

std::string runtime_geometry_artifact_candidate_path(const EngineConfig& config);
bool load_runtime_geometry_artifact_from_file(const std::string& path, RuntimeGeometryArtifactData* artifact_out);

}  // namespace hogak::engine
