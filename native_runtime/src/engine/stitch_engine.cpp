#include "engine/stitch_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cctype>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudawarping.hpp>
#include <opencv2/imgproc.hpp>

#include "input/ffmpeg_rtsp_reader.h"

namespace hogak::engine {

namespace {

input::FfmpegRtspReader g_left_reader;
input::FfmpegRtspReader g_right_reader;

double fps_from_period_ns(std::int64_t delta_ns) {
    if (delta_ns <= 0) {
        return 0.0;
    }
    return 1'000'000'000.0 / static_cast<double>(delta_ns);
}

std::string sanitize_numeric_text(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (const char ch : text) {
        if (std::isdigit(static_cast<unsigned char>(ch)) || ch == '-' || ch == '+' || ch == '.' || ch == 'e' || ch == 'E') {
            out.push_back(ch);
        } else {
            out.push_back(' ');
        }
    }
    return out;
}

bool load_homography_from_file(const std::string& path, cv::Mat* homography_out) {
    if (homography_out == nullptr) {
        return false;
    }

    cv::FileStorage fs(path, cv::FileStorage::READ | cv::FileStorage::FORMAT_AUTO);
    if (fs.isOpened()) {
        cv::Mat mat;
        fs["homography"] >> mat;
        if (mat.empty()) {
            fs.root() >> mat;
        }
        if (!mat.empty() && mat.rows == 3 && mat.cols == 3) {
            mat.convertTo(*homography_out, CV_64F);
            return true;
        }
    }

    std::ifstream file(path);
    if (!file.is_open()) {
        return false;
    }
    std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    std::istringstream values(sanitize_numeric_text(text));
    std::array<double, 9> data{};
    for (double& value : data) {
        if (!(values >> value)) {
            return false;
        }
    }
    *homography_out = cv::Mat(3, 3, CV_64F, data.data()).clone();
    return true;
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

StitchEngine::StitchEngine() = default;

StitchEngine::~StitchEngine() {
    stop();
}

bool StitchEngine::start(const EngineConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    metrics_ = EngineMetrics{};
    metrics_.status = "starting";
    metrics_.gpu_enabled = (config.gpu_mode != "off");
    metrics_.gpu_reason = metrics_.gpu_enabled ? "native runtime stitch pipeline" : "gpu disabled by config";
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_worker_timestamp_ns_ = 0;
    calibrated_ = false;
    homography_.release();
    homography_adjusted_.release();
    left_mask_template_.release();
    right_mask_template_.release();
    overlap_mask_.release();
    only_left_mask_.release();
    only_right_mask_.release();
    weight_left_.release();
    weight_right_.release();
    weight_left_3c_.release();
    weight_right_3c_.release();
    latest_stitched_.release();
    gpu_right_input_.release();
    gpu_right_warped_.release();
    gpu_available_ = metrics_.gpu_enabled && (cv::cuda::getCudaEnabledDeviceCount() > config.gpu_device);
    if (metrics_.gpu_enabled && !gpu_available_) {
        metrics_.gpu_reason = "cuda requested but unavailable";
    }

    running_.store(true);
    const auto ffmpeg_bin = config.ffmpeg_bin;
    const bool left_ok = !config.left.url.empty() &&
        g_left_reader.start(config.left, ffmpeg_bin, config.input_runtime);
    const bool right_ok = !config.right.url.empty() &&
        g_right_reader.start(config.right, ffmpeg_bin, config.input_runtime);
    if (!left_ok || !right_ok) {
        metrics_.status = "reader_start_failed";
        running_.store(false);
        return false;
    }
    metrics_.status = "waiting for both streams";
    return true;
}

void StitchEngine::stop() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!running_.load()) {
        return;
    }
    running_.store(false);
    g_left_reader.stop();
    g_right_reader.stop();
    metrics_.status = "stopped";
}

bool StitchEngine::reload_config(const EngineConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    calibrated_ = false;
    metrics_.gpu_enabled = (config.gpu_mode != "off");
    gpu_available_ = metrics_.gpu_enabled && (cv::cuda::getCudaEnabledDeviceCount() > config.gpu_device);
    metrics_.gpu_reason = gpu_available_ ? "reloaded config" : "gpu disabled or unavailable";
    return true;
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

bool StitchEngine::load_homography_locked(cv::Mat* homography_out) {
    if (homography_out == nullptr) {
        return false;
    }
    if (config_.homography_file.empty()) {
        *homography_out = cv::Mat::eye(3, 3, CV_64F);
        return true;
    }
    return load_homography_from_file(config_.homography_file, homography_out);
}

bool StitchEngine::ensure_calibration_locked(const cv::Mat& left_frame, const cv::Mat& right_frame) {
    if (calibrated_) {
        return true;
    }

    cv::Mat homography;
    if (!load_homography_locked(&homography)) {
        metrics_.status = "homography_load_failed";
        return false;
    }

    cv::Mat adjusted_h;
    cv::Size output_size;
    cv::Rect left_roi;
    if (!prepare_warp_plan(
            left_frame.size(),
            right_frame.size(),
            homography,
            config_.process_scale <= 0.0 ? 4.0 : 4.0,
            40'000'000,
            &adjusted_h,
            &output_size,
            &left_roi)) {
        metrics_.status = "warp_plan_failed";
        return false;
    }

    homography_ = homography;
    homography_adjusted_ = adjusted_h;
    output_size_ = output_size;
    left_roi_ = left_roi;

    left_mask_template_ = cv::Mat::zeros(output_size_, CV_8UC1);
    left_mask_template_(left_roi_).setTo(cv::Scalar(255));

    cv::Mat right_mask_source(right_frame.size(), CV_8UC1, cv::Scalar(255));
    cv::warpPerspective(
        right_mask_source,
        right_mask_template_,
        homography_adjusted_,
        output_size_,
        cv::INTER_NEAREST,
        cv::BORDER_CONSTANT);

    cv::Mat right_mask_not;
    cv::Mat left_mask_not;
    cv::bitwise_and(left_mask_template_, right_mask_template_, overlap_mask_);
    cv::bitwise_not(right_mask_template_, right_mask_not);
    cv::bitwise_not(left_mask_template_, left_mask_not);
    cv::bitwise_and(left_mask_template_, right_mask_not, only_left_mask_);
    cv::bitwise_and(right_mask_template_, left_mask_not, only_right_mask_);

    if (cv::countNonZero(overlap_mask_) > 0) {
        cv::Mat left_valid;
        cv::Mat right_valid;
        left_mask_template_.convertTo(left_valid, CV_8UC1, 1.0 / 255.0);
        right_mask_template_.convertTo(right_valid, CV_8UC1, 1.0 / 255.0);

        cv::Mat dist_left;
        cv::Mat dist_right;
        cv::distanceTransform(left_valid, dist_left, cv::DIST_L2, 3);
        cv::distanceTransform(right_valid, dist_right, cv::DIST_L2, 3);

        cv::Mat denom = dist_left + dist_right + 1e-6f;
        cv::divide(dist_left, denom, weight_left_);
        cv::divide(dist_right, denom, weight_right_);

        std::vector<cv::Mat> wl(3, weight_left_);
        std::vector<cv::Mat> wr(3, weight_right_);
        cv::merge(wl, weight_left_3c_);
        cv::merge(wr, weight_right_3c_);
    } else {
        weight_left_ = cv::Mat::zeros(output_size_, CV_32FC1);
        weight_right_ = cv::Mat::zeros(output_size_, CV_32FC1);
        weight_left_3c_ = cv::Mat::zeros(output_size_, CV_32FC3);
        weight_right_3c_ = cv::Mat::zeros(output_size_, CV_32FC3);
    }

    calibrated_ = true;
    metrics_.calibrated = true;
    metrics_.output_width = output_size_.width;
    metrics_.output_height = output_size_.height;
    metrics_.blend_mode = "feather";
    metrics_.status = "calibrated";
    return true;
}

bool StitchEngine::stitch_pair_locked(const cv::Mat& left_frame, const cv::Mat& right_frame, std::int64_t pair_ts_ns) {
    if (!ensure_calibration_locked(left_frame, right_frame)) {
        metrics_.stitch_fps = 0.0;
        return false;
    }

    cv::Mat canvas_left = cv::Mat::zeros(output_size_, CV_8UC3);
    left_frame.copyTo(canvas_left(left_roi_));

    cv::Mat warped_right;
    if (gpu_available_) {
        try {
            gpu_right_input_.upload(right_frame);
            cv::cuda::warpPerspective(
                gpu_right_input_,
                gpu_right_warped_,
                homography_adjusted_,
                output_size_);
            gpu_right_warped_.download(warped_right);
            metrics_.gpu_warp_count += 1;
        } catch (const cv::Exception&) {
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = "cuda warp failed, fallback cpu";
        }
    }

    if (warped_right.empty()) {
        cv::warpPerspective(
            right_frame,
            warped_right,
            homography_adjusted_,
            output_size_);
        metrics_.cpu_warp_count += 1;
    }

    cv::Mat stitched = cv::Mat::zeros(output_size_, CV_8UC3);
    canvas_left.copyTo(stitched, only_left_mask_);
    warped_right.copyTo(stitched, only_right_mask_);

    if (cv::countNonZero(overlap_mask_) > 0) {
        cv::Mat left_f;
        cv::Mat right_f;
        cv::Mat left_part;
        cv::Mat right_part;
        cv::Mat overlap_f;
        cv::Mat overlap_u8;

        canvas_left.convertTo(left_f, CV_32FC3);
        warped_right.convertTo(right_f, CV_32FC3);
        cv::multiply(left_f, weight_left_3c_, left_part);
        cv::multiply(right_f, weight_right_3c_, right_part);
        cv::add(left_part, right_part, overlap_f);
        overlap_f.convertTo(overlap_u8, CV_8UC3);
        overlap_u8.copyTo(stitched, overlap_mask_);

        cv::Mat left_gray;
        cv::Mat right_gray;
        cv::Mat abs_diff;
        cv::cvtColor(canvas_left, left_gray, cv::COLOR_BGR2GRAY);
        cv::cvtColor(warped_right, right_gray, cv::COLOR_BGR2GRAY);
        cv::absdiff(left_gray, right_gray, abs_diff);
        metrics_.overlap_diff_mean = cv::mean(abs_diff, overlap_mask_)[0];
    } else {
        metrics_.overlap_diff_mean = 0.0;
    }

    latest_stitched_ = stitched;
    metrics_.cpu_blend_count += 1;
    metrics_.stitched_count += 1;
    metrics_.frame_index += 1;
    metrics_.status = "stitching";
    metrics_.blend_mode = "feather";
    if (last_worker_timestamp_ns_ > 0) {
        metrics_.stitch_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.stitch_fps = 0.0;
    }
    return true;
}

void StitchEngine::update_metrics_locked() {
    const auto left = g_left_reader.snapshot();
    const auto right = g_right_reader.snapshot();

    metrics_.left_fps = left.fps;
    metrics_.right_fps = right.fps;
    metrics_.left_frames_total = left.frames_total;
    metrics_.right_frames_total = right.frames_total;
    metrics_.left_stale_drops = left.stale_drops;
    metrics_.right_stale_drops = right.stale_drops;
    metrics_.left_last_error = left.last_error;
    metrics_.right_last_error = right.last_error;
    metrics_.gpu_feature_enabled = false;
    metrics_.gpu_feature_reason = "not implemented in native runtime yet";
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.calibrated = calibrated_;
    metrics_.output_width = output_size_.width;
    metrics_.output_height = output_size_.height;

    if (!left.has_frame || !right.has_frame) {
        metrics_.status = "waiting for both streams";
        metrics_.worker_fps = 0.0;
        metrics_.stitch_fps = 0.0;
        return;
    }

    metrics_.pair_skew_ms_mean =
        std::abs(static_cast<double>(left.latest_timestamp_ns - right.latest_timestamp_ns)) / 1'000'000.0;

    const bool has_new_pair = (left.latest_seq > last_left_seq_) && (right.latest_seq > last_right_seq_);
    if (!has_new_pair) {
        metrics_.status = "waiting next frame";
        return;
    }

    cv::Mat left_frame;
    cv::Mat right_frame;
    std::int64_t left_seq = 0;
    std::int64_t right_seq = 0;
    std::int64_t left_ts_ns = 0;
    std::int64_t right_ts_ns = 0;
    if (!g_left_reader.copy_latest_frame(&left_frame, &left_seq, &left_ts_ns) ||
        !g_right_reader.copy_latest_frame(&right_frame, &right_seq, &right_ts_ns)) {
        metrics_.status = "frame_copy_failed";
        return;
    }

    const auto pair_ts_ns = std::max(left_ts_ns, right_ts_ns);
    if (last_worker_timestamp_ns_ > 0) {
        metrics_.worker_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.worker_fps = 0.0;
    }

    last_left_seq_ = left_seq;
    last_right_seq_ = right_seq;
    const bool stitched_ok = stitch_pair_locked(left_frame, right_frame, pair_ts_ns);
    last_worker_timestamp_ns_ = pair_ts_ns;

    if (!stitched_ok) {
        return;
    }
}

}  // namespace hogak::engine
