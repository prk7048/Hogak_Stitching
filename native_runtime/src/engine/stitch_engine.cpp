#include "engine/stitch_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudaarithm.hpp>
#include <opencv2/cudawarping.hpp>
#include <opencv2/imgproc.hpp>

#include "input/ffmpeg_rtsp_reader.h"
#include "output/ffmpeg_output_writer.h"

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

double clamp_output_scale(double value) {
    return std::clamp(value, 0.1, 1.0);
}

cv::Mat resize_frame_for_runtime(const cv::Mat& frame, double scale) {
    if (frame.empty()) {
        return frame;
    }
    if (std::abs(scale - 1.0) < 1e-6) {
        return frame;
    }
    const int width = std::max(2, static_cast<int>(std::round(frame.cols * scale)));
    const int height = std::max(2, static_cast<int>(std::round(frame.rows * scale)));
    cv::Mat resized;
    cv::resize(frame, resized, cv::Size(width, height), 0.0, 0.0, cv::INTER_AREA);
    return resized;
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

bool parse_homography_numbers(const std::string& text, cv::Mat* homography_out) {
    if (homography_out == nullptr) {
        return false;
    }
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

bool extract_json_array_for_key(const std::string& text, const std::string& key, std::string* array_text_out) {
    if (array_text_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto first_bracket = text.find('[', key_pos);
    if (first_bracket == std::string::npos) {
        return false;
    }

    int depth = 0;
    for (std::size_t index = first_bracket; index < text.size(); ++index) {
        const char ch = text[index];
        if (ch == '[') {
            depth += 1;
        } else if (ch == ']') {
            depth -= 1;
            if (depth == 0) {
                *array_text_out = text.substr(first_bracket, index - first_bracket + 1);
                return true;
            }
        }
    }
    return false;
}

bool load_homography_from_file(const std::string& path, cv::Mat* homography_out) {
    if (homography_out == nullptr) {
        return false;
    }

    std::ifstream file(path);
    if (!file.is_open()) {
        return false;
    }
    std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());

    std::string homography_array_text;
    if (extract_json_array_for_key(text, "\"homography\"", &homography_array_text) &&
        parse_homography_numbers(homography_array_text, homography_out)) {
        return true;
    }

    try {
        cv::FileStorage fs(path, cv::FileStorage::READ | cv::FileStorage::FORMAT_AUTO);
        if (fs.isOpened()) {
            cv::Mat mat;
            if (fs.root().isMap()) {
                fs["homography"] >> mat;
            }
            if (mat.empty()) {
                fs.root() >> mat;
            }
            if (!mat.empty() && mat.rows == 3 && mat.cols == 3) {
                mat.convertTo(*homography_out, CV_64F);
                return true;
            }
        }
    } catch (const cv::Exception&) {
        // Fall through to permissive numeric parsing below.
    }

    return parse_homography_numbers(text, homography_out);
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

bool StitchEngine::select_pair_locked(
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    SelectedPair* pair_out) {
    if (pair_out == nullptr || !left_snapshot.has_frame || !right_snapshot.has_frame) {
        return false;
    }

    const auto mode = config_.sync_pair_mode;
    const auto max_delta_ns = static_cast<std::int64_t>(
        std::max(1.0, config_.sync_match_max_delta_ms) * 1'000'000.0);
    const auto manual_offset_ns = static_cast<std::int64_t>(config_.sync_manual_offset_ms * 1'000'000.0);

    bool left_ok = false;
    bool right_ok = false;
    if (mode == "none") {
        left_ok = g_left_reader.copy_latest_frame(&pair_out->left_frame, &pair_out->left_seq, &pair_out->left_ts_ns);
        right_ok = g_right_reader.copy_latest_frame(&pair_out->right_frame, &pair_out->right_seq, &pair_out->right_ts_ns);
    } else {
        const auto left_target_ns = left_snapshot.latest_timestamp_ns;
        const auto right_target_ns = right_snapshot.latest_timestamp_ns + manual_offset_ns;
        const auto common_target_ns = (mode == "oldest")
            ? std::min(left_target_ns, right_target_ns)
            : std::max(left_target_ns, right_target_ns);
        const bool prefer_past = (mode == "oldest");
        left_ok = g_left_reader.copy_closest_frame(
            common_target_ns,
            prefer_past,
            &pair_out->left_frame,
            &pair_out->left_seq,
            &pair_out->left_ts_ns);
        right_ok = g_right_reader.copy_closest_frame(
            common_target_ns - manual_offset_ns,
            prefer_past,
            &pair_out->right_frame,
            &pair_out->right_seq,
            &pair_out->right_ts_ns);
    }

    if (!left_ok || !right_ok) {
        return false;
    }

    const auto adjusted_right_ts_ns = pair_out->right_ts_ns + manual_offset_ns;
    metrics_.pair_skew_ms_mean =
        std::abs(static_cast<double>(pair_out->left_ts_ns - adjusted_right_ts_ns)) / 1'000'000.0;
    if (mode != "none" && std::llabs(pair_out->left_ts_ns - adjusted_right_ts_ns) > max_delta_ns) {
        metrics_.status = "waiting sync pair";
        return false;
    }
    return true;
}

void StitchEngine::clear_calibration_state_locked() {
    calibrated_ = false;
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
    latest_stitched_.release();
    gpu_left_input_.release();
    gpu_left_canvas_.release();
    gpu_stitched_.release();
    gpu_right_input_.release();
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
    left_roi_ = cv::Rect();
    overlap_roi_ = cv::Rect();
    output_size_ = cv::Size();
    full_overlap_ = false;
    metrics_.calibrated = false;
    metrics_.output_width = 0;
    metrics_.output_height = 0;
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.overlap_diff_mean = 0.0;
    metrics_.stitched_mean_luma = 0.0;
    metrics_.warped_mean_luma = 0.0;
    metrics_.only_left_pixels = 0;
    metrics_.only_right_pixels = 0;
    metrics_.overlap_pixels = 0;
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
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    clear_calibration_state_locked();
    output_writer_.reset();
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
    if (output_writer_ != nullptr) {
        output_writer_->stop();
        output_writer_.reset();
    }
    g_left_reader.stop();
    g_right_reader.stop();
    metrics_.status = "stopped";
    metrics_.output_written_fps = 0.0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
}

bool StitchEngine::reload_config(const EngineConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
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
        (config.left.width != config_.left.width) ||
        (config.left.height != config_.left.height) ||
        (config.right.width != config_.right.width) ||
        (config.right.height != config_.right.height) ||
        (config.input_runtime != config_.input_runtime) ||
        (config.ffmpeg_bin != config_.ffmpeg_bin);

    if (output_writer_ != nullptr) {
        output_writer_->stop();
        output_writer_.reset();
    }
    if (restart_readers) {
        g_left_reader.stop();
        g_right_reader.stop();
    }

    config_ = config;
    clear_calibration_state_locked();
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    metrics_.output_written_fps = 0.0;
    metrics_.gpu_enabled = (config.gpu_mode != "off");
    gpu_available_ = metrics_.gpu_enabled && (cv::cuda::getCudaEnabledDeviceCount() > config.gpu_device);
    metrics_.gpu_reason = gpu_available_ ? "reloaded config" : "gpu disabled or unavailable";
    metrics_.output_last_error.clear();
    metrics_.output_effective_codec.clear();
    metrics_.output_target = config.output.target;
    metrics_.status = "config reloaded";

    if (restart_readers) {
        const auto ffmpeg_bin = config.ffmpeg_bin;
        const bool left_ok = !config.left.url.empty() &&
            g_left_reader.start(config.left, ffmpeg_bin, config.input_runtime);
        const bool right_ok = !config.right.url.empty() &&
            g_right_reader.start(config.right, ffmpeg_bin, config.input_runtime);
        if (!left_ok || !right_ok) {
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
    clear_calibration_state_locked();
    metrics_.output_last_error.clear();
    metrics_.output_effective_codec.clear();
    metrics_.status = "calibration reset";
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    metrics_.output_written_fps = 0.0;
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
    homography = scale_homography_for_runtime(homography, clamp_output_scale(config_.stitch_output_scale));

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
    metrics_.only_left_pixels = cv::countNonZero(only_left_mask_);
    metrics_.only_right_pixels = cv::countNonZero(only_right_mask_);
    metrics_.overlap_pixels = cv::countNonZero(overlap_mask_);
    full_overlap_ = (metrics_.overlap_pixels > 0) &&
                    (metrics_.only_left_pixels == 0) &&
                    (metrics_.only_right_pixels == 0);

    if (cv::countNonZero(overlap_mask_) > 0) {
        overlap_roi_ = cv::boundingRect(overlap_mask_);
        overlap_mask_roi_ = overlap_mask_(overlap_roi_).clone();
        if (full_overlap_) {
            weight_left_ = cv::Mat(output_size_, CV_32FC1, cv::Scalar(0.5f));
            weight_right_ = cv::Mat(output_size_, CV_32FC1, cv::Scalar(0.5f));
        } else {
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
        }

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

    metrics_.left_mean_luma = mean_luma(left_frame);
    metrics_.right_mean_luma = mean_luma(right_frame);
    metrics_.warped_mean_luma = 0.0;

    cv::Mat stitched;
    cv::Mat warped_right;
    cv::Mat canvas_left;
    bool used_gpu_blend = false;

    if (gpu_available_) {
        try {
            gpu_left_input_.upload(left_frame);
            gpu_left_canvas_.create(output_size_, CV_8UC3);
            gpu_left_canvas_.setTo(cv::Scalar::all(0));
            cv::cuda::GpuMat left_roi_gpu(gpu_left_canvas_, left_roi_);
            gpu_left_input_.copyTo(left_roi_gpu);

            gpu_right_input_.upload(right_frame);
            cv::cuda::warpPerspective(
                gpu_right_input_,
                gpu_right_warped_,
                homography_adjusted_,
                output_size_);
            metrics_.gpu_warp_count += 1;
            gpu_right_warped_.download(warped_right);
            metrics_.warped_mean_luma = mean_luma(warped_right);

            gpu_stitched_.create(output_size_, CV_8UC3);
            gpu_stitched_.setTo(cv::Scalar::all(0));
            gpu_left_canvas_.copyTo(gpu_stitched_, gpu_only_left_mask_);
            gpu_right_warped_.copyTo(gpu_stitched_, gpu_only_right_mask_);

            if (overlap_roi_.area() > 0) {
                if (full_overlap_) {
                    cv::cuda::addWeighted(
                        gpu_left_canvas_,
                        0.5,
                        gpu_right_warped_,
                        0.5,
                        0.0,
                        gpu_stitched_);
                } else {
                    cv::cuda::GpuMat left_overlap_u8(gpu_left_canvas_, overlap_roi_);
                    cv::cuda::GpuMat right_overlap_u8(gpu_right_warped_, overlap_roi_);
                    cv::cuda::GpuMat stitched_overlap_u8(gpu_stitched_, overlap_roi_);

                    cv::cuda::GpuMat left_overlap_f;
                    cv::cuda::GpuMat right_overlap_f;
                    left_overlap_u8.convertTo(left_overlap_f, CV_32FC3);
                    right_overlap_u8.convertTo(right_overlap_f, CV_32FC3);

                    std::vector<cv::cuda::GpuMat> left_channels;
                    std::vector<cv::cuda::GpuMat> right_channels;
                    std::vector<cv::cuda::GpuMat> blended_channels(3);
                    cv::cuda::split(left_overlap_f, left_channels);
                    cv::cuda::split(right_overlap_f, right_channels);
                    for (int channel = 0; channel < 3; ++channel) {
                        cv::cuda::GpuMat left_part;
                        cv::cuda::GpuMat right_part;
                        cv::cuda::multiply(left_channels[channel], gpu_weight_left_roi_, left_part);
                        cv::cuda::multiply(right_channels[channel], gpu_weight_right_roi_, right_part);
                        cv::cuda::add(left_part, right_part, blended_channels[channel]);
                    }

                    cv::cuda::merge(blended_channels, gpu_overlap_f_);
                    gpu_overlap_f_.convertTo(gpu_overlap_u8_, CV_8UC3);
                    gpu_overlap_u8_.copyTo(stitched_overlap_u8, gpu_overlap_mask_roi_);
                }
            }

            gpu_stitched_.download(stitched);
            metrics_.gpu_blend_count += 1;
            metrics_.overlap_diff_mean = 0.0;
            used_gpu_blend = true;
        } catch (const cv::Exception& e) {
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = std::string("cuda stitch failed: ") + e.what();
        }
    }

    if (!used_gpu_blend) {
        canvas_left = cv::Mat::zeros(output_size_, CV_8UC3);
        left_frame.copyTo(canvas_left(left_roi_));
        cv::warpPerspective(
            right_frame,
            warped_right,
            homography_adjusted_,
            output_size_);
        metrics_.cpu_warp_count += 1;
        metrics_.warped_mean_luma = mean_luma(warped_right);

        stitched = cv::Mat::zeros(output_size_, CV_8UC3);
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

        metrics_.cpu_blend_count += 1;
    }

    latest_stitched_ = stitched;
    if (!stitched.empty()) {
        cv::Mat stitched_gray;
        cv::cvtColor(stitched, stitched_gray, cv::COLOR_BGR2GRAY);
        metrics_.stitched_mean_luma = cv::mean(stitched_gray)[0];
    } else {
        metrics_.stitched_mean_luma = 0.0;
    }
    metrics_.stitched_count += 1;
    metrics_.frame_index += 1;
    metrics_.status = "stitching";
    metrics_.blend_mode = "feather";
    if (last_worker_timestamp_ns_ > 0) {
        metrics_.stitch_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.stitch_fps = 0.0;
    }

    if (config_.output.runtime == "ffmpeg" && !config_.output.target.empty()) {
        if (output_writer_ == nullptr) {
            output_writer_ = std::make_unique<hogak::output::FfmpegOutputWriter>();
            const double output_fps = std::max({metrics_.worker_fps, metrics_.left_fps, metrics_.right_fps, 30.0});
            if (!output_writer_->start(config_.output, config_.ffmpeg_bin, stitched.cols, stitched.rows, output_fps)) {
                metrics_.output_last_error = "failed to start ffmpeg output writer";
                output_writer_.reset();
            } else {
                metrics_.output_target = config_.output.target;
                metrics_.output_effective_codec = output_writer_->effective_codec();
            }
        }
        if (output_writer_ != nullptr) {
            output_writer_->submit(stitched, pair_ts_ns);
        }
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
    metrics_.output_active = (output_writer_ != nullptr) && output_writer_->active();
    metrics_.output_frames_written = (output_writer_ != nullptr) ? output_writer_->frames_written() : 0;
    metrics_.output_frames_dropped = (output_writer_ != nullptr) ? output_writer_->frames_dropped() : 0;
    metrics_.output_effective_codec =
        (output_writer_ != nullptr) ? output_writer_->effective_codec() : metrics_.output_effective_codec;
    metrics_.output_last_error = (output_writer_ != nullptr) ? output_writer_->last_error() : metrics_.output_last_error;
    if (metrics_.output_frames_written < last_output_frames_written_) {
        last_output_frames_written_ = metrics_.output_frames_written;
        last_output_timestamp_ns_ = 0;
    }
    if (metrics_.output_frames_written > last_output_frames_written_) {
        const auto now_ns =
            std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
                .count();
        const auto delta_frames = metrics_.output_frames_written - last_output_frames_written_;
        const auto delta_ns = now_ns - last_output_timestamp_ns_;
        if (last_output_timestamp_ns_ > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics_.output_written_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        last_output_frames_written_ = metrics_.output_frames_written;
        last_output_timestamp_ns_ = now_ns;
    } else if (!metrics_.output_active) {
        metrics_.output_written_fps = 0.0;
    }

    if (!left.has_frame || !right.has_frame) {
        metrics_.status = "waiting for both streams";
        metrics_.worker_fps = 0.0;
        metrics_.stitch_fps = 0.0;
        return;
    }

    metrics_.pair_skew_ms_mean =
        std::abs(static_cast<double>(left.latest_timestamp_ns - right.latest_timestamp_ns)) / 1'000'000.0;
    SelectedPair pair;
    if (!select_pair_locked(left, right, &pair)) {
        if (metrics_.status.empty() || metrics_.status == "stitching") {
            metrics_.status = "waiting sync pair";
        }
        return;
    }

    const bool has_new_left = pair.left_seq > last_left_seq_;
    const bool has_new_right = pair.right_seq > last_right_seq_;
    if (!has_new_left && !has_new_right) {
        metrics_.status = "waiting next frame";
        return;
    }
    if (!has_new_left || !has_new_right) {
        metrics_.status = "waiting paired fresh frame";
        return;
    }

    const auto pair_ts_ns = std::max(pair.left_ts_ns, pair.right_ts_ns);
    if (last_worker_timestamp_ns_ > 0) {
        metrics_.worker_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.worker_fps = 0.0;
    }

    last_left_seq_ = pair.left_seq;
    last_right_seq_ = pair.right_seq;
    if (config_.stitch_every_n > 1 && (std::max(pair.left_seq, pair.right_seq) % config_.stitch_every_n) != 0) {
        metrics_.reused_count += 1;
        metrics_.status = "skipping per stitch_every_n";
        last_worker_timestamp_ns_ = pair_ts_ns;
        return;
    }

    const double output_scale = clamp_output_scale(config_.stitch_output_scale);
    cv::Mat left_frame = resize_frame_for_runtime(pair.left_frame, output_scale);
    cv::Mat right_frame = resize_frame_for_runtime(pair.right_frame, output_scale);
    const bool stitched_ok = stitch_pair_locked(left_frame, right_frame, pair_ts_ns);
    last_worker_timestamp_ns_ = pair_ts_ns;

    if (!stitched_ok) {
        return;
    }
}

}  // namespace hogak::engine
