#include "engine/engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cctype>
#include <ctime>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudaarithm.hpp>
#include <opencv2/cudaimgproc.hpp>
#include <opencv2/cudawarping.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "engine/input_pipeline.h"
#include "engine/metrics_pipeline.h"
#include "engine/output_pipeline.h"
#include "engine/geometry_loader.h"
#include "input/ffmpeg_rtsp_reader.h"
#include "output/output_writer.h"
#include "output/output_writer_factory.h"

namespace hogak::engine {

namespace {

constexpr int kMotionProbeWidth = 64;
constexpr int kMotionProbeHeight = 36;
constexpr double kReaderRestartAgeMs = 1500.0;
constexpr double kReaderRestartCooldownSec = 3.0;
constexpr int kSeamFeatherMinWidth = 48;
constexpr int kSeamFeatherMaxWidth = 192;
constexpr double kSeamFeatherFraction = 0.14;
constexpr int kHeavyMetricSampleEveryN = 15;
constexpr std::int64_t kMetricsSnapshotIntervalNs = 250'000'000LL;

double fps_from_period_ns(std::int64_t delta_ns) {
    if (delta_ns <= 0) {
        return 0.0;
    }
    return 1'000'000'000.0 / static_cast<double>(delta_ns);
}

double mean_luma(const cv::Mat& image) {
    if (image.empty()) {
        return 0.0;
    }
    cv::Mat gray;
    if (image.channels() == 1) {
        gray = image;
    } else {
        cv::cvtColor(image, gray, cv::COLOR_BGR2GRAY);
    }
    return cv::mean(gray)[0];
}

cv::Mat make_motion_probe_gray(const cv::Mat& image) {
    if (image.empty()) {
        return {};
    }
    cv::Mat gray;
    if (image.channels() == 1) {
        gray = image.clone();
    } else {
        cv::cvtColor(image, gray, cv::COLOR_BGR2GRAY);
    }
    cv::resize(gray, gray, cv::Size(kMotionProbeWidth, kMotionProbeHeight), 0.0, 0.0, cv::INTER_AREA);
    return gray;
}

double probe_motion_mean(const cv::Mat& previous_probe_gray, const cv::Mat& current_probe_gray) {
    if (previous_probe_gray.empty() || current_probe_gray.empty()) {
        return 0.0;
    }
    if (current_probe_gray.size() != previous_probe_gray.size()) {
        return 0.0;
    }
    cv::Mat diff;
    cv::absdiff(previous_probe_gray, current_probe_gray, diff);
    return cv::mean(diff)[0];
}

double clamp_output_scale(double value) {
    return std::clamp(value, 0.1, 1.0);
}

bool should_sample_heavy_metrics(std::int64_t next_frame_index) {
    return next_frame_index <= 1 || (next_frame_index % kHeavyMetricSampleEveryN) == 0;
}

bool output_config_enabled(const OutputConfig& config) {
    return config.runtime != "none" && !config.target.empty();
}

bool gpu_only_mode_enabled(const EngineConfig& config) {
    return config.gpu_mode == "only";
}

bool fail_gpu_only_validation(std::string* reason_out, const std::string& reason) {
    if (reason_out != nullptr) {
        *reason_out = reason;
    }
    return false;
}

bool validate_gpu_only_config(const EngineConfig& config, std::string* reason_out) {
    if (!gpu_only_mode_enabled(config)) {
        return true;
    }
    if (config.input_runtime != "ffmpeg-cuda") {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires runtime.input_runtime=ffmpeg-cuda");
    }
    if (!input_pipe_format_is_nv12(config.left) || !input_pipe_format_is_nv12(config.right)) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires NV12 input on both cameras");
    }
    if (config.output.runtime != "none") {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires outputs.probe.runtime=none");
    }
    if (!config.output.target.empty()) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires outputs.probe.target to be empty");
    }
    if (config.output.debug_overlay) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode does not allow probe debug overlay");
    }
    if (config.production_output.runtime != "gpu-direct") {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires outputs.transmit.runtime=gpu-direct");
    }
    if (config.production_output.target.empty()) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires outputs.transmit.target");
    }
    if (config.production_output.debug_overlay) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode does not allow transmit debug overlay");
    }
    const int gpu_count = cv::cuda::getCudaEnabledDeviceCount();
    if (gpu_count <= config.gpu_device) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode could not find the requested CUDA device");
    }
    if (!hogak::output::output_runtime_available(config.production_output.runtime)) {
        return fail_gpu_only_validation(reason_out, "gpu-only mode requires a working gpu-direct output runtime");
    }
    return true;
}

double runtime_warp_plan_limit_scale(const EngineConfig& config) {
    const double process_scale = std::max(1.0, config.process_scale);
    const double stitch_output_scale = std::max(1.0, config.stitch_output_scale);
    // A stitch canvas can legitimately be much wider than a single source frame.
    return std::max({4.0, process_scale, stitch_output_scale});
}

cv::Mat scale_homography_for_runtime(const cv::Mat& homography, double scale) {
    if (homography.empty() || std::abs(scale - 1.0) < 1e-6) {
        return homography;
    }
    cv::Mat scale_matrix = (cv::Mat_<double>(3, 3) <<
        scale, 0.0, 0.0,
        0.0, scale, 0.0,
        0.0, 0.0, 1.0);
    cv::Mat inv_scale_matrix = (cv::Mat_<double>(3, 3) <<
        1.0 / scale, 0.0, 0.0,
        0.0, 1.0 / scale, 0.0,
        0.0, 0.0, 1.0);
    return scale_matrix * homography * inv_scale_matrix;
}

void build_seam_blend_weights(
    const cv::Mat& overlap_mask,
    const cv::Rect& overlap_roi,
    cv::Mat* weight_left_out,
    cv::Mat* weight_right_out) {
    if (weight_left_out == nullptr || weight_right_out == nullptr) {
        return;
    }

    *weight_left_out = cv::Mat::zeros(overlap_mask.size(), CV_32FC1);
    *weight_right_out = cv::Mat::zeros(overlap_mask.size(), CV_32FC1);
    if (overlap_mask.empty() || overlap_roi.area() <= 0) {
        return;
    }

    const int band_width = std::clamp(
        static_cast<int>(std::round(static_cast<double>(overlap_roi.width) * kSeamFeatherFraction)),
        kSeamFeatherMinWidth,
        std::min(kSeamFeatherMaxWidth, std::max(kSeamFeatherMinWidth, overlap_roi.width)));
    const int half_band = std::max(1, band_width / 2);
    const int seam_center_x = overlap_roi.x + overlap_roi.width / 2;
    const int transition_start_x = std::max(overlap_roi.x, seam_center_x - half_band);
    const int transition_end_x = std::min(overlap_roi.x + overlap_roi.width - 1, seam_center_x + half_band);
    const float denom = static_cast<float>(std::max(1, transition_end_x - transition_start_x));

    for (int y = overlap_roi.y; y < overlap_roi.y + overlap_roi.height; ++y) {
        const auto* mask_row = overlap_mask.ptr<std::uint8_t>(y);
        auto* left_row = weight_left_out->ptr<float>(y);
        auto* right_row = weight_right_out->ptr<float>(y);
        for (int x = overlap_roi.x; x < overlap_roi.x + overlap_roi.width; ++x) {
            if (mask_row[x] == 0) {
                continue;
            }
            if (x <= transition_start_x) {
                left_row[x] = 1.0f;
                right_row[x] = 0.0f;
            } else if (x >= transition_end_x) {
                left_row[x] = 0.0f;
                right_row[x] = 1.0f;
            } else {
                const float alpha = static_cast<float>(x - transition_start_x) / denom;
                left_row[x] = 1.0f - alpha;
                right_row[x] = alpha;
            }
        }
    }
}

cv::Mat scale_camera_matrix_to_runtime(
    const cv::Mat& camera_matrix,
    const cv::Size& stored_size,
    const cv::Size& runtime_size) {
    if (camera_matrix.empty() || stored_size.width <= 0 || stored_size.height <= 0) {
        return camera_matrix.clone();
    }
    cv::Mat scaled = camera_matrix.clone();
    const double scale_x = static_cast<double>(runtime_size.width) / static_cast<double>(stored_size.width);
    const double scale_y = static_cast<double>(runtime_size.height) / static_cast<double>(stored_size.height);
    scaled.at<double>(0, 0) *= scale_x;
    scaled.at<double>(0, 2) *= scale_x;
    scaled.at<double>(1, 1) *= scale_y;
    scaled.at<double>(1, 2) *= scale_y;
    return scaled;
}

bool prepare_warp_plan(
    const cv::Size& left_size,
    const cv::Size& right_size,
    const cv::Mat& homography,
    double max_output_scale,
    int max_output_pixels,
    cv::Mat* adjusted_homography_out,
    cv::Size* output_size_out,
    cv::Rect* left_roi_out) {
    if (adjusted_homography_out == nullptr || output_size_out == nullptr || left_roi_out == nullptr) {
        return false;
    }

    std::vector<cv::Point2f> corners_left = {
        {0.0f, 0.0f},
        {static_cast<float>(left_size.width), 0.0f},
        {static_cast<float>(left_size.width), static_cast<float>(left_size.height)},
        {0.0f, static_cast<float>(left_size.height)},
    };
    std::vector<cv::Point2f> corners_right = {
        {0.0f, 0.0f},
        {static_cast<float>(right_size.width), 0.0f},
        {static_cast<float>(right_size.width), static_cast<float>(right_size.height)},
        {0.0f, static_cast<float>(right_size.height)},
    };
    std::vector<cv::Point2f> warped_right;
    cv::perspectiveTransform(corners_right, warped_right, homography);

    std::vector<cv::Point2f> all_corners = corners_left;
    all_corners.insert(all_corners.end(), warped_right.begin(), warped_right.end());

    float min_x = all_corners.front().x;
    float min_y = all_corners.front().y;
    float max_x = all_corners.front().x;
    float max_y = all_corners.front().y;
    for (const auto& p : all_corners) {
        min_x = std::min(min_x, p.x);
        min_y = std::min(min_y, p.y);
        max_x = std::max(max_x, p.x);
        max_y = std::max(max_y, p.y);
    }

    const int tx = static_cast<int>(-std::floor(min_x));
    const int ty = static_cast<int>(-std::floor(min_y));
    const int width = static_cast<int>(std::ceil(max_x) - std::floor(min_x));
    const int height = static_cast<int>(std::ceil(max_y) - std::floor(min_y));
    if (width <= 0 || height <= 0) {
        return false;
    }

    const int max_dim = static_cast<int>(std::max({left_size.width, right_size.width, left_size.height, right_size.height}) *
        std::max(1.0, max_output_scale));
    if (width > max_dim || height > max_dim) {
        return false;
    }
    if (width * height > max_output_pixels) {
        return false;
    }

    cv::Mat translation = (cv::Mat_<double>(3, 3) <<
        1.0, 0.0, static_cast<double>(tx),
        0.0, 1.0, static_cast<double>(ty),
        0.0, 0.0, 1.0);
    *adjusted_homography_out = translation * homography;
    *output_size_out = cv::Size(width, height);
    *left_roi_out = cv::Rect(tx, ty, left_size.width, left_size.height);
    return true;
}

}  // namespace

bool StitchEngine::build_virtual_center_rectilinear_maps_locked(
    const cv::Size& image_size,
    double source_focal_px,
    double source_center_x,
    double source_center_y,
    double virtual_focal_px,
    double virtual_center_x,
    double virtual_center_y,
    const cv::Mat& virtual_to_source_rotation,
    cv::Mat* map_x_out,
    cv::Mat* map_y_out) const {
    if (map_x_out == nullptr || map_y_out == nullptr || image_size.width <= 0 || image_size.height <= 0) {
        return false;
    }

    const double source_focal = (source_focal_px > 1e-6)
        ? source_focal_px
        : static_cast<double>(std::max(image_size.width, image_size.height)) * 0.90;
    const double source_cx = (source_center_x > 0.0) ? source_center_x : static_cast<double>(image_size.width) * 0.5;
    const double source_cy = (source_center_y > 0.0) ? source_center_y : static_cast<double>(image_size.height) * 0.5;
    const double virtual_focal = (virtual_focal_px > 1e-6) ? virtual_focal_px : source_focal;
    const double virtual_cx = (virtual_center_x > 0.0) ? virtual_center_x : static_cast<double>(image_size.width) * 0.5;
    const double virtual_cy = (virtual_center_y > 0.0) ? virtual_center_y : static_cast<double>(image_size.height) * 0.5;

    cv::Mat rotation = virtual_to_source_rotation.empty()
        ? cv::Mat::eye(3, 3, CV_64F)
        : virtual_to_source_rotation.clone();
    if (rotation.rows != 3 || rotation.cols != 3) {
        return false;
    }
    rotation.convertTo(rotation, CV_64F);
    const cv::Matx33d rotation_m(
        rotation.at<double>(0, 0), rotation.at<double>(0, 1), rotation.at<double>(0, 2),
        rotation.at<double>(1, 0), rotation.at<double>(1, 1), rotation.at<double>(1, 2),
        rotation.at<double>(2, 0), rotation.at<double>(2, 1), rotation.at<double>(2, 2));

    map_x_out->create(image_size, CV_32FC1);
    map_y_out->create(image_size, CV_32FC1);
    for (int y = 0; y < image_size.height; ++y) {
        auto* map_x_row = map_x_out->ptr<float>(y);
        auto* map_y_row = map_y_out->ptr<float>(y);
        const double ray_y = (static_cast<double>(y) - virtual_cy) / virtual_focal;
        for (int x = 0; x < image_size.width; ++x) {
            const double ray_x = (static_cast<double>(x) - virtual_cx) / virtual_focal;
            const cv::Vec3d ray_virtual(ray_x, ray_y, 1.0);
            const cv::Vec3d ray_source = rotation_m * ray_virtual;
            if (ray_source[2] <= 1e-9) {
                map_x_row[x] = -1.0f;
                map_y_row[x] = -1.0f;
                continue;
            }
            map_x_row[x] = static_cast<float>(source_focal * (ray_source[0] / ray_source[2]) + source_cx);
            map_y_row[x] = static_cast<float>(source_focal * (ray_source[1] / ray_source[2]) + source_cy);
        }
    }
    return true;
}

bool StitchEngine::build_runtime_mesh_maps_locked(
    const cv::Size& canvas_size,
    const cv::Mat& control_displacement_x,
    const cv::Mat& control_displacement_y,
    cv::Mat* map_x_out,
    cv::Mat* map_y_out) const {
    if (map_x_out == nullptr || map_y_out == nullptr) {
        return false;
    }
    if (canvas_size.width <= 1 || canvas_size.height <= 1) {
        return false;
    }
    if (control_displacement_x.empty() ||
        control_displacement_y.empty() ||
        control_displacement_x.type() != CV_32F ||
        control_displacement_y.type() != CV_32F ||
        control_displacement_x.size() != control_displacement_y.size() ||
        control_displacement_x.rows < 2 ||
        control_displacement_x.cols < 2) {
        return false;
    }

    const int node_rows = control_displacement_x.rows;
    const int node_cols = control_displacement_x.cols;
    const int grid_rows = node_rows - 1;
    const int grid_cols = node_cols - 1;
    if (grid_rows <= 0 || grid_cols <= 0) {
        return false;
    }
    const double cell_w = static_cast<double>(std::max(1, canvas_size.width - 1)) / static_cast<double>(grid_cols);
    const double cell_h = static_cast<double>(std::max(1, canvas_size.height - 1)) / static_cast<double>(grid_rows);
    if (cell_w <= 0.0 || cell_h <= 0.0) {
        return false;
    }

    map_x_out->create(canvas_size, CV_32FC1);
    map_y_out->create(canvas_size, CV_32FC1);
    for (int y = 0; y < canvas_size.height; ++y) {
        auto* map_x_row = map_x_out->ptr<float>(y);
        auto* map_y_row = map_y_out->ptr<float>(y);
        const double gy_raw = static_cast<double>(y) / cell_h;
        const double gy_clamped = std::clamp(gy_raw, 0.0, static_cast<double>(grid_rows) - 1e-6);
        const int iy = std::clamp(static_cast<int>(std::floor(gy_clamped)), 0, grid_rows - 1);
        const float ty = static_cast<float>(gy_clamped - static_cast<double>(iy));
        const auto* dx_row0 = control_displacement_x.ptr<float>(iy);
        const auto* dx_row1 = control_displacement_x.ptr<float>(iy + 1);
        const auto* dy_row0 = control_displacement_y.ptr<float>(iy);
        const auto* dy_row1 = control_displacement_y.ptr<float>(iy + 1);
        for (int x = 0; x < canvas_size.width; ++x) {
            const double gx_raw = static_cast<double>(x) / cell_w;
            const double gx_clamped = std::clamp(gx_raw, 0.0, static_cast<double>(grid_cols) - 1e-6);
            const int ix = std::clamp(static_cast<int>(std::floor(gx_clamped)), 0, grid_cols - 1);
            const float tx = static_cast<float>(gx_clamped - static_cast<double>(ix));
            const float dx00 = dx_row0[ix];
            const float dx10 = dx_row0[ix + 1];
            const float dx01 = dx_row1[ix];
            const float dx11 = dx_row1[ix + 1];
            const float dy00 = dy_row0[ix];
            const float dy10 = dy_row0[ix + 1];
            const float dy01 = dy_row1[ix];
            const float dy11 = dy_row1[ix + 1];
            const float dx_top = dx00 + ((dx10 - dx00) * tx);
            const float dx_bottom = dx01 + ((dx11 - dx01) * tx);
            const float dy_top = dy00 + ((dy10 - dy00) * tx);
            const float dy_bottom = dy01 + ((dy11 - dy01) * tx);
            const float dx = dx_top + ((dx_bottom - dx_top) * ty);
            const float dy = dy_top + ((dy_bottom - dy_top) * ty);
            map_x_row[x] = static_cast<float>(x) - dx;
            map_y_row[x] = static_cast<float>(y) - dy;
        }
    }
    return true;
}

bool StitchEngine::build_affine_output_plan_locked(
    const cv::Size& left_size,
    const cv::Size& right_size,
    const cv::Mat& affine_matrix,
    cv::Size* output_size_out,
    cv::Rect* left_roi_out,
    cv::Rect* overlap_roi_out,
    cv::Mat* adjusted_affine_out) {
    if (output_size_out == nullptr || left_roi_out == nullptr || overlap_roi_out == nullptr || adjusted_affine_out == nullptr) {
        return false;
    }
    cv::Mat affine = affine_matrix.empty() ? cv::Mat::eye(3, 3, CV_64F) : affine_matrix.clone();
    return prepare_warp_plan(
        left_size,
        right_size,
        affine,
        runtime_warp_plan_limit_scale(config_),
        40'000'000,
        adjusted_affine_out,
        output_size_out,
        left_roi_out);
}

bool StitchEngine::compute_exposure_compensation_locked(
    const cv::Mat& canvas_left,
    const cv::Mat& warped_right,
    const cv::Mat& overlap_mask,
    cv::Mat* compensated_right_out,
    double* gain_out,
    double* bias_out) const {
    if (compensated_right_out == nullptr || gain_out == nullptr || bias_out == nullptr) {
        return false;
    }
    *gain_out = 1.0;
    *bias_out = 0.0;
    if (!runtime_geometry_.exposure_enabled || overlap_mask.empty() || !cv::countNonZero(overlap_mask)) {
        *compensated_right_out = warped_right.clone();
        return !compensated_right_out->empty();
    }

    cv::Mat gray_left;
    cv::Mat gray_right;
    cv::cvtColor(canvas_left, gray_left, cv::COLOR_BGR2GRAY);
    cv::cvtColor(warped_right, gray_right, cv::COLOR_BGR2GRAY);

    const cv::Scalar left_mean = cv::mean(gray_left, overlap_mask);
    const cv::Scalar right_mean = cv::mean(gray_right, overlap_mask);
    cv::Mat left_gray_f;
    cv::Mat right_gray_f;
    gray_left.convertTo(left_gray_f, CV_32F);
    gray_right.convertTo(right_gray_f, CV_32F);
    cv::Scalar left_stddev;
    cv::Scalar right_stddev;
    cv::Scalar dummy_mean;
    cv::meanStdDev(left_gray_f, dummy_mean, left_stddev, overlap_mask);
    cv::meanStdDev(right_gray_f, dummy_mean, right_stddev, overlap_mask);

    const double left_std = std::max(1e-3, left_stddev[0]);
    const double right_std = std::max(1e-3, right_stddev[0]);
    double gain = left_std / right_std;
    gain = std::clamp(gain, runtime_geometry_.exposure_gain_min, runtime_geometry_.exposure_gain_max);
    double bias = left_mean[0] - gain * right_mean[0];
    bias = std::clamp(bias, -runtime_geometry_.exposure_bias_abs_max, runtime_geometry_.exposure_bias_abs_max);

    warped_right.convertTo(*compensated_right_out, warped_right.type(), gain, bias);
    *gain_out = gain;
    *bias_out = bias;
    return !compensated_right_out->empty();
}

bool StitchEngine::build_dynamic_seam_path_locked(
    const cv::Mat& canvas_left,
    const cv::Mat& warped_right,
    const cv::Mat& overlap_mask,
    std::vector<int>* seam_path_out) const {
    if (seam_path_out == nullptr || canvas_left.empty() || warped_right.empty() || overlap_mask.empty()) {
        return false;
    }

    cv::Mat gray_left;
    cv::Mat gray_right;
    cv::cvtColor(canvas_left, gray_left, cv::COLOR_BGR2GRAY);
    cv::cvtColor(warped_right, gray_right, cv::COLOR_BGR2GRAY);

    cv::Mat diff;
    cv::absdiff(gray_left, gray_right, diff);
    cv::Mat grad_x;
    cv::Mat grad_y;
    cv::Sobel(gray_left, grad_x, CV_32F, 1, 0, 3);
    cv::Sobel(gray_left, grad_y, CV_32F, 0, 1, 3);
    cv::Mat grad_mag;
    cv::magnitude(grad_x, grad_y, grad_mag);

    cv::Mat diff_f;
    cv::Mat grad_f;
    diff.convertTo(diff_f, CV_32F);
    grad_mag.convertTo(grad_f, CV_32F);
    cv::Mat cost = diff_f + (0.25f * grad_f);
    cost.setTo(1e9f, overlap_mask == 0);

    const int height = overlap_mask.rows;
    const int width = overlap_mask.cols;
    std::vector<int> seam_path(height, -1);
    std::vector<std::vector<double>> dp(height, std::vector<double>(width, std::numeric_limits<double>::infinity()));
    std::vector<std::vector<int>> prev_index(height, std::vector<int>(width, -1));

    int first_valid_row = -1;
    int last_valid_row = -1;
    int x_min = width;
    int x_max = -1;
    for (int y = 0; y < height; ++y) {
        const auto* mask_row = overlap_mask.ptr<std::uint8_t>(y);
        for (int x = 0; x < width; ++x) {
            if (mask_row[x] > 0) {
                first_valid_row = (first_valid_row < 0) ? y : first_valid_row;
                last_valid_row = y;
                x_min = std::min(x_min, x);
                x_max = std::max(x_max, x);
            }
        }
    }
    if (first_valid_row < 0 || x_max < x_min) {
        return false;
    }

    const double temporal_penalty = std::max(0.0, runtime_geometry_.seam_temporal_penalty);
    const double smoothness_penalty = std::max(0.0, runtime_geometry_.seam_smoothness_penalty);
    const bool has_prev_seam = previous_seam_path_.size() == static_cast<std::size_t>(height);

    for (int y = first_valid_row; y <= last_valid_row; ++y) {
        const auto* mask_row = overlap_mask.ptr<std::uint8_t>(y);
        const auto* cost_row = cost.ptr<float>(y);
        for (int x = x_min; x <= x_max; ++x) {
            if (mask_row[x] == 0) {
                continue;
            }
            const double temporal_cost = has_prev_seam
                ? temporal_penalty * std::abs(static_cast<double>(x) - static_cast<double>(previous_seam_path_[y]))
                : 0.0;
            const double row_cost = static_cast<double>(cost_row[x]) + temporal_cost;
            if (y == first_valid_row) {
                dp[y][x] = row_cost;
                prev_index[y][x] = x;
                continue;
            }
            double best_cost = std::numeric_limits<double>::infinity();
            int best_index = x;
            for (int step = -1; step <= 1; ++step) {
                const int prev_x = x + step;
                if (prev_x < x_min || prev_x > x_max) {
                    continue;
                }
                const double candidate = dp[y - 1][prev_x];
                if (!std::isfinite(candidate)) {
                    continue;
                }
                const double score = candidate + (smoothness_penalty * static_cast<double>(std::abs(step)));
                if (score < best_cost) {
                    best_cost = score;
                    best_index = prev_x;
                }
            }
            if (!std::isfinite(best_cost)) {
                best_cost = row_cost;
                best_index = x;
            }
            dp[y][x] = row_cost + best_cost;
            prev_index[y][x] = best_index;
        }
    }

    double best_final = std::numeric_limits<double>::infinity();
    int best_x = (x_min + x_max) / 2;
    const auto* last_mask_row = overlap_mask.ptr<std::uint8_t>(last_valid_row);
    for (int x = x_min; x <= x_max; ++x) {
        if (last_mask_row[x] == 0) {
            continue;
        }
        if (dp[last_valid_row][x] < best_final) {
            best_final = dp[last_valid_row][x];
            best_x = x;
        }
    }
    seam_path[last_valid_row] = best_x;
    for (int y = last_valid_row; y > first_valid_row; --y) {
        const int prev_x = prev_index[y][seam_path[y]];
        seam_path[y - 1] = (prev_x >= x_min && prev_x <= x_max) ? prev_x : seam_path[y];
    }
    for (int y = 0; y < first_valid_row; ++y) {
        seam_path[y] = seam_path[first_valid_row];
    }
    for (int y = first_valid_row; y < height; ++y) {
        if (seam_path[y] < 0) {
            seam_path[y] = (y > 0) ? seam_path[y - 1] : best_x;
        }
    }
    *seam_path_out = std::move(seam_path);
    return true;
}

void StitchEngine::blend_with_dynamic_seam_locked(
    const cv::Mat& canvas_left,
    const cv::Mat& warped_right,
    const cv::Mat& left_mask,
    const cv::Mat& right_mask,
    const std::vector<int>& seam_path,
    int transition_px,
    cv::Mat* stitched_out) const {
    if (stitched_out == nullptr) {
        return;
    }
    stitched_out->create(canvas_left.size(), CV_8UC3);
    stitched_out->setTo(cv::Scalar::all(0));
    const cv::Mat left_valid = left_mask > 0;
    const cv::Mat right_valid = right_mask > 0;
    const cv::Mat overlap = left_valid & right_valid;
    canvas_left.copyTo(*stitched_out, left_valid & ~right_valid);
    warped_right.copyTo(*stitched_out, right_valid & ~left_valid);
    if (!cv::countNonZero(overlap)) {
        return;
    }

    const int width = stitched_out->cols;
    const int height = stitched_out->rows;
    const int transition = std::max(2, transition_px);
    const cv::Rect overlap_bounds = cv::boundingRect(overlap);
    const int x_min = std::max(0, std::min(width - 1, overlap_bounds.x));
    const int x_max = std::min(width - 1, overlap_bounds.x + overlap_bounds.width - 1);
    const int y_min = std::max(0, overlap_bounds.y);
    const int y_max = std::min(height - 1, overlap_bounds.y + overlap_bounds.height - 1);
    for (int y = y_min; y <= y_max && y < static_cast<int>(seam_path.size()); ++y) {
        const int seam_x = std::clamp(seam_path[y], x_min, x_max);
        const double start_x = static_cast<double>(seam_x) - (static_cast<double>(transition) * 0.5);
        const double end_x = static_cast<double>(seam_x) + (static_cast<double>(transition) * 0.5);
        for (int x = x_min; x <= x_max; ++x) {
            if (!overlap.at<std::uint8_t>(y, x)) {
                continue;
            }
            const double right_w = std::clamp((static_cast<double>(x) - start_x) / std::max(1.0, end_x - start_x), 0.0, 1.0);
            const double left_w = 1.0 - right_w;
            const cv::Vec3b left_px = canvas_left.at<cv::Vec3b>(y, x);
            const cv::Vec3b right_px = warped_right.at<cv::Vec3b>(y, x);
            const cv::Vec3d blended(
                (left_px[0] * left_w) + (right_px[0] * right_w),
                (left_px[1] * left_w) + (right_px[1] * right_w),
                (left_px[2] * left_w) + (right_px[2] * right_w));
            stitched_out->at<cv::Vec3b>(y, x) = cv::Vec3b(
                static_cast<std::uint8_t>(std::clamp(blended[0], 0.0, 255.0)),
                static_cast<std::uint8_t>(std::clamp(blended[1], 0.0, 255.0)),
                static_cast<std::uint8_t>(std::clamp(blended[2], 0.0, 255.0)));
        }
    }
}

cv::Rect StitchEngine::largest_valid_rect_locked(const cv::Mat& valid_mask) const {
    if (valid_mask.empty()) {
        return cv::Rect();
    }
    const cv::Mat binary = valid_mask > 0;
    const int height = binary.rows;
    const int width = binary.cols;
    std::vector<int> heights(width, 0);
    int best_area = 0;
    cv::Rect best_rect(0, 0, width, height);
    for (int y = 0; y < height; ++y) {
        const auto* row = binary.ptr<std::uint8_t>(y);
        for (int x = 0; x < width; ++x) {
            heights[x] = (row[x] > 0) ? (heights[x] + 1) : 0;
        }
        std::vector<int> stack;
        stack.reserve(static_cast<std::size_t>(width) + 1U);
        for (int x = 0; x <= width; ++x) {
            const int current = (x < width) ? heights[x] : 0;
            while (!stack.empty() && current < heights[stack.back()]) {
                const int h = heights[stack.back()];
                stack.pop_back();
                if (h <= 0) {
                    continue;
                }
                const int left = stack.empty() ? 0 : (stack.back() + 1);
                const int rect_width = x - left;
                const int area = h * rect_width;
                if (area > best_area && rect_width > 0) {
                    best_area = area;
                    best_rect = cv::Rect(left, (y - h) + 1, rect_width, h);
                }
            }
            stack.push_back(x);
        }
    }
    if (best_rect.width <= 0 || best_rect.height <= 0) {
        return cv::Rect(0, 0, width, height);
    }
    return best_rect;
}

cv::Rect StitchEngine::resolve_runtime_crop_rect_locked(const cv::Mat& valid_mask) const {
    cv::Rect crop_rect = runtime_geometry_.crop_enabled ? runtime_geometry_.crop_rect : cv::Rect();
    const cv::Rect recomputed_rect = largest_valid_rect_locked(valid_mask);
    const bool crop_rect_valid =
        crop_rect.width > 0 &&
        crop_rect.height > 0 &&
        crop_rect.x >= 0 &&
        crop_rect.y >= 0 &&
        crop_rect.x + crop_rect.width <= valid_mask.cols &&
        crop_rect.y + crop_rect.height <= valid_mask.rows;
    const bool recomputed_valid =
        recomputed_rect.width > 0 &&
        recomputed_rect.height > 0 &&
        recomputed_rect.x >= 0 &&
        recomputed_rect.y >= 0 &&
        recomputed_rect.x + recomputed_rect.width <= valid_mask.cols &&
        recomputed_rect.y + recomputed_rect.height <= valid_mask.rows;
    if (runtime_geometry_.model == "virtual-center-rectilinear") {
        if (crop_rect_valid && recomputed_valid) {
            const cv::Rect intersection = crop_rect & recomputed_rect;
            if (intersection.width > 0 && intersection.height > 0) {
                return intersection;
            }
        }
        if (recomputed_valid) {
            return recomputed_rect;
        }
        if (crop_rect_valid) {
            return crop_rect;
        }
        return cv::Rect();
    }
    if (crop_rect_valid) {
        return crop_rect;
    }
    return recomputed_rect;
}

bool StitchEngine::compose_stitched_video_quality_locked(
    const cv::Mat& canvas_left,
    const cv::Mat& warped_right,
    cv::Mat* stitched_out,
    bool* used_exposure_compensation_out,
    bool* used_dynamic_seam_out) {
    if (stitched_out == nullptr || canvas_left.empty() || warped_right.empty()) {
        return false;
    }
    if (used_exposure_compensation_out != nullptr) {
        *used_exposure_compensation_out = false;
    }
    if (used_dynamic_seam_out != nullptr) {
        *used_dynamic_seam_out = false;
    }

    cv::Mat compensated_right = warped_right;
    double exposure_gain = 1.0;
    double exposure_bias = 0.0;
    if (!runtime_geometry_.exposure_enabled) {
        last_exposure_gain_ = exposure_gain;
        last_exposure_bias_ = exposure_bias;
    } else if (compute_exposure_compensation_locked(
                   canvas_left,
                   warped_right,
                   overlap_mask_,
                   &compensated_right,
                   &exposure_gain,
                   &exposure_bias)) {
        if (used_exposure_compensation_out != nullptr) {
            *used_exposure_compensation_out = true;
        }
    } else {
        compensated_right = warped_right;
    }
    last_exposure_gain_ = exposure_gain;
    last_exposure_bias_ = exposure_bias;
    metrics_.exposure_gain = exposure_gain;
    metrics_.exposure_bias = exposure_bias;

    std::vector<int> seam_path;
    if (!build_dynamic_seam_path_locked(canvas_left, compensated_right, overlap_mask_, &seam_path)) {
        seam_path.assign(canvas_left.rows, canvas_left.cols / 2);
    }
    update_seam_path_jitter_locked(seam_path);
    metrics_.seam_path_jitter_px = last_seam_path_jitter_px_;

    cv::Mat stitched_uncropped;
    blend_with_dynamic_seam_locked(
        canvas_left,
        compensated_right,
        left_mask_template_,
        right_mask_template_,
        seam_path,
        runtime_geometry_.seam_transition_px,
        &stitched_uncropped);

    cv::Mat valid_mask;
    cv::bitwise_or(left_mask_template_, right_mask_template_, valid_mask);
    cv::Rect crop_rect = runtime_geometry_.crop_enabled ? runtime_geometry_.crop_rect : cv::Rect();
    const bool crop_rect_valid =
        crop_rect.width > 0 &&
        crop_rect.height > 0 &&
        crop_rect.x >= 0 &&
        crop_rect.y >= 0 &&
        crop_rect.x + crop_rect.width <= stitched_uncropped.cols &&
        crop_rect.y + crop_rect.height <= stitched_uncropped.rows;
    if (!crop_rect_valid) {
        crop_rect = largest_valid_rect_locked(valid_mask);
    }
    if (crop_rect.width > 0 &&
        crop_rect.height > 0 &&
        crop_rect.x >= 0 &&
        crop_rect.y >= 0 &&
        crop_rect.x + crop_rect.width <= stitched_uncropped.cols &&
        crop_rect.y + crop_rect.height <= stitched_uncropped.rows) {
        *stitched_out = stitched_uncropped(crop_rect).clone();
    } else {
        *stitched_out = stitched_uncropped;
    }

    metrics_.warped_mean_luma = mean_luma(compensated_right);
    if (cv::countNonZero(overlap_mask_) > 0) {
        cv::Mat left_gray;
        cv::Mat right_gray;
        cv::Mat abs_diff;
        cv::cvtColor(canvas_left, left_gray, cv::COLOR_BGR2GRAY);
        cv::cvtColor(compensated_right, right_gray, cv::COLOR_BGR2GRAY);
        cv::absdiff(left_gray, right_gray, abs_diff);
        metrics_.overlap_diff_mean = cv::mean(abs_diff, overlap_mask_)[0];
    } else {
        metrics_.overlap_diff_mean = 0.0;
    }
    metrics_.cpu_blend_count += 1;
    metrics_.blend_mode = "narrow-seam-feather";
    if (used_dynamic_seam_out != nullptr) {
        *used_dynamic_seam_out = true;
    }
    return !stitched_out->empty();
}

void StitchEngine::update_seam_path_jitter_locked(const std::vector<int>& seam_path) {
    if (previous_seam_path_.size() != seam_path.size() || seam_path.empty()) {
        last_seam_path_jitter_px_ = 0.0;
        previous_seam_path_ = seam_path;
        return;
    }
    double total = 0.0;
    for (std::size_t index = 0; index < seam_path.size(); ++index) {
        total += std::abs(static_cast<double>(seam_path[index] - previous_seam_path_[index]));
    }
    last_seam_path_jitter_px_ = total / static_cast<double>(seam_path.size());
    previous_seam_path_ = seam_path;
}

struct ServicePairCandidate {
    hogak::input::BufferedFrameInfo left_info;
    hogak::input::BufferedFrameInfo right_info;
    hogak::input::FrameTimeDomain time_domain = hogak::input::FrameTimeDomain::kArrival;
    std::int64_t left_pair_time_ns = 0;
    std::int64_t right_pair_time_ns = 0;
    std::int64_t pair_time_ns = 0;
    std::int64_t scheduler_pair_time_ns = 0;
    std::int64_t arrival_skew_ns = 0;
    std::int64_t source_skew_ns = 0;
    std::int64_t skew_ns = std::numeric_limits<std::int64_t>::max();
    std::int64_t sync_overage_ns = 0;
    std::int64_t cadence_error_ns = std::numeric_limits<std::int64_t>::max();
    std::int64_t freshness_ns = 0;
    std::int64_t newest_ns = 0;
    std::int64_t reuse_age_ns = 0;
    int reuse_streak = 0;
    int advance_score = 0;
};

struct OffsetScore {
    bool valid = false;
    double combined_score = -1'000'000.0;
    double motion_corr = 0.0;
    double luma_corr = 0.0;
    double overlap_ratio = 0.0;
    double avg_gap_ms = 0.0;
    int matched_pairs = 0;
};

struct OffsetEstimateResult {
    bool valid = false;
    double offset_ms = 0.0;
    double confidence = 0.0;
    OffsetScore best_score{};
    double second_best_score = -1'000'000.0;
    double selection_score = -1'000'000.0;
};

std::int64_t wallclock_now_ns() {
    using clock = std::chrono::system_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        clock::now().time_since_epoch()).count();
}

std::string output_runtime_mode_hint(const std::string& runtime) {
    if (runtime == "ffmpeg") {
        return "ffmpeg-process";
    }
    if (runtime == "gpu-direct") {
        return "gpu-direct-requested";
    }
    if (runtime.empty()) {
        return "none";
    }
    return runtime;
}

namespace {

void increment_wait_paired_fresh_breakdown(
    EngineMetrics* metrics,
    bool left_missing_fresh,
    bool right_missing_fresh) {
    if (metrics == nullptr) {
        return;
    }
    if (left_missing_fresh && right_missing_fresh) {
        metrics->wait_paired_fresh_both_count += 1;
    } else if (left_missing_fresh) {
        metrics->wait_paired_fresh_left_count += 1;
    } else if (right_missing_fresh) {
        metrics->wait_paired_fresh_right_count += 1;
    }
}

}  // namespace

void StitchEngine::record_wait_paired_fresh_locked(bool left_missing_fresh, bool right_missing_fresh) {
    increment_wait_paired_fresh_breakdown(&metrics_, left_missing_fresh, right_missing_fresh);
    if (left_missing_fresh) {
        wait_paired_fresh_left_age_sum_ms_ += std::max(0.0, metrics_.left_age_ms);
    }
    if (right_missing_fresh) {
        wait_paired_fresh_right_age_sum_ms_ += std::max(0.0, metrics_.right_age_ms);
    }
}

StitchEngine::StitchEngine() = default;

StitchEngine::~StitchEngine() {
    stop();
}

void StitchEngine::invalidate_pair_selection_cache_locked() {
    left_buffered_infos_cache_.clear();
    right_buffered_infos_cache_.clear();
    left_buffered_infos_cache_state_ = BufferedInfoCacheState{};
    right_buffered_infos_cache_state_ = BufferedInfoCacheState{};
}

bool StitchEngine::select_pair_locked(
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    SelectedPair* pair_out) {
    if (pair_out == nullptr || !left_snapshot.has_frame || !right_snapshot.has_frame) {
        return false;
    }

    const auto refresh_cache_if_needed =
        [](hogak::input::FfmpegRtspReader* reader,
           const hogak::input::ReaderSnapshot& snapshot,
           BufferedInfoCacheState* cache_state,
           std::vector<hogak::input::BufferedFrameInfo>* cache) {
            if (reader == nullptr || cache_state == nullptr || cache == nullptr) {
                return;
            }
            const bool cache_matches_snapshot =
                cache_state->valid &&
                cache_state->latest_seq == snapshot.latest_seq &&
                cache_state->oldest_seq == snapshot.oldest_seq &&
                cache_state->buffered_frames == snapshot.buffered_frames;
            if (cache_matches_snapshot) {
                return;
            }
            reader->buffered_frame_infos(cache);
            cache_state->valid = true;
            cache_state->latest_seq = snapshot.latest_seq;
            cache_state->oldest_seq = snapshot.oldest_seq;
            cache_state->buffered_frames = snapshot.buffered_frames;
        };
    refresh_cache_if_needed(
        &input_state_.left_reader,
        left_snapshot,
        &left_buffered_infos_cache_state_,
        &left_buffered_infos_cache_);
    refresh_cache_if_needed(
        &input_state_.right_reader,
        right_snapshot,
        &right_buffered_infos_cache_state_,
        &right_buffered_infos_cache_);
    const PairSelectionContext scheduler_context{
        config_,
        metrics_,
        left_snapshot,
        right_snapshot,
        left_buffered_infos_cache_,
        right_buffered_infos_cache_,
    };
    return hogak::engine::select_pair(scheduler_context, &pair_scheduler_state_, pair_out);
}

void StitchEngine::clear_calibration_state_locked() {
    calibrated_ = false;
    runtime_geometry_ = RuntimeGeometryState{};
    runtime_geometry_source_path_.clear();
    homography_.release();
    homography_adjusted_.release();
    left_mask_template_.release();
    right_mask_template_.release();
    overlap_mask_.release();
    overlap_mask_roi_.release();
    only_left_mask_.release();
    only_right_mask_.release();
    weight_left_.release();
    weight_right_.release();
    weight_left_3c_.release();
    weight_right_3c_.release();
    previous_stitched_probe_gray_.release();
    latest_stitched_.release();
    previous_seam_path_.clear();
    gpu_left_nv12_y_.release();
    gpu_left_nv12_uv_.release();
    gpu_left_decoded_.release();
    gpu_left_input_.release();
    gpu_left_canvas_.release();
    gpu_stitched_.release();
    gpu_right_nv12_y_.release();
    gpu_right_nv12_uv_.release();
    gpu_right_decoded_.release();
    gpu_right_input_.release();
    gpu_right_aligned_.release();
    gpu_right_warped_.release();
    gpu_overlap_mask_.release();
    gpu_overlap_mask_roi_.release();
    gpu_only_left_mask_.release();
    gpu_only_right_mask_.release();
    gpu_weight_left_3c_.release();
    gpu_weight_right_3c_.release();
    gpu_weight_left_roi_.release();
    gpu_weight_right_roi_.release();
    gpu_left_f_.release();
    gpu_right_f_.release();
    gpu_left_part_.release();
    gpu_right_part_.release();
    gpu_overlap_f_.release();
    gpu_overlap_u8_.release();
    gpu_output_scaled_.release();
    gpu_output_canvas_.release();
    left_roi_ = cv::Rect();
    overlap_roi_ = cv::Rect();
    output_size_ = cv::Size();
    full_overlap_ = false;
    metrics_.calibrated = false;
    cached_left_cpu_seq_ = 0;
    cached_right_cpu_seq_ = 0;
    cached_left_gpu_input_seq_ = 0;
    cached_right_gpu_input_seq_ = 0;
    cached_left_canvas_seq_ = 0;
    cached_right_warped_seq_ = 0;
    cached_left_cpu_frame_.release();
    cached_right_cpu_frame_.release();
    cached_left_canvas_cpu_.release();
    cached_right_warped_cpu_.release();
    metrics_.output_width = 0;
    metrics_.output_height = 0;
    metrics_.production_output_width = 0;
    metrics_.production_output_height = 0;
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.overlap_diff_mean = 0.0;
    metrics_.geometry_mode = "virtual-center-rectilinear-rigid";
    metrics_.alignment_mode = "rigid";
    metrics_.seam_mode = "fixed-seam";
    metrics_.exposure_mode = "off";
    metrics_.geometry_artifact_path.clear();
    metrics_.geometry_artifact_model = "virtual-center-rectilinear-rigid";
    metrics_.residual_alignment_error_px = 0.0;
    metrics_.seam_path_jitter_px = 0.0;
    metrics_.exposure_gain = 1.0;
    metrics_.exposure_bias = 0.0;
    metrics_.blend_mode = "simple-feather";
    last_exposure_gain_ = 1.0;
    last_exposure_bias_ = 0.0;
    last_residual_alignment_error_px_ = 0.0;
    last_seam_path_jitter_px_ = 0.0;
    metrics_.left_motion_mean = 0.0;
    metrics_.right_motion_mean = 0.0;
    metrics_.stitched_motion_mean = 0.0;
    metrics_.stitched_mean_luma = 0.0;
    metrics_.warped_mean_luma = 0.0;
    metrics_.only_left_pixels = 0;
    metrics_.only_right_pixels = 0;
    metrics_.overlap_pixels = 0;
}

bool StitchEngine::restart_reader_locked(bool left_reader, const char* reason) {
    const auto now_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count();
    auto& last_restart_ns = left_reader ? last_left_reader_restart_ns_ : last_right_reader_restart_ns_;
    if (last_restart_ns > 0 &&
        static_cast<double>(now_ns - last_restart_ns) / 1'000'000'000.0 < kReaderRestartCooldownSec) {
        return false;
    }

    const auto ffmpeg_bin = config_.ffmpeg_bin;
    if (left_reader) {
        input_state_.left_reader.stop();
        invalidate_pair_selection_cache_locked();
        const bool ok = input_state_.left_reader.start(config_.left, ffmpeg_bin, config_.input_runtime, false);
        if (ok) {
            left_reader_restart_count_ += 1;
            last_left_reader_restart_ns_ = now_ns;
            metrics_.status = std::string("left reader restarted: ") + reason;
        }
        return ok;
    }

    input_state_.right_reader.stop();
    invalidate_pair_selection_cache_locked();
    const bool ok = input_state_.right_reader.start(config_.right, ffmpeg_bin, config_.input_runtime, false);
    if (ok) {
        right_reader_restart_count_ += 1;
        last_right_reader_restart_ns_ = now_ns;
        metrics_.status = std::string("right reader restarted: ") + reason;
    }
    return ok;
}

bool StitchEngine::start(const EngineConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    metrics_ = EngineMetrics{};
    metrics_.status = "starting";
    metrics_.sync_pair_mode = config.sync_pair_mode;
    metrics_.gpu_enabled = (config.gpu_mode != "off");
    metrics_.gpu_reason = metrics_.gpu_enabled ? "native runtime stitch pipeline" : "gpu disabled by config";
    metrics_.geometry_mode = "virtual-center-rectilinear-rigid";
    metrics_.output_target = config.output.target;
    metrics_.output_command_line.clear();
    metrics_.output_runtime_mode = output_runtime_mode_hint(config.output.runtime);
    metrics_.production_output_target = config.production_output.target;
    metrics_.production_output_command_line.clear();
    metrics_.production_output_runtime_mode = output_runtime_mode_hint(config.production_output.runtime);
    pair_scheduler_state_ = PairSchedulerState{};
    last_worker_timestamp_ns_ = 0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
    last_metrics_refresh_ns_ = 0;
    left_reader_restart_count_ = 0;
    right_reader_restart_count_ = 0;
    last_left_reader_restart_ns_ = 0;
    last_right_reader_restart_ns_ = 0;
    clear_calibration_state_locked();
    load_runtime_geometry_locked();
    invalidate_pair_selection_cache_locked();
    output_writer_.reset();
    production_output_writer_.reset();
    std::string gpu_only_reason;
    if (!validate_gpu_only_config(config, &gpu_only_reason)) {
        metrics_.status = "gpu_only_blocked";
        metrics_.gpu_reason = gpu_only_reason;
        metrics_.gpu_feature_enabled = false;
        metrics_.gpu_feature_reason = gpu_only_reason;
        running_.store(false);
        return false;
    }
    gpu_available_ = metrics_.gpu_enabled && (cv::cuda::getCudaEnabledDeviceCount() > config.gpu_device);
    gpu_nv12_input_supported_ = true;
    if (metrics_.gpu_enabled && !gpu_available_) {
        metrics_.gpu_reason = "cuda requested but unavailable";
    }

    running_.store(true);
    const auto ffmpeg_bin = config.ffmpeg_bin;
    const bool left_ok = !config.left.url.empty() &&
        input_state_.left_reader.start(config.left, ffmpeg_bin, config.input_runtime, false);
    const bool right_ok = !config.right.url.empty() &&
        input_state_.right_reader.start(config.right, ffmpeg_bin, config.input_runtime, false);
    if (!left_ok || !right_ok) {
        input_state_.left_reader.stop();
        input_state_.right_reader.stop();
        metrics_.status = "reader_start_failed";
        running_.store(false);
        return false;
    }
    metrics_.status = "waiting for both streams";
    return true;
}

void StitchEngine::stop() {
    std::lock_guard<std::mutex> lock(mutex_);
    running_.store(false);
    if (output_writer_ != nullptr) {
        output_writer_->stop();
        output_writer_.reset();
    }
    if (production_output_writer_ != nullptr) {
        production_output_writer_->stop();
        production_output_writer_.reset();
    }
    input_state_.left_reader.stop();
    input_state_.right_reader.stop();
    invalidate_pair_selection_cache_locked();
    metrics_.status = "stopped";
    metrics_.stitch_actual_fps = 0.0;
    metrics_.output_written_fps = 0.0;
    metrics_.production_output_written_fps = 0.0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
    pair_scheduler_state_ = PairSchedulerState{};
    last_metrics_refresh_ns_ = 0;
}

bool StitchEngine::reload_config(const EngineConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::string gpu_only_reason;
    if (!validate_gpu_only_config(config, &gpu_only_reason)) {
        metrics_.status = "gpu_only_blocked";
        metrics_.gpu_reason = gpu_only_reason;
        metrics_.gpu_feature_enabled = false;
        metrics_.gpu_feature_reason = gpu_only_reason;
        return false;
    }
    const bool restart_readers =
        (config.left.url != config_.left.url) ||
        (config.right.url != config_.right.url) ||
        (config.left.transport != config_.left.transport) ||
        (config.right.transport != config_.right.transport) ||
        (config.left.timeout_sec != config_.left.timeout_sec) ||
        (config.right.timeout_sec != config_.right.timeout_sec) ||
        (config.left.reconnect_cooldown_sec != config_.left.reconnect_cooldown_sec) ||
        (config.right.reconnect_cooldown_sec != config_.right.reconnect_cooldown_sec) ||
        (config.left.video_codec != config_.left.video_codec) ||
        (config.right.video_codec != config_.right.video_codec) ||
        (config.left.input_pipe_format != config_.left.input_pipe_format) ||
        (config.right.input_pipe_format != config_.right.input_pipe_format) ||
        (config.left.width != config_.left.width) ||
        (config.left.height != config_.left.height) ||
        (config.left.max_buffered_frames != config_.left.max_buffered_frames) ||
        (config.right.width != config_.right.width) ||
        (config.right.height != config_.right.height) ||
        (config.right.max_buffered_frames != config_.right.max_buffered_frames) ||
        (config.input_runtime != config_.input_runtime) ||
        (config.ffmpeg_bin != config_.ffmpeg_bin);

    if (output_writer_ != nullptr) {
        output_writer_->stop();
        output_writer_.reset();
    }
    if (production_output_writer_ != nullptr) {
        production_output_writer_->stop();
        production_output_writer_.reset();
    }
    if (restart_readers) {
        input_state_.left_reader.stop();
        input_state_.right_reader.stop();
        invalidate_pair_selection_cache_locked();
    }

    config_ = config;
    clear_calibration_state_locked();
    pair_scheduler_state_ = PairSchedulerState{};
    last_worker_timestamp_ns_ = 0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
    last_metrics_refresh_ns_ = 0;
    metrics_.stitch_actual_fps = 0.0;
    metrics_.output_written_fps = 0.0;
    metrics_.production_output_written_fps = 0.0;
    metrics_.gpu_enabled = (config.gpu_mode != "off");
    metrics_.sync_pair_mode = config.sync_pair_mode;
    gpu_available_ = metrics_.gpu_enabled && (cv::cuda::getCudaEnabledDeviceCount() > config.gpu_device);
    gpu_nv12_input_supported_ = true;
    metrics_.gpu_reason = gpu_available_ ? "reloaded config" : "gpu disabled or unavailable";
    metrics_.output_last_error.clear();
    metrics_.output_command_line.clear();
    metrics_.output_effective_codec.clear();
    metrics_.output_runtime_mode = output_runtime_mode_hint(config.output.runtime);
    metrics_.output_target = config.output.target;
    metrics_.production_output_last_error.clear();
    metrics_.production_output_command_line.clear();
    metrics_.production_output_effective_codec.clear();
    metrics_.production_output_runtime_mode = output_runtime_mode_hint(config.production_output.runtime);
    metrics_.production_output_target = config.production_output.target;
    metrics_.status = "config reloaded";
    load_runtime_geometry_locked();
    invalidate_pair_selection_cache_locked();

    if (restart_readers) {
        const auto ffmpeg_bin = config.ffmpeg_bin;
        const bool left_ok = !config.left.url.empty() &&
            input_state_.left_reader.start(config.left, ffmpeg_bin, config.input_runtime, false);
        const bool right_ok = !config.right.url.empty() &&
            input_state_.right_reader.start(config.right, ffmpeg_bin, config.input_runtime, false);
        if (!left_ok || !right_ok) {
            input_state_.left_reader.stop();
            input_state_.right_reader.stop();
            metrics_.status = "reader_restart_failed";
            return false;
        }
    }

    return true;
}

void StitchEngine::reset_calibration() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (output_writer_ != nullptr) {
        output_writer_->stop();
        output_writer_.reset();
    }
    if (production_output_writer_ != nullptr) {
        production_output_writer_->stop();
        production_output_writer_.reset();
    }
    clear_calibration_state_locked();
    load_runtime_geometry_locked();
    invalidate_pair_selection_cache_locked();
    metrics_.output_last_error.clear();
    metrics_.output_command_line.clear();
    metrics_.output_effective_codec.clear();
    metrics_.output_runtime_mode = output_runtime_mode_hint(config_.output.runtime);
    metrics_.production_output_last_error.clear();
    metrics_.production_output_command_line.clear();
    metrics_.production_output_effective_codec.clear();
    metrics_.production_output_runtime_mode = output_runtime_mode_hint(config_.production_output.runtime);
    metrics_.status = "calibration reset";
    pair_scheduler_state_ = PairSchedulerState{};
    last_worker_timestamp_ns_ = 0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
    last_metrics_refresh_ns_ = 0;
    metrics_.stitch_actual_fps = 0.0;
    metrics_.output_written_fps = 0.0;
    metrics_.production_output_written_fps = 0.0;
}

EngineConfig StitchEngine::current_config() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return config_;
}

EngineMetrics StitchEngine::snapshot_metrics() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return metrics_;
}

bool StitchEngine::running() const noexcept {
    return running_.load();
}

void StitchEngine::tick() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!running_.load()) {
        return;
    }
    update_metrics_locked();
}

bool StitchEngine::load_runtime_geometry_from_file(const std::string& path, RuntimeGeometryState* state) {
    if (state == nullptr || path.empty()) {
        return false;
    }

    RuntimeGeometryArtifactData artifact;
    if (!load_runtime_geometry_artifact_from_file(path, &artifact)) {
        return false;
    }

    state->model = artifact.model.empty() ? "virtual-center-rectilinear" : artifact.model;
    state->alignment_model = artifact.alignment_model.empty() ? "rigid" : artifact.alignment_model;
    state->residual_model = artifact.residual_model.empty() ? "rigid" : artifact.residual_model;
    if (state->model == "virtual_center_rectilinear") {
        state->model = "virtual-center-rectilinear";
    }
    if (state->model != "virtual-center-rectilinear") {
        return false;
    }
    if (state->residual_model != "rigid") {
        return false;
    }
    state->artifact_path = artifact.artifact_path.empty() ? path : artifact.artifact_path;
    state->output_size = artifact.output_size;
    state->alignment_matrix = artifact.alignment_matrix.empty() ? cv::Mat::eye(3, 3, CV_64F) : artifact.alignment_matrix.clone();
    state->left_projection_model = artifact.left_projection_model;
    state->right_projection_model = artifact.right_projection_model;
    state->left_focal_px = artifact.left_focal_px;
    state->left_center_x = artifact.left_center_x;
    state->left_center_y = artifact.left_center_y;
    state->right_focal_px = artifact.right_focal_px;
    state->right_center_x = artifact.right_center_x;
    state->right_center_y = artifact.right_center_y;
    state->left_virtual_focal_px = artifact.left_virtual_focal_px;
    state->left_virtual_center_x = artifact.left_virtual_center_x;
    state->left_virtual_center_y = artifact.left_virtual_center_y;
    state->right_virtual_focal_px = artifact.right_virtual_focal_px;
    state->right_virtual_center_x = artifact.right_virtual_center_x;
    state->right_virtual_center_y = artifact.right_virtual_center_y;
    state->left_virtual_to_source_rotation = artifact.left_virtual_to_source_rotation.clone();
    state->right_virtual_to_source_rotation = artifact.right_virtual_to_source_rotation.clone();
    state->mesh_fallback_used = false;
    state->mesh_grid_cols = 0;
    state->mesh_grid_rows = 0;
    state->mesh_control_displacement_x.release();
    state->mesh_control_displacement_y.release();
    state->mesh_max_displacement_px = 0.0;
    state->mesh_max_local_scale_drift = 0.0;
    state->mesh_max_local_rotation_drift = 0.0;
    state->mesh_enabled = false;
    state->residual_alignment_error_px = artifact.residual_alignment_error_px;
    state->seam_transition_px = std::max(2, artifact.seam_transition_px);
    state->seam_smoothness_penalty = std::max(0.0, artifact.seam_smoothness_penalty);
    state->seam_temporal_penalty = std::max(0.0, artifact.seam_temporal_penalty);
    state->exposure_enabled = artifact.exposure_enabled;
    state->exposure_gain_min = std::max(0.0, artifact.exposure_gain_min);
    state->exposure_gain_max = std::max(state->exposure_gain_min, artifact.exposure_gain_max);
    state->exposure_bias_abs_max = std::max(0.0, artifact.exposure_bias_abs_max);
    state->crop_enabled = artifact.crop_enabled;
    state->crop_rect = artifact.crop_rect;
    return true;
}

bool StitchEngine::runtime_geometry_requests_mesh_locked() const {
    return false;
}

bool StitchEngine::runtime_geometry_mesh_active_locked() const {
    return false;
}

std::string StitchEngine::runtime_geometry_public_model_locked() const {
    return "virtual-center-rectilinear-rigid";
}

std::string StitchEngine::runtime_geometry_artifact_truth_locked() const {
    return "virtual-center-rectilinear-rigid";
}

std::string StitchEngine::runtime_alignment_truth_locked() const {
    return "rigid";
}

std::string StitchEngine::runtime_seam_truth_locked() const {
    return "fixed-seam";
}

std::string StitchEngine::runtime_exposure_truth_locked() const {
    return "off";
}

bool StitchEngine::load_runtime_geometry_locked() {
    runtime_geometry_ = RuntimeGeometryState{};
    runtime_geometry_source_path_.clear();

    const std::string candidate_path = runtime_geometry_artifact_candidate_path(config_);
    if (!candidate_path.empty() && load_runtime_geometry_from_file(candidate_path, &runtime_geometry_)) {
        runtime_geometry_source_path_ = runtime_geometry_.artifact_path.empty() ? candidate_path : runtime_geometry_.artifact_path;
        last_residual_alignment_error_px_ = runtime_geometry_.residual_alignment_error_px;
        apply_runtime_geometry_to_metrics_locked();
        return true;
    }

    apply_runtime_geometry_to_metrics_locked();
    return false;
}

bool StitchEngine::prepare_runtime_geometry_locked(const cv::Size& left_size, const cv::Size& right_size) {
    if (left_size.width <= 0 || left_size.height <= 0 || right_size.width <= 0 || right_size.height <= 0) {
        return false;
    }

    if (runtime_geometry_.artifact_path.empty() && !load_runtime_geometry_locked()) {
        return false;
    }
    const bool rectilinear_runtime = runtime_geometry_.model == "virtual-center-rectilinear";
    const bool rectilinear_mesh_requested = rectilinear_runtime && runtime_geometry_requests_mesh_locked();
    if (!rectilinear_runtime) {
        apply_runtime_geometry_to_metrics_locked();
        metrics_.status = "unsupported_runtime_geometry_model";
        return false;
    }
    if (rectilinear_runtime && !gpu_available_) {
        apply_runtime_geometry_to_metrics_locked();
        metrics_.status = "virtual_center_rectilinear_requires_gpu";
        metrics_.gpu_reason = "virtual-center-rectilinear requires GPU path";
        return false;
    }
    cv::Size rectilinear_output_size = runtime_geometry_.output_size;
    if (rectilinear_runtime && (rectilinear_output_size.width <= 0 || rectilinear_output_size.height <= 0)) {
        rectilinear_output_size = left_size;
    }
    if (!build_virtual_center_rectilinear_maps_locked(
            rectilinear_output_size,
            runtime_geometry_.left_focal_px,
            runtime_geometry_.left_center_x,
            runtime_geometry_.left_center_y,
            runtime_geometry_.left_virtual_focal_px,
            runtime_geometry_.left_virtual_center_x,
            runtime_geometry_.left_virtual_center_y,
            runtime_geometry_.left_virtual_to_source_rotation,
            &runtime_geometry_.rectilinear_left_map_x,
            &runtime_geometry_.rectilinear_left_map_y)) {
        apply_runtime_geometry_to_metrics_locked();
        metrics_.status = "virtual_center_rectilinear_left_map_build_failed";
        return false;
    }
    if (!build_virtual_center_rectilinear_maps_locked(
            rectilinear_output_size,
            runtime_geometry_.right_focal_px,
            runtime_geometry_.right_center_x,
            runtime_geometry_.right_center_y,
            runtime_geometry_.right_virtual_focal_px,
            runtime_geometry_.right_virtual_center_x,
            runtime_geometry_.right_virtual_center_y,
            runtime_geometry_.right_virtual_to_source_rotation,
            &runtime_geometry_.rectilinear_right_map_x,
            &runtime_geometry_.rectilinear_right_map_y)) {
        apply_runtime_geometry_to_metrics_locked();
        metrics_.status = "virtual_center_rectilinear_right_map_build_failed";
        return false;
    }
    if (rectilinear_mesh_requested && runtime_geometry_.mesh_fallback_used) {
        apply_runtime_geometry_to_metrics_locked();
        metrics_.status = "virtual_center_rectilinear_mesh_blocked_degraded";
        metrics_.gpu_reason = "virtual-center mesh artifact is marked degraded-to-rigid and cannot be launched as mesh";
        return false;
    }
    if (runtime_geometry_.mesh_enabled) {
        if (!build_runtime_mesh_maps_locked(
                rectilinear_output_size,
                runtime_geometry_.mesh_control_displacement_x,
                runtime_geometry_.mesh_control_displacement_y,
                &runtime_geometry_.mesh_map_x,
                &runtime_geometry_.mesh_map_y)) {
            apply_runtime_geometry_to_metrics_locked();
            metrics_.status = "virtual_center_rectilinear_mesh_map_missing";
            metrics_.gpu_reason = "virtual-center mesh artifact is missing a valid runtime mesh map";
            return false;
        }
    }
    if (gpu_available_) {
        try {
            runtime_geometry_.rectilinear_left_map_x_gpu.upload(runtime_geometry_.rectilinear_left_map_x);
            runtime_geometry_.rectilinear_left_map_y_gpu.upload(runtime_geometry_.rectilinear_left_map_y);
            runtime_geometry_.rectilinear_right_map_x_gpu.upload(runtime_geometry_.rectilinear_right_map_x);
            runtime_geometry_.rectilinear_right_map_y_gpu.upload(runtime_geometry_.rectilinear_right_map_y);
            if (runtime_geometry_.mesh_enabled) {
                runtime_geometry_.mesh_map_x_gpu.upload(runtime_geometry_.mesh_map_x);
                runtime_geometry_.mesh_map_y_gpu.upload(runtime_geometry_.mesh_map_y);
            }
        } catch (const cv::Exception& e) {
            metrics_.gpu_errors += 1;
            metrics_.status = "virtual_center_rectilinear_map_upload_failed";
            metrics_.gpu_reason = std::string("cuda rectilinear map upload failed: ") + e.what();
            return false;
        }
    }

    cv::Size output_size;
    cv::Rect left_roi;
    cv::Rect overlap_roi_unused;
    cv::Mat adjusted_affine;
    output_size = rectilinear_output_size;
    left_roi = cv::Rect(0, 0, output_size.width, output_size.height);
    overlap_roi_unused = cv::Rect();
    adjusted_affine = runtime_geometry_.alignment_matrix.empty()
        ? cv::Mat::eye(3, 3, CV_64F)
        : runtime_geometry_.alignment_matrix.clone();

    runtime_geometry_.output_size = output_size;
    runtime_geometry_.alignment_matrix = adjusted_affine;
    last_residual_alignment_error_px_ = runtime_geometry_.residual_alignment_error_px;
    left_roi_ = left_roi;
    output_size_ = output_size;
    homography_ = runtime_geometry_.alignment_matrix.clone();
    homography_adjusted_ = runtime_geometry_.alignment_matrix.clone();

    cv::Mat left_mask_source(left_size, CV_8UC1, cv::Scalar(255));
    cv::remap(
        left_mask_source,
        left_mask_template_,
        runtime_geometry_.rectilinear_left_map_x,
        runtime_geometry_.rectilinear_left_map_y,
        cv::INTER_NEAREST,
        cv::BORDER_CONSTANT,
        cv::Scalar());
    cv::Mat right_mask_source(right_size, CV_8UC1, cv::Scalar(255));
    cv::Mat right_projected_mask;
    cv::remap(
        right_mask_source,
        right_projected_mask,
        runtime_geometry_.rectilinear_right_map_x,
        runtime_geometry_.rectilinear_right_map_y,
        cv::INTER_NEAREST,
        cv::BORDER_CONSTANT,
        cv::Scalar());
    cv::warpPerspective(
        right_projected_mask,
        right_mask_template_,
        runtime_geometry_.alignment_matrix,
        output_size_,
        cv::INTER_NEAREST,
        cv::BORDER_CONSTANT);
    if (runtime_geometry_.mesh_enabled) {
        cv::Mat right_mask_meshed;
        cv::remap(
            right_mask_template_,
            right_mask_meshed,
            runtime_geometry_.mesh_map_x,
            runtime_geometry_.mesh_map_y,
            cv::INTER_NEAREST,
            cv::BORDER_CONSTANT,
            cv::Scalar());
        right_mask_template_ = right_mask_meshed;
    }

    cv::Mat right_mask_not;
    cv::Mat left_mask_not;
    cv::bitwise_and(left_mask_template_, right_mask_template_, overlap_mask_);
    cv::bitwise_not(right_mask_template_, right_mask_not);
    cv::bitwise_not(left_mask_template_, left_mask_not);
    cv::bitwise_and(left_mask_template_, right_mask_not, only_left_mask_);
    cv::bitwise_and(right_mask_template_, left_mask_not, only_right_mask_);
    metrics_.only_left_pixels = cv::countNonZero(only_left_mask_);
    metrics_.only_right_pixels = cv::countNonZero(only_right_mask_);
    metrics_.overlap_pixels = cv::countNonZero(overlap_mask_);
    full_overlap_ = (metrics_.overlap_pixels > 0) &&
                    (metrics_.only_left_pixels == 0) &&
                    (metrics_.only_right_pixels == 0);

    if (metrics_.overlap_pixels > 0) {
        overlap_roi_ = cv::boundingRect(overlap_mask_);
        overlap_mask_roi_ = overlap_mask_(overlap_roi_).clone();
        build_seam_blend_weights(overlap_mask_, overlap_roi_, &weight_left_, &weight_right_);
        std::vector<cv::Mat> wl(3, weight_left_);
        std::vector<cv::Mat> wr(3, weight_right_);
        cv::merge(wl, weight_left_3c_);
        cv::merge(wr, weight_right_3c_);
    } else {
        overlap_roi_ = cv::Rect();
        overlap_mask_roi_.release();
        weight_left_ = cv::Mat::zeros(output_size_, CV_32FC1);
        weight_right_ = cv::Mat::zeros(output_size_, CV_32FC1);
        weight_left_3c_ = cv::Mat::zeros(output_size_, CV_32FC3);
        weight_right_3c_ = cv::Mat::zeros(output_size_, CV_32FC3);
    }

    if (gpu_available_) {
        try {
            gpu_overlap_mask_.upload(overlap_mask_);
            gpu_only_left_mask_.upload(only_left_mask_);
            gpu_only_right_mask_.upload(only_right_mask_);
            if (overlap_roi_.area() > 0) {
                gpu_overlap_mask_roi_.upload(overlap_mask_roi_);
                gpu_weight_left_roi_.upload(weight_left_(overlap_roi_));
                gpu_weight_right_roi_.upload(weight_right_(overlap_roi_));
            }
        } catch (const cv::Exception& e) {
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = std::string("cuda calibration upload failed: ") + e.what();
        }
    }

    metrics_.geometry_mode = runtime_geometry_public_model_locked();
    metrics_.alignment_mode = runtime_alignment_truth_locked();
    metrics_.seam_mode = runtime_seam_truth_locked();
    metrics_.exposure_mode = runtime_exposure_truth_locked();
    metrics_.geometry_artifact_path = runtime_geometry_source_path_;
    metrics_.geometry_artifact_model = runtime_geometry_artifact_truth_locked();
    metrics_.residual_alignment_error_px = last_residual_alignment_error_px_;
    metrics_.seam_path_jitter_px = last_seam_path_jitter_px_;
    metrics_.exposure_gain = last_exposure_gain_;
    metrics_.exposure_bias = last_exposure_bias_;
    metrics_.blend_mode = "simple-feather";
    return true;
}

void StitchEngine::apply_runtime_geometry_to_metrics_locked() {
    metrics_.geometry_mode = runtime_geometry_public_model_locked();
    metrics_.alignment_mode = runtime_alignment_truth_locked();
    metrics_.seam_mode = runtime_seam_truth_locked();
    metrics_.exposure_mode = runtime_exposure_truth_locked();
    metrics_.geometry_artifact_path = runtime_geometry_source_path_;
    metrics_.geometry_artifact_model = runtime_geometry_artifact_truth_locked();
    metrics_.residual_alignment_error_px = last_residual_alignment_error_px_;
    metrics_.seam_path_jitter_px = last_seam_path_jitter_px_;
    metrics_.exposure_gain = last_exposure_gain_;
    metrics_.exposure_bias = last_exposure_bias_;
    metrics_.blend_mode = "simple-feather";
}

bool StitchEngine::ensure_calibration_locked(const cv::Size& left_size, const cv::Size& right_size) {
    if (calibrated_) {
        return true;
    }

    const bool runtime_geometry_prepare_requested =
        runtime_geometry_.model == "virtual-center-rectilinear";
    if (runtime_geometry_prepare_requested && prepare_runtime_geometry_locked(left_size, right_size)) {
        calibrated_ = true;
        metrics_.calibrated = true;
        metrics_.output_width = (config_.output.width > 0) ? config_.output.width : output_size_.width;
        metrics_.output_height = (config_.output.height > 0) ? config_.output.height : output_size_.height;
        if (config_.production_output.runtime != "none" && !config_.production_output.target.empty()) {
            metrics_.production_output_width =
                (config_.production_output.width > 0) ? config_.production_output.width : output_size_.width;
            metrics_.production_output_height =
                (config_.production_output.height > 0) ? config_.production_output.height : output_size_.height;
        } else {
            metrics_.production_output_width = 0;
            metrics_.production_output_height = 0;
        }
        apply_runtime_geometry_to_metrics_locked();
        metrics_.status = "calibrated";
        return true;
    }
    apply_runtime_geometry_to_metrics_locked();
    if (metrics_.status.empty() || metrics_.status == "config reloaded") {
        metrics_.status = "virtual_center_rectilinear_prepare_failed";
    }
    return false;
}

bool StitchEngine::prepare_execution_inputs_locked(
    const SelectedPair& pair,
    double output_scale,
    PreparedExecutionInput* prepared_out) {
    if (prepared_out == nullptr) {
        return false;
    }
    prepared_out->left_frame.release();
    prepared_out->right_frame.release();
    prepared_out->left_raw_input = nullptr;
    prepared_out->right_raw_input = nullptr;
    prepared_out->gpu_nv12_fast_path = false;

    const bool gpu_only_mode = gpu_only_mode_enabled(config_);
    const bool gpu_nv12_input_available =
        gpu_available_ &&
        input_pipe_format_is_nv12(config_.left) &&
        input_pipe_format_is_nv12(config_.right);
    prepared_out->gpu_nv12_fast_path =
        gpu_nv12_input_available &&
        gpu_nv12_input_supported_;
    if (gpu_only_mode && !gpu_nv12_input_available) {
        metrics_.status = "gpu_only_input_unavailable";
        metrics_.gpu_reason = "gpu-only mode requires NV12 fast-path input without CPU staging";
        return false;
    }
    if (gpu_nv12_input_available) {
        prepared_out->left_raw_input = &pair.left_frame;
        prepared_out->right_raw_input = &pair.right_frame;
    }
    if (prepared_out->gpu_nv12_fast_path) {
        return true;
    }

    if (pair.left_seq > 0 && cached_left_cpu_seq_ == pair.left_seq && !cached_left_cpu_frame_.empty()) {
        prepared_out->left_frame = cached_left_cpu_frame_;
    } else {
        cv::Mat left_decoded = decode_input_frame_for_stitch(pair.left_frame, config_.left);
        if (left_decoded.empty()) {
            metrics_.status = "input decode failed";
            return false;
        }
        prepared_out->left_frame = resize_frame_for_runtime(left_decoded, output_scale);
        cached_left_cpu_frame_ = prepared_out->left_frame;
        cached_left_cpu_seq_ = pair.left_seq;
    }
    if (pair.right_seq > 0 && cached_right_cpu_seq_ == pair.right_seq && !cached_right_cpu_frame_.empty()) {
        prepared_out->right_frame = cached_right_cpu_frame_;
    } else {
        cv::Mat right_decoded = decode_input_frame_for_stitch(pair.right_frame, config_.right);
        if (right_decoded.empty()) {
            metrics_.status = "input decode failed";
            return false;
        }
        prepared_out->right_frame = resize_frame_for_runtime(right_decoded, output_scale);
        cached_right_cpu_frame_ = prepared_out->right_frame;
        cached_right_cpu_seq_ = pair.right_seq;
    }
    return true;
}

}  // namespace hogak::engine
