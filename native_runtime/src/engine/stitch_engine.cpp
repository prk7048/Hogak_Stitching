#include "engine/stitch_engine.h"

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

#include "input/ffmpeg_rtsp_reader.h"
#include "output/output_writer.h"
#include "output/output_writer_factory.h"

namespace hogak::engine {

namespace {

input::FfmpegRtspReader g_left_reader;
input::FfmpegRtspReader g_right_reader;
constexpr int kMotionProbeWidth = 64;
constexpr int kMotionProbeHeight = 36;
constexpr double kReaderRestartAgeMs = 1500.0;
constexpr double kReaderRestartCooldownSec = 3.0;
constexpr int kSeamFeatherMinWidth = 48;
constexpr int kSeamFeatherMaxWidth = 192;
constexpr double kSeamFeatherFraction = 0.14;
constexpr int kHeavyMetricSampleEveryN = 15;

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

bool input_pipe_format_is_nv12(const StreamConfig& config) {
    return config.input_pipe_format == "nv12";
}

bool is_nv12_gpu_conversion_unsupported(const cv::Exception& error) {
    const std::string message = error.what();
    return message.find("Unknown/unsupported color conversion code") != std::string::npos;
}

cv::Size scaled_frame_size(const cv::Size& source_size, double scale) {
    if (source_size.width <= 0 || source_size.height <= 0) {
        return source_size;
    }
    if (std::abs(scale - 1.0) < 1e-6) {
        return source_size;
    }
    return cv::Size(
        std::max(2, static_cast<int>(std::round(source_size.width * scale))),
        std::max(2, static_cast<int>(std::round(source_size.height * scale))));
}

cv::Size input_frame_size_for_runtime(const cv::Mat& frame, const StreamConfig& config, double scale) {
    if (input_pipe_format_is_nv12(config)) {
        return scaled_frame_size(cv::Size(config.width, config.height), scale);
    }
    if (frame.empty()) {
        return {};
    }
    return scaled_frame_size(frame.size(), scale);
}

double input_frame_mean_luma(const cv::Mat& frame, const StreamConfig& config) {
    if (frame.empty()) {
        return 0.0;
    }
    if (!input_pipe_format_is_nv12(config)) {
        return mean_luma(frame);
    }
    if (frame.type() != CV_8UC1 || frame.cols != config.width || frame.rows < config.height) {
        return 0.0;
    }
    cv::Mat y_plane(config.height, config.width, CV_8UC1, const_cast<std::uint8_t*>(frame.ptr<std::uint8_t>(0)), frame.step);
    return cv::mean(y_plane)[0];
}

cv::Mat decode_input_frame_for_stitch(const cv::Mat& frame, const StreamConfig& config) {
    if (frame.empty()) {
        return {};
    }
    if (!input_pipe_format_is_nv12(config)) {
        return frame;
    }
    if (frame.type() != CV_8UC1 || frame.cols != config.width || frame.rows < (config.height + (config.height / 2))) {
        return {};
    }

    cv::Mat y_plane(config.height, config.width, CV_8UC1, const_cast<std::uint8_t*>(frame.ptr<std::uint8_t>(0)), frame.step);
    cv::Mat uv_plane(
        config.height / 2,
        config.width / 2,
        CV_8UC2,
        const_cast<std::uint8_t*>(frame.ptr<std::uint8_t>(config.height)),
        frame.step);
    cv::Mat decoded_bgr;
    cv::cvtColorTwoPlane(y_plane, uv_plane, decoded_bgr, cv::COLOR_YUV2BGR_NV12);
    return decoded_bgr;
}

bool upload_input_frame_for_gpu_stitch(
    const cv::Mat& raw_input,
    const StreamConfig& config,
    double output_scale,
    const cv::Mat* cpu_fallback_bgr,
    cv::cuda::GpuMat* nv12_y_gpu,
    cv::cuda::GpuMat* nv12_uv_gpu,
    cv::cuda::GpuMat* decoded_bgr_gpu,
    cv::cuda::GpuMat* final_bgr_gpu) {
    if (final_bgr_gpu == nullptr) {
        return false;
    }
    const cv::Size target_size = input_frame_size_for_runtime(raw_input, config, output_scale);
    if (target_size.width <= 0 || target_size.height <= 0) {
        return false;
    }

    if (!input_pipe_format_is_nv12(config)) {
        if (cpu_fallback_bgr == nullptr || cpu_fallback_bgr->empty()) {
            return false;
        }
        const cv::Size source_size = cpu_fallback_bgr->size();
        if (target_size == source_size) {
            final_bgr_gpu->upload(*cpu_fallback_bgr);
        } else {
            cv::cuda::GpuMat uploaded_bgr;
            uploaded_bgr.upload(*cpu_fallback_bgr);
            cv::cuda::resize(uploaded_bgr, *final_bgr_gpu, target_size, 0.0, 0.0, cv::INTER_AREA);
        }
        return true;
    }

    if (raw_input.type() != CV_8UC1 || raw_input.cols != config.width || raw_input.rows < (config.height + (config.height / 2))) {
        return false;
    }
    if (nv12_y_gpu == nullptr || decoded_bgr_gpu == nullptr) {
        return false;
    }
    if (nv12_uv_gpu != nullptr) {
        nv12_uv_gpu->release();
    }
    nv12_y_gpu->upload(raw_input);
    cv::cuda::cvtColor(*nv12_y_gpu, *decoded_bgr_gpu, cv::COLOR_YUV2BGR_NV12);

    if (decoded_bgr_gpu->size() == target_size) {
        decoded_bgr_gpu->copyTo(*final_bgr_gpu);
    } else {
        cv::cuda::resize(*decoded_bgr_gpu, *final_bgr_gpu, target_size, 0.0, 0.0, cv::INTER_AREA);
    }
    return true;
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

cv::Size resolve_output_frame_size(const OutputConfig& config, const cv::Size& fallback) {
    if (config.width > 0 && config.height > 0) {
        return cv::Size(config.width, config.height);
    }
    return fallback;
}

cv::Size fit_aspect_inside(const cv::Size& source, const cv::Size& target) {
    if (source.width <= 0 || source.height <= 0 || target.width <= 0 || target.height <= 0) {
        return source;
    }
    const double scale = std::min(
        static_cast<double>(target.width) / static_cast<double>(source.width),
        static_cast<double>(target.height) / static_cast<double>(source.height));
    return cv::Size(
        std::max(2, static_cast<int>(std::round(static_cast<double>(source.width) * scale))),
        std::max(2, static_cast<int>(std::round(static_cast<double>(source.height) * scale))));
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

std::string format_overlay_clock_now() {
    const auto now = std::chrono::system_clock::now();
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()) % 1000;
    const std::time_t now_time = std::chrono::system_clock::to_time_t(now);
    std::tm local_tm{};
#ifdef _WIN32
    localtime_s(&local_tm, &now_time);
#else
    localtime_r(&now_time, &local_tm);
#endif
    std::ostringstream out;
    out << std::put_time(&local_tm, "%H:%M:%S")
        << '.'
        << std::setw(3)
        << std::setfill('0')
        << ms.count();
    return out.str();
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

struct ServicePairCandidate {
    cv::Mat left_frame;
    cv::Mat right_frame;
    std::int64_t left_seq = 0;
    std::int64_t right_seq = 0;
    std::int64_t left_ts_ns = 0;
    std::int64_t right_ts_ns = 0;
    std::int64_t pair_ts_ns = 0;
    std::int64_t skew_ns = std::numeric_limits<std::int64_t>::max();
    std::int64_t sync_overage_ns = 0;
    std::int64_t cadence_error_ns = std::numeric_limits<std::int64_t>::max();
    std::int64_t freshness_ns = 0;
    std::int64_t newest_ns = 0;
    std::int64_t reuse_age_ns = 0;
    int reuse_streak = 0;
    int advance_score = 0;
};

double resolve_service_target_fps(
    const EngineConfig& config,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot) {
    double configured_output_fps = 0.0;
    if (output_config_enabled(config.production_output) && config.production_output.fps > 0.0) {
        configured_output_fps = std::max(configured_output_fps, config.production_output.fps);
    }
    if (output_config_enabled(config.output) && config.output.fps > 0.0) {
        configured_output_fps = std::max(configured_output_fps, config.output.fps);
    }
    if (configured_output_fps > 0.0) {
        return configured_output_fps;
    }
    return std::max({left_snapshot.fps, right_snapshot.fps, 30.0});
}

bool service_pair_candidate_better(
    const ServicePairCandidate& candidate,
    const ServicePairCandidate& best) {
    if (candidate.advance_score != best.advance_score) {
        return candidate.advance_score > best.advance_score;
    }
    if (candidate.sync_overage_ns != best.sync_overage_ns) {
        return candidate.sync_overage_ns < best.sync_overage_ns;
    }
    if (candidate.reuse_streak != best.reuse_streak) {
        return candidate.reuse_streak < best.reuse_streak;
    }
    if (candidate.reuse_age_ns != best.reuse_age_ns) {
        return candidate.reuse_age_ns < best.reuse_age_ns;
    }
    if (candidate.cadence_error_ns != best.cadence_error_ns) {
        return candidate.cadence_error_ns < best.cadence_error_ns;
    }
    if (candidate.skew_ns != best.skew_ns) {
        return candidate.skew_ns < best.skew_ns;
    }
    if (candidate.freshness_ns != best.freshness_ns) {
        return candidate.freshness_ns > best.freshness_ns;
    }
    if (candidate.newest_ns != best.newest_ns) {
        return candidate.newest_ns > best.newest_ns;
    }
    if (candidate.left_seq != best.left_seq) {
        return candidate.left_seq > best.left_seq;
    }
    return candidate.right_seq > best.right_seq;
}

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
    } else if (mode == "service") {
        g_left_reader.buffered_frame_infos(&left_buffered_infos_cache_);
        g_right_reader.buffered_frame_infos(&right_buffered_infos_cache_);
        const auto& left_infos = left_buffered_infos_cache_;
        const auto& right_infos = right_buffered_infos_cache_;
        const double target_fps = resolve_service_target_fps(config_, left_snapshot, right_snapshot);
        const auto target_period_ns = static_cast<std::int64_t>(
            std::max(1.0, std::round(1'000'000'000.0 / std::max(1.0, target_fps))));
        const auto fresh_pair_slack_ns = std::min<std::int64_t>(
            max_delta_ns / 4,
            std::max<std::int64_t>(1'000'000, target_period_ns / 2));
        const auto max_reuse_age_ns = static_cast<std::int64_t>(
            std::max(1.0, config_.pair_reuse_max_age_ms) * 1'000'000.0);
        const int max_consecutive_reuse = std::max(1, config_.pair_reuse_max_consecutive);
        const auto latest_adjusted_right_ts_ns = right_snapshot.latest_timestamp_ns + manual_offset_ns;
        const auto latest_pair_ts_ns = std::max(left_snapshot.latest_timestamp_ns, latest_adjusted_right_ts_ns);
        const auto unclamped_target_pair_ts_ns =
            (last_service_pair_ts_ns_ > 0) ? (last_service_pair_ts_ns_ + target_period_ns) : latest_pair_ts_ns;
        const auto min_target_pair_ts_ns = latest_pair_ts_ns - target_period_ns;
        const auto target_pair_ts_ns = std::max(unclamped_target_pair_ts_ns, min_target_pair_ts_ns);
        ServicePairCandidate best_candidate;
        bool have_candidate = false;
        bool had_reuse_limited_candidate = false;
        bool had_repeat_only_candidate = false;
        bool had_reuse_limited_left_only = false;
        bool had_reuse_limited_right_only = false;
        bool had_reuse_limited_both = false;

        for (const auto& left_info : left_infos) {
            for (const auto& right_info : right_infos) {
                const auto adjusted_right_ts_ns = right_info.timestamp_ns + manual_offset_ns;
                const auto skew_ns = std::llabs(left_info.timestamp_ns - adjusted_right_ts_ns);
                const bool has_new_left = left_info.seq > last_left_seq_;
                const bool has_new_right = right_info.seq > last_right_seq_;
                const bool left_reused = !has_new_left;
                const bool right_reused = !has_new_right;
                if (!has_new_left && !has_new_right) {
                    had_repeat_only_candidate = true;
                    continue;
                }
                if (!config_.allow_frame_reuse && (left_reused || right_reused)) {
                    had_reuse_limited_candidate = true;
                    if (left_reused && right_reused) {
                        had_reuse_limited_both = true;
                    } else if (left_reused) {
                        had_reuse_limited_left_only = true;
                    } else if (right_reused) {
                        had_reuse_limited_right_only = true;
                    }
                    continue;
                }
                const auto allowed_skew_ns =
                    (!left_reused && !right_reused) ? (max_delta_ns + fresh_pair_slack_ns) : max_delta_ns;
                if (skew_ns > allowed_skew_ns) {
                    continue;
                }

                ServicePairCandidate candidate;
                candidate.left_frame = left_info.frame;
                candidate.right_frame = right_info.frame;
                candidate.left_seq = left_info.seq;
                candidate.right_seq = right_info.seq;
                candidate.left_ts_ns = left_info.timestamp_ns;
                candidate.right_ts_ns = right_info.timestamp_ns;
                candidate.skew_ns = skew_ns;
                candidate.sync_overage_ns = std::max<std::int64_t>(0, skew_ns - max_delta_ns);
                candidate.pair_ts_ns = std::max(left_info.timestamp_ns, adjusted_right_ts_ns);
                candidate.cadence_error_ns = std::llabs(candidate.pair_ts_ns - target_pair_ts_ns);
                candidate.freshness_ns = std::min(left_info.timestamp_ns, adjusted_right_ts_ns);
                candidate.newest_ns = candidate.pair_ts_ns;
                const auto left_age_ns = std::max<std::int64_t>(0, latest_pair_ts_ns - left_info.timestamp_ns);
                const auto right_age_ns = std::max<std::int64_t>(0, latest_pair_ts_ns - adjusted_right_ts_ns);
                const bool can_reuse_left =
                    left_reused &&
                    config_.allow_frame_reuse &&
                    left_age_ns <= max_reuse_age_ns &&
                    consecutive_left_reuse_ < max_consecutive_reuse;
                const bool can_reuse_right =
                    right_reused &&
                    config_.allow_frame_reuse &&
                    right_age_ns <= max_reuse_age_ns &&
                    consecutive_right_reuse_ < max_consecutive_reuse;
                if ((left_reused && !can_reuse_left) || (right_reused && !can_reuse_right)) {
                    had_reuse_limited_candidate = true;
                    if (left_reused && right_reused) {
                        had_reuse_limited_both = true;
                    } else if (left_reused) {
                        had_reuse_limited_left_only = true;
                    } else if (right_reused) {
                        had_reuse_limited_right_only = true;
                    }
                    continue;
                }
                candidate.reuse_age_ns = std::max(
                    left_reused ? left_age_ns : 0,
                    right_reused ? right_age_ns : 0);
                candidate.reuse_streak =
                    (left_reused ? consecutive_left_reuse_ : 0) +
                    (right_reused ? consecutive_right_reuse_ : 0);
                candidate.advance_score =
                    (has_new_left ? 1 : 0) +
                    (has_new_right ? 1 : 0);

                if (!have_candidate || service_pair_candidate_better(candidate, best_candidate)) {
                    best_candidate = candidate;
                    have_candidate = true;
                }
            }
        }

        if (!have_candidate) {
            if (had_reuse_limited_candidate) {
                metrics_.status = "waiting paired fresh frame";
                metrics_.wait_paired_fresh_count += 1;
                const bool classify_both =
                    had_reuse_limited_both || (had_reuse_limited_left_only && had_reuse_limited_right_only);
                record_wait_paired_fresh_locked(
                    classify_both || had_reuse_limited_left_only,
                    classify_both || had_reuse_limited_right_only);
            } else if (had_repeat_only_candidate) {
                metrics_.status = "waiting next frame";
                metrics_.wait_next_frame_count += 1;
            } else {
                metrics_.status = "waiting sync pair";
                metrics_.wait_sync_pair_count += 1;
            }
            return false;
        }

        pair_out->left_frame = best_candidate.left_frame;
        pair_out->right_frame = best_candidate.right_frame;
        pair_out->left_seq = best_candidate.left_seq;
        pair_out->right_seq = best_candidate.right_seq;
        pair_out->left_ts_ns = best_candidate.left_ts_ns;
        pair_out->right_ts_ns = best_candidate.right_ts_ns;
        left_ok = !pair_out->left_frame.empty();
        right_ok = !pair_out->right_frame.empty();
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
    previous_stitched_probe_gray_.release();
    latest_stitched_.release();
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
    metrics_.output_width = 0;
    metrics_.output_height = 0;
    metrics_.production_output_width = 0;
    metrics_.production_output_height = 0;
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.overlap_diff_mean = 0.0;
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
        g_left_reader.stop();
        const bool ok = g_left_reader.start(config_.left, ffmpeg_bin, config_.input_runtime);
        if (ok) {
            left_reader_restart_count_ += 1;
            last_left_reader_restart_ns_ = now_ns;
            metrics_.status = std::string("left reader restarted: ") + reason;
        }
        return ok;
    }

    g_right_reader.stop();
    const bool ok = g_right_reader.start(config_.right, ffmpeg_bin, config_.input_runtime);
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
    metrics_.output_target = config.output.target;
    metrics_.output_command_line.clear();
    metrics_.production_output_target = config.production_output.target;
    metrics_.production_output_command_line.clear();
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_service_pair_ts_ns_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
    consecutive_left_reuse_ = 0;
    consecutive_right_reuse_ = 0;
    left_reader_restart_count_ = 0;
    right_reader_restart_count_ = 0;
    last_left_reader_restart_ns_ = 0;
    last_right_reader_restart_ns_ = 0;
    clear_calibration_state_locked();
    output_writer_.reset();
    production_output_writer_.reset();
    gpu_available_ = metrics_.gpu_enabled && (cv::cuda::getCudaEnabledDeviceCount() > config.gpu_device);
    gpu_nv12_input_supported_ = true;
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
    if (production_output_writer_ != nullptr) {
        production_output_writer_->stop();
        production_output_writer_.reset();
    }
    g_left_reader.stop();
    g_right_reader.stop();
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
    last_service_pair_ts_ns_ = 0;
    consecutive_left_reuse_ = 0;
    consecutive_right_reuse_ = 0;
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
        g_left_reader.stop();
        g_right_reader.stop();
    }

    config_ = config;
    clear_calibration_state_locked();
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_service_pair_ts_ns_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
    consecutive_left_reuse_ = 0;
    consecutive_right_reuse_ = 0;
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
    metrics_.output_target = config.output.target;
    metrics_.production_output_last_error.clear();
    metrics_.production_output_command_line.clear();
    metrics_.production_output_effective_codec.clear();
    metrics_.production_output_target = config.production_output.target;
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
    if (production_output_writer_ != nullptr) {
        production_output_writer_->stop();
        production_output_writer_.reset();
    }
    clear_calibration_state_locked();
    metrics_.output_last_error.clear();
    metrics_.output_command_line.clear();
    metrics_.output_effective_codec.clear();
    metrics_.production_output_last_error.clear();
    metrics_.production_output_command_line.clear();
    metrics_.production_output_effective_codec.clear();
    metrics_.status = "calibration reset";
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_service_pair_ts_ns_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_stitched_count_ = 0;
    last_stitch_timestamp_ns_ = 0;
    last_output_frames_written_ = 0;
    last_output_timestamp_ns_ = 0;
    last_production_output_frames_written_ = 0;
    last_production_output_timestamp_ns_ = 0;
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

bool StitchEngine::ensure_calibration_locked(const cv::Size& left_size, const cv::Size& right_size) {
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
            left_size,
            right_size,
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

    cv::Mat right_mask_source(right_size, CV_8UC1, cv::Scalar(255));
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

    calibrated_ = true;
    metrics_.calibrated = true;
    metrics_.output_width = (config_.output.width > 0) ? config_.output.width : output_size_.width;
    metrics_.output_height = (config_.output.height > 0) ? config_.output.height : output_size_.height;
    if (config_.production_output.runtime == "ffmpeg" && !config_.production_output.target.empty()) {
        metrics_.production_output_width =
            (config_.production_output.width > 0) ? config_.production_output.width : output_size_.width;
        metrics_.production_output_height =
            (config_.production_output.height > 0) ? config_.production_output.height : output_size_.height;
    } else {
        metrics_.production_output_width = 0;
        metrics_.production_output_height = 0;
    }
    metrics_.blend_mode = "seam_feather";
    metrics_.status = "calibrated";
    return true;
}

bool StitchEngine::prepare_output_frame_locked(
    const OutputConfig& output_config,
    const cv::Mat& stitched_cpu,
    const cv::cuda::GpuMat* stitched_gpu,
    cv::Mat* prepared_frame_out,
    const cv::cuda::GpuMat** prepared_gpu_frame_out) {
    if (prepared_frame_out != nullptr) {
        prepared_frame_out->release();
    }
    if (prepared_gpu_frame_out != nullptr) {
        *prepared_gpu_frame_out = nullptr;
    }
    const bool has_cpu_source = !stitched_cpu.empty();
    const bool has_gpu_source = stitched_gpu != nullptr && !stitched_gpu->empty();
    if ((prepared_frame_out == nullptr && prepared_gpu_frame_out == nullptr) || (!has_cpu_source && !has_gpu_source)) {
        return false;
    }

    const cv::Size source_size = has_cpu_source
        ? cv::Size(stitched_cpu.cols, stitched_cpu.rows)
        : cv::Size(stitched_gpu->cols, stitched_gpu->rows);
    const cv::Size target_size = resolve_output_frame_size(output_config, source_size);
    if (target_size.width <= 0 || target_size.height <= 0) {
        return false;
    }
    if (target_size == source_size) {
        if (prepared_frame_out != nullptr && has_cpu_source) {
            *prepared_frame_out = stitched_cpu;
        }
        if (prepared_gpu_frame_out != nullptr && has_gpu_source) {
            *prepared_gpu_frame_out = stitched_gpu;
        }
        if (prepared_frame_out != nullptr && !has_cpu_source && has_gpu_source) {
            stitched_gpu->download(*prepared_frame_out);
        }
        return true;
    }

    const cv::Size scaled_size = fit_aspect_inside(source_size, target_size);
    if (gpu_available_ && stitched_gpu != nullptr && !stitched_gpu->empty()) {
        try {
            const cv::cuda::GpuMat* scaled_source = stitched_gpu;
            if (scaled_size != source_size) {
                cv::cuda::resize(*stitched_gpu, gpu_output_scaled_, scaled_size, 0.0, 0.0, cv::INTER_LINEAR);
                scaled_source = &gpu_output_scaled_;
            }

            gpu_output_canvas_.create(target_size, CV_8UC3);
            gpu_output_canvas_.setTo(cv::Scalar::all(0));
            const cv::Rect roi(
                std::max(0, (target_size.width - scaled_size.width) / 2),
                std::max(0, (target_size.height - scaled_size.height) / 2),
                scaled_size.width,
                scaled_size.height);
            cv::cuda::GpuMat target_roi(gpu_output_canvas_, roi);
            scaled_source->copyTo(target_roi);
            if (prepared_gpu_frame_out != nullptr) {
                *prepared_gpu_frame_out = &gpu_output_canvas_;
            }
            if (prepared_frame_out != nullptr) {
                gpu_output_canvas_.download(*prepared_frame_out);
            }
            return true;
        } catch (const cv::Exception& e) {
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = std::string("cuda output prep failed: ") + e.what();
        }
    }

    cv::Mat scaled_frame;
    if (!has_cpu_source) {
        return false;
    }
    if (scaled_size == source_size) {
        scaled_frame = stitched_cpu;
    } else {
        cv::resize(stitched_cpu, scaled_frame, scaled_size, 0.0, 0.0, cv::INTER_LINEAR);
    }
    if (prepared_frame_out == nullptr) {
        return false;
    }
    prepared_frame_out->create(target_size, CV_8UC3);
    prepared_frame_out->setTo(cv::Scalar::all(0));
    const cv::Rect roi(
        std::max(0, (target_size.width - scaled_size.width) / 2),
        std::max(0, (target_size.height - scaled_size.height) / 2),
        scaled_size.width,
        scaled_size.height);
    scaled_frame.copyTo((*prepared_frame_out)(roi));
    return true;
}

void StitchEngine::annotate_output_debug_overlay_locked(
    cv::Mat* frame,
    const char* label,
    const SelectedPair& selected_pair,
    bool left_reused,
    bool right_reused,
    double pair_age_ms) const {
    if (frame == nullptr || frame->empty()) {
        return;
    }

    const double min_dim = static_cast<double>(std::min(frame->cols, frame->rows));
    const double font_scale = std::clamp(min_dim / 1200.0, 0.55, 1.15);
    const int thickness = std::max(1, static_cast<int>(std::round(font_scale * 2.0)));
    const int pad = std::max(10, static_cast<int>(std::round(font_scale * 14.0)));
    const int line_gap = std::max(24, static_cast<int>(std::round(font_scale * 30.0)));
    const int baseline_pad = std::max(6, static_cast<int>(std::round(font_scale * 8.0)));

    const std::string reuse_text =
        std::string(left_reused ? "L" : "-") + std::string(right_reused ? "R" : "-");
    const std::vector<std::string> lines = {
        std::string(label) + " frame=" + std::to_string(metrics_.frame_index) +
            " status=" + metrics_.status,
        "seq L=" + std::to_string(selected_pair.left_seq) +
            " R=" + std::to_string(selected_pair.right_seq) +
            " reuse=" + reuse_text +
            " pair_age=" + std::to_string(static_cast<int>(std::llround(pair_age_ms))) + "ms" +
            " skew=" + std::to_string(static_cast<int>(std::llround(metrics_.pair_skew_ms_mean))) + "ms",
        "input_age L=" + std::to_string(static_cast<int>(std::llround(metrics_.left_age_ms))) +
            "ms R=" + std::to_string(static_cast<int>(std::llround(metrics_.right_age_ms))) +
            "ms time=" + format_overlay_clock_now(),
    };

    int max_text_width = 0;
    for (const auto& line : lines) {
        int baseline = 0;
        const auto size = cv::getTextSize(line, cv::FONT_HERSHEY_SIMPLEX, font_scale, thickness, &baseline);
        max_text_width = std::max(max_text_width, size.width);
    }

    const int box_width = std::min(frame->cols, max_text_width + (pad * 2));
    const int box_height = std::min(frame->rows, pad + static_cast<int>(lines.size()) * line_gap + baseline_pad);
    cv::rectangle(*frame, cv::Rect(0, 0, box_width, box_height), cv::Scalar(8, 8, 8), cv::FILLED);

    int y = pad + line_gap - baseline_pad;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        const cv::Scalar color = (index == 0) ? cv::Scalar(90, 255, 90) : cv::Scalar(240, 240, 240);
        cv::putText(
            *frame,
            lines[index],
            cv::Point(pad, y),
            cv::FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            thickness,
            cv::LINE_AA);
        y += line_gap;
    }
}

bool StitchEngine::stitch_pair_locked(
    const cv::Mat& left_frame,
    const cv::Mat& right_frame,
    std::int64_t pair_ts_ns,
    const SelectedPair& selected_pair,
    bool left_reused,
    bool right_reused,
    double pair_age_ms,
    const cv::Mat* left_raw_input,
    const cv::Mat* right_raw_input,
    double output_scale) {
    cv::Mat left_cpu_frame = left_frame;
    cv::Mat right_cpu_frame = right_frame;
    const cv::Size left_runtime_size = !left_cpu_frame.empty()
        ? left_cpu_frame.size()
        : input_frame_size_for_runtime(
            (left_raw_input != nullptr) ? *left_raw_input : cv::Mat{},
            config_.left,
            output_scale);
    const cv::Size right_runtime_size = !right_cpu_frame.empty()
        ? right_cpu_frame.size()
        : input_frame_size_for_runtime(
            (right_raw_input != nullptr) ? *right_raw_input : cv::Mat{},
            config_.right,
            output_scale);
    if (left_runtime_size.width <= 0 || left_runtime_size.height <= 0 ||
        right_runtime_size.width <= 0 || right_runtime_size.height <= 0) {
        metrics_.status = "input decode failed";
        metrics_.stitch_fps = 0.0;
        return false;
    }

    if (!ensure_calibration_locked(left_runtime_size, right_runtime_size)) {
        metrics_.stitch_fps = 0.0;
        return false;
    }

    metrics_.left_mean_luma = !left_cpu_frame.empty()
        ? mean_luma(left_cpu_frame)
        : input_frame_mean_luma((left_raw_input != nullptr) ? *left_raw_input : cv::Mat{}, config_.left);
    metrics_.right_mean_luma = !right_cpu_frame.empty()
        ? mean_luma(right_cpu_frame)
        : input_frame_mean_luma((right_raw_input != nullptr) ? *right_raw_input : cv::Mat{}, config_.right);

    const std::int64_t next_frame_index = metrics_.frame_index + 1;
    const bool sample_heavy_metrics = should_sample_heavy_metrics(next_frame_index);
    const bool probe_enabled = output_config_enabled(config_.output);
    const bool production_enabled = output_config_enabled(config_.production_output);
    const auto probe_caps = hogak::output::get_output_runtime_capabilities(config_.output.runtime);
    const auto production_caps = hogak::output::get_output_runtime_capabilities(config_.production_output.runtime);
    const bool probe_needs_cpu =
        probe_enabled && (config_.output.debug_overlay || probe_caps.requires_cpu_input);
    const bool production_needs_cpu =
        production_enabled && (config_.production_output.debug_overlay || production_caps.requires_cpu_input);
    const bool need_cpu_stitched = probe_needs_cpu || production_needs_cpu || sample_heavy_metrics;

    cv::Mat stitched;
    cv::Mat warped_right;
    cv::Mat canvas_left;
    bool used_gpu_blend = false;

    if (gpu_available_) {
        try {
            const bool left_gpu_fast =
                gpu_nv12_input_supported_ &&
                left_raw_input != nullptr &&
                input_pipe_format_is_nv12(config_.left);
            const bool right_gpu_fast =
                gpu_nv12_input_supported_ &&
                right_raw_input != nullptr &&
                input_pipe_format_is_nv12(config_.right);
            auto ensure_cpu_frame = [&](cv::Mat* cpu_frame, const cv::Mat* raw_input, const StreamConfig& stream_config) -> bool {
                if (cpu_frame == nullptr || !cpu_frame->empty()) {
                    return cpu_frame != nullptr;
                }
                if (raw_input == nullptr) {
                    return false;
                }
                cv::Mat decoded = decode_input_frame_for_stitch(*raw_input, stream_config);
                if (decoded.empty()) {
                    return false;
                }
                *cpu_frame = resize_frame_for_runtime(decoded, output_scale);
                return !cpu_frame->empty();
            };
            auto upload_cpu_frame = [&](const cv::Mat* cpu_frame, cv::cuda::GpuMat* gpu_frame, const char* label) {
                if (cpu_frame == nullptr || cpu_frame->empty()) {
                    throw cv::Exception(cv::Error::StsError, std::string(label) + " input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                }
                gpu_frame->upload(*cpu_frame);
            };
            auto upload_with_nv12_gpu_fallback = [&](const cv::Mat* raw_input,
                                                     const StreamConfig& stream_config,
                                                     cv::Mat* cpu_frame,
                                                     cv::cuda::GpuMat* nv12_gpu,
                                                     cv::cuda::GpuMat* nv12_uv_gpu,
                                                     cv::cuda::GpuMat* decoded_bgr_gpu,
                                                     cv::cuda::GpuMat* final_bgr_gpu,
                                                     const char* label) {
                if (raw_input == nullptr) {
                    if (!ensure_cpu_frame(cpu_frame, raw_input, stream_config)) {
                        throw cv::Exception(cv::Error::StsError, std::string(label) + " input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                    }
                    upload_cpu_frame(cpu_frame, final_bgr_gpu, label);
                    return;
                }

                try {
                    if (!upload_input_frame_for_gpu_stitch(
                            *raw_input,
                            stream_config,
                            output_scale,
                            (cpu_frame != nullptr && !cpu_frame->empty()) ? cpu_frame : nullptr,
                            nv12_gpu,
                            nv12_uv_gpu,
                            decoded_bgr_gpu,
                            final_bgr_gpu)) {
                        throw cv::Exception(cv::Error::StsError, std::string(label) + " nv12 gpu upload failed", __FUNCTION__, __FILE__, __LINE__);
                    }
                } catch (const cv::Exception& e) {
                    if (!is_nv12_gpu_conversion_unsupported(e)) {
                        throw;
                    }
                    gpu_nv12_input_supported_ = false;
                    metrics_.gpu_reason = std::string("cuda nv12 input unsupported: ") + e.what();
                    if (!ensure_cpu_frame(cpu_frame, raw_input, stream_config)) {
                        throw;
                    }
                    upload_cpu_frame(cpu_frame, final_bgr_gpu, label);
                }
            };

            if (left_gpu_fast) {
                upload_with_nv12_gpu_fallback(
                    left_raw_input,
                    config_.left,
                    &left_cpu_frame,
                    &gpu_left_nv12_y_,
                    &gpu_left_nv12_uv_,
                    &gpu_left_decoded_,
                    &gpu_left_input_,
                    "left");
            } else {
                if (!ensure_cpu_frame(&left_cpu_frame, left_raw_input, config_.left)) {
                    throw cv::Exception(cv::Error::StsError, "left input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                }
                upload_cpu_frame(&left_cpu_frame, &gpu_left_input_, "left");
            }
            gpu_left_canvas_.create(output_size_, CV_8UC3);
            gpu_left_canvas_.setTo(cv::Scalar::all(0));
            cv::cuda::GpuMat left_roi_gpu(gpu_left_canvas_, left_roi_);
            gpu_left_input_.copyTo(left_roi_gpu);

            if (right_gpu_fast) {
                upload_with_nv12_gpu_fallback(
                    right_raw_input,
                    config_.right,
                    &right_cpu_frame,
                    &gpu_right_nv12_y_,
                    &gpu_right_nv12_uv_,
                    &gpu_right_decoded_,
                    &gpu_right_input_,
                    "right");
            } else {
                if (!ensure_cpu_frame(&right_cpu_frame, right_raw_input, config_.right)) {
                    throw cv::Exception(cv::Error::StsError, "right input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                }
                upload_cpu_frame(&right_cpu_frame, &gpu_right_input_, "right");
            }
            cv::cuda::warpPerspective(
                gpu_right_input_,
                gpu_right_warped_,
                homography_adjusted_,
                output_size_);
            metrics_.gpu_warp_count += 1;
            if (sample_heavy_metrics) {
                gpu_right_warped_.download(warped_right);
                metrics_.warped_mean_luma = mean_luma(warped_right);
            }

            gpu_stitched_.create(output_size_, CV_8UC3);
            gpu_stitched_.setTo(cv::Scalar::all(0));
            gpu_left_canvas_.copyTo(gpu_stitched_, gpu_only_left_mask_);
            gpu_right_warped_.copyTo(gpu_stitched_, gpu_only_right_mask_);

            if (overlap_roi_.area() > 0) {
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

            if (need_cpu_stitched) {
                gpu_stitched_.download(stitched);
            }
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
        if (left_cpu_frame.empty() && left_raw_input != nullptr) {
            cv::Mat left_decoded = decode_input_frame_for_stitch(*left_raw_input, config_.left);
            left_cpu_frame = resize_frame_for_runtime(left_decoded, output_scale);
        }
        if (right_cpu_frame.empty() && right_raw_input != nullptr) {
            cv::Mat right_decoded = decode_input_frame_for_stitch(*right_raw_input, config_.right);
            right_cpu_frame = resize_frame_for_runtime(right_decoded, output_scale);
        }
        if (left_cpu_frame.empty() || right_cpu_frame.empty()) {
            metrics_.status = "input decode failed";
            metrics_.stitch_fps = 0.0;
            return false;
        }
        canvas_left = cv::Mat::zeros(output_size_, CV_8UC3);
        left_cpu_frame.copyTo(canvas_left(left_roi_));
        cv::warpPerspective(
            right_cpu_frame,
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

    if (!stitched.empty()) {
        latest_stitched_ = stitched;
    }
    if (sample_heavy_metrics && !stitched.empty()) {
        cv::Mat current_stitched_probe_gray = make_motion_probe_gray(stitched);
        metrics_.stitched_motion_mean = probe_motion_mean(previous_stitched_probe_gray_, current_stitched_probe_gray);
        previous_stitched_probe_gray_ = current_stitched_probe_gray;
        cv::Mat stitched_gray;
        cv::cvtColor(stitched, stitched_gray, cv::COLOR_BGR2GRAY);
        metrics_.stitched_mean_luma = cv::mean(stitched_gray)[0];
    }
    metrics_.stitched_count += 1;
    metrics_.frame_index += 1;
    metrics_.status = "stitching";
    metrics_.blend_mode = "seam_feather";
    if (last_worker_timestamp_ns_ > 0) {
        metrics_.stitch_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.stitch_fps = 0.0;
    }

    const auto launch_output_writer = [&](const char* overlay_label,
                                          const OutputConfig& output_config,
                                          std::unique_ptr<hogak::output::OutputWriter>* writer,
                                          std::string* last_error,
                                          std::string* target,
                                          std::string* effective_codec) {
        if (writer == nullptr || last_error == nullptr || target == nullptr || effective_codec == nullptr) {
            return;
        }
        if (output_config.runtime == "none" || output_config.target.empty()) {
            if (*writer != nullptr) {
                (*writer)->stop();
                writer->reset();
            }
            return;
        }
        cv::Mat prepared_frame;
        const cv::cuda::GpuMat* prepared_gpu_frame = nullptr;
        const auto output_caps = hogak::output::get_output_runtime_capabilities(output_config.runtime);
        const bool needs_cpu_prepared_frame =
            output_config.debug_overlay || output_caps.requires_cpu_input || !output_caps.supports_gpu_input;
        const bool prepared_ok = prepare_output_frame_locked(
            output_config,
            stitched,
            used_gpu_blend ? &gpu_stitched_ : nullptr,
            needs_cpu_prepared_frame ? &prepared_frame : nullptr,
            &prepared_gpu_frame);
        const cv::Mat* cpu_submit_frame = nullptr;
        const cv::cuda::GpuMat* gpu_submit_frame = nullptr;
        if (prepared_ok) {
            if (!prepared_frame.empty()) {
                cpu_submit_frame = &prepared_frame;
            }
            if (prepared_gpu_frame != nullptr && !prepared_gpu_frame->empty()) {
                gpu_submit_frame = prepared_gpu_frame;
            }
        } else {
            cpu_submit_frame = &stitched;
            if (used_gpu_blend && !gpu_stitched_.empty()) {
                gpu_submit_frame = &gpu_stitched_;
            }
        }
        cv::Mat annotated_frame;
        if (output_config.debug_overlay) {
            if (cpu_submit_frame != nullptr && !cpu_submit_frame->empty()) {
                annotated_frame = cpu_submit_frame->clone();
            } else if (gpu_submit_frame != nullptr && !gpu_submit_frame->empty()) {
                try {
                    gpu_submit_frame->download(annotated_frame);
                } catch (const cv::Exception& e) {
                    *last_error = std::string("debug overlay gpu download failed: ") + e.what();
                    return;
                }
            }
            if (annotated_frame.empty()) {
                *last_error = "debug overlay frame unavailable";
                return;
            }
            annotate_output_debug_overlay_locked(
                &annotated_frame,
                overlay_label,
                selected_pair,
                left_reused,
                right_reused,
                pair_age_ms);
            cpu_submit_frame = &annotated_frame;
            gpu_submit_frame = nullptr;
        }
        hogak::output::OutputFrame submit_frame;
        if (cpu_submit_frame != nullptr && !cpu_submit_frame->empty()) {
            submit_frame.cpu_frame = cpu_submit_frame;
        }
        if (gpu_submit_frame != nullptr && !gpu_submit_frame->empty()) {
            submit_frame.gpu_frame = gpu_submit_frame;
        }
        if (submit_frame.empty()) {
            *last_error = "output submit frame unavailable";
            return;
        }
        submit_frame.input_prepared =
            (submit_frame.width() != stitched.cols) || (submit_frame.height() != stitched.rows);
        if (*writer == nullptr) {
            *writer = hogak::output::create_output_writer(output_config.runtime);
            if (*writer == nullptr) {
                *last_error = "unsupported output runtime: " + output_config.runtime;
                return;
            }
            const double requested_output_fps = (output_config.fps > 0.0) ? output_config.fps : 0.0;
            const double output_fps = (requested_output_fps > 0.0)
                ? requested_output_fps
                : std::max({metrics_.worker_fps, metrics_.left_fps, metrics_.right_fps, 30.0});
            if (!(*writer)->start(
                    output_config,
                    config_.ffmpeg_bin,
                    submit_frame.width(),
                    submit_frame.height(),
                    output_fps,
                    submit_frame.input_prepared)) {
                *last_error = (*writer)->last_error();
                if (last_error->empty()) {
                    *last_error = "failed to start output writer";
                }
                writer->reset();
                return;
            }
            *target = output_config.target;
            *effective_codec = (*writer)->effective_codec();
        }
        if (*writer != nullptr) {
            (*writer)->submit(submit_frame, pair_ts_ns);
        }
    };

    launch_output_writer(
        "PROBE",
        config_.output,
        &output_writer_,
        &metrics_.output_last_error,
        &metrics_.output_target,
        &metrics_.output_effective_codec);
    launch_output_writer(
        "TX",
        config_.production_output,
        &production_output_writer_,
        &metrics_.production_output_last_error,
        &metrics_.production_output_target,
        &metrics_.production_output_effective_codec);

    return true;
}

void StitchEngine::update_metrics_locked() {
    const auto left = g_left_reader.snapshot();
    const auto right = g_right_reader.snapshot();
    const auto now_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count();

    metrics_.left_fps = left.fps;
    metrics_.right_fps = right.fps;
    metrics_.left_avg_frame_interval_ms = left.avg_frame_interval_ms;
    metrics_.right_avg_frame_interval_ms = right.avg_frame_interval_ms;
    metrics_.left_last_frame_interval_ms = left.last_frame_interval_ms;
    metrics_.right_last_frame_interval_ms = right.last_frame_interval_ms;
    metrics_.left_max_frame_interval_ms = left.max_frame_interval_ms;
    metrics_.right_max_frame_interval_ms = right.max_frame_interval_ms;
    metrics_.left_late_frame_intervals = left.late_frame_intervals;
    metrics_.right_late_frame_intervals = right.late_frame_intervals;
    metrics_.left_buffer_span_ms = left.buffer_span_ms;
    metrics_.right_buffer_span_ms = right.buffer_span_ms;
    metrics_.left_avg_read_ms = left.avg_read_ms;
    metrics_.right_avg_read_ms = right.avg_read_ms;
    metrics_.left_max_read_ms = left.max_read_ms;
    metrics_.right_max_read_ms = right.max_read_ms;
    metrics_.left_buffer_seq_span = left.buffer_seq_span;
    metrics_.right_buffer_seq_span = right.buffer_seq_span;
    metrics_.left_age_ms =
        left.latest_timestamp_ns > 0 ? static_cast<double>(now_ns - left.latest_timestamp_ns) / 1'000'000.0 : 0.0;
    metrics_.right_age_ms =
        right.latest_timestamp_ns > 0 ? static_cast<double>(now_ns - right.latest_timestamp_ns) / 1'000'000.0 : 0.0;
    metrics_.selected_left_lag_ms = 0.0;
    metrics_.selected_right_lag_ms = 0.0;
    metrics_.selected_left_lag_frames = 0;
    metrics_.selected_right_lag_frames = 0;
    metrics_.left_motion_mean = left.motion_mean;
    metrics_.right_motion_mean = right.motion_mean;
    metrics_.left_frames_total = left.frames_total;
    metrics_.right_frames_total = right.frames_total;
    metrics_.left_buffered_frames = left.buffered_frames;
    metrics_.right_buffered_frames = right.buffered_frames;
    metrics_.left_stale_drops = left.stale_drops;
    metrics_.right_stale_drops = right.stale_drops;
    metrics_.left_launch_failures = left.launch_failures;
    metrics_.right_launch_failures = right.launch_failures;
    metrics_.left_read_failures = left.read_failures;
    metrics_.right_read_failures = right.read_failures;
    metrics_.left_reader_restarts = left_reader_restart_count_;
    metrics_.right_reader_restarts = right_reader_restart_count_;
    metrics_.left_content_frozen = left.content_frozen;
    metrics_.right_content_frozen = right.content_frozen;
    metrics_.left_frozen_duration_sec = left.frozen_duration_sec;
    metrics_.right_frozen_duration_sec = right.frozen_duration_sec;
    metrics_.left_freeze_restarts = left.freeze_restarts;
    metrics_.right_freeze_restarts = right.freeze_restarts;
    metrics_.left_last_error = left.last_error;
    metrics_.right_last_error = right.last_error;
    metrics_.wait_paired_fresh_left_age_ms_avg =
        (metrics_.wait_paired_fresh_left_count > 0)
            ? (wait_paired_fresh_left_age_sum_ms_ / static_cast<double>(metrics_.wait_paired_fresh_left_count))
            : 0.0;
    metrics_.wait_paired_fresh_right_age_ms_avg =
        (metrics_.wait_paired_fresh_right_count > 0)
            ? (wait_paired_fresh_right_age_sum_ms_ / static_cast<double>(metrics_.wait_paired_fresh_right_count))
            : 0.0;
    metrics_.sync_pair_mode = config_.sync_pair_mode;
    metrics_.gpu_feature_enabled = false;
    metrics_.gpu_feature_reason = "not implemented in native runtime yet";
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.calibrated = calibrated_;
    metrics_.output_width = (config_.output.width > 0) ? config_.output.width : output_size_.width;
    metrics_.output_height = (config_.output.height > 0) ? config_.output.height : output_size_.height;
    if (config_.production_output.runtime == "ffmpeg" && !config_.production_output.target.empty()) {
        metrics_.production_output_width =
            (config_.production_output.width > 0) ? config_.production_output.width : output_size_.width;
        metrics_.production_output_height =
            (config_.production_output.height > 0) ? config_.production_output.height : output_size_.height;
    } else {
        metrics_.production_output_width = 0;
        metrics_.production_output_height = 0;
    }
    metrics_.output_active = (output_writer_ != nullptr) && output_writer_->active();
    metrics_.output_frames_written = (output_writer_ != nullptr) ? output_writer_->frames_written() : 0;
    metrics_.output_frames_dropped = (output_writer_ != nullptr) ? output_writer_->frames_dropped() : 0;
    metrics_.output_command_line =
        (output_writer_ != nullptr) ? output_writer_->command_line() : metrics_.output_command_line;
    metrics_.output_effective_codec =
        (output_writer_ != nullptr) ? output_writer_->effective_codec() : metrics_.output_effective_codec;
    metrics_.output_last_error = (output_writer_ != nullptr) ? output_writer_->last_error() : metrics_.output_last_error;
    metrics_.production_output_active =
        (production_output_writer_ != nullptr) && production_output_writer_->active();
    metrics_.production_output_frames_written =
        (production_output_writer_ != nullptr) ? production_output_writer_->frames_written() : 0;
    metrics_.production_output_frames_dropped =
        (production_output_writer_ != nullptr) ? production_output_writer_->frames_dropped() : 0;
    metrics_.production_output_command_line =
        (production_output_writer_ != nullptr)
            ? production_output_writer_->command_line()
            : metrics_.production_output_command_line;
    metrics_.production_output_effective_codec =
        (production_output_writer_ != nullptr)
            ? production_output_writer_->effective_codec()
            : metrics_.production_output_effective_codec;
    metrics_.production_output_last_error =
        (production_output_writer_ != nullptr)
            ? production_output_writer_->last_error()
            : metrics_.production_output_last_error;
    if (metrics_.stitched_count < last_stitched_count_) {
        last_stitched_count_ = metrics_.stitched_count;
        last_stitch_timestamp_ns_ = 0;
    }
    if (metrics_.stitched_count > last_stitched_count_) {
        const auto delta_frames = metrics_.stitched_count - last_stitched_count_;
        const auto delta_ns = now_ns - last_stitch_timestamp_ns_;
        if (last_stitch_timestamp_ns_ > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics_.stitch_actual_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        last_stitched_count_ = metrics_.stitched_count;
        last_stitch_timestamp_ns_ = now_ns;
    }
    if (metrics_.output_frames_written < last_output_frames_written_) {
        last_output_frames_written_ = metrics_.output_frames_written;
        last_output_timestamp_ns_ = 0;
    }
    if (metrics_.output_frames_written > last_output_frames_written_) {
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
    if (metrics_.production_output_frames_written < last_production_output_frames_written_) {
        last_production_output_frames_written_ = metrics_.production_output_frames_written;
        last_production_output_timestamp_ns_ = 0;
    }
    if (metrics_.production_output_frames_written > last_production_output_frames_written_) {
        const auto delta_frames =
            metrics_.production_output_frames_written - last_production_output_frames_written_;
        const auto delta_ns = now_ns - last_production_output_timestamp_ns_;
        if (last_production_output_timestamp_ns_ > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics_.production_output_written_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        last_production_output_frames_written_ = metrics_.production_output_frames_written;
        last_production_output_timestamp_ns_ = now_ns;
    } else if (!metrics_.production_output_active) {
        metrics_.production_output_written_fps = 0.0;
    }

    const auto set_wait_status = [&](const char* status, std::int64_t* counter) {
        metrics_.status = status;
        if (counter != nullptr) {
            *counter += 1;
        }
    };

    if (!left.has_frame || !right.has_frame) {
        set_wait_status("waiting for both streams", &metrics_.wait_both_streams_count);
        metrics_.worker_fps = 0.0;
        metrics_.stitch_fps = 0.0;
        return;
    }

    if (metrics_.left_age_ms > kReaderRestartAgeMs) {
        restart_reader_locked(true, "input age exceeded threshold");
    }
    if (metrics_.right_age_ms > kReaderRestartAgeMs) {
        restart_reader_locked(false, "input age exceeded threshold");
    }

    metrics_.pair_skew_ms_mean =
        std::abs(static_cast<double>(left.latest_timestamp_ns - right.latest_timestamp_ns)) / 1'000'000.0;
    SelectedPair pair;
    if (!select_pair_locked(left, right, &pair)) {
        if (metrics_.status.empty() || metrics_.status == "stitching") {
            metrics_.status = "waiting sync pair";
        }
        if (metrics_.status == "waiting sync pair") {
            metrics_.wait_sync_pair_count += 1;
        }
        return;
    }

    const bool has_new_left = pair.left_seq > last_left_seq_;
    const bool has_new_right = pair.right_seq > last_right_seq_;
    metrics_.selected_left_lag_frames = std::max<std::int64_t>(0, left.latest_seq - pair.left_seq);
    metrics_.selected_right_lag_frames = std::max<std::int64_t>(0, right.latest_seq - pair.right_seq);
    metrics_.selected_left_lag_ms =
        (left.latest_timestamp_ns > 0 && pair.left_ts_ns > 0 && left.latest_timestamp_ns >= pair.left_ts_ns)
            ? static_cast<double>(left.latest_timestamp_ns - pair.left_ts_ns) / 1'000'000.0
            : 0.0;
    metrics_.selected_right_lag_ms =
        (right.latest_timestamp_ns > 0 && pair.right_ts_ns > 0 && right.latest_timestamp_ns >= pair.right_ts_ns)
            ? static_cast<double>(right.latest_timestamp_ns - pair.right_ts_ns) / 1'000'000.0
            : 0.0;
    if (!has_new_left && !has_new_right) {
        set_wait_status("waiting next frame", &metrics_.wait_next_frame_count);
        return;
    }
    if (!has_new_left || !has_new_right) {
        if (!config_.allow_frame_reuse) {
            set_wait_status("waiting paired fresh frame", &metrics_.wait_paired_fresh_count);
            record_wait_paired_fresh_locked(!has_new_left, !has_new_right);
            return;
        }
        const bool left_reused = !has_new_left;
        const bool right_reused = !has_new_right;
        const double max_reuse_age_ms = std::max(1.0, config_.pair_reuse_max_age_ms);
        const int max_consecutive_reuse = std::max(1, config_.pair_reuse_max_consecutive);
        const double left_age_ms = static_cast<double>(std::max<std::int64_t>(0, now_ns - pair.left_ts_ns)) / 1'000'000.0;
        const double right_age_ms = static_cast<double>(std::max<std::int64_t>(0, now_ns - pair.right_ts_ns)) / 1'000'000.0;
        const bool can_reuse_left =
            left_reused &&
            left_age_ms <= max_reuse_age_ms &&
            consecutive_left_reuse_ < max_consecutive_reuse;
        const bool can_reuse_right =
            right_reused &&
            right_age_ms <= max_reuse_age_ms &&
            consecutive_right_reuse_ < max_consecutive_reuse;
        if ((left_reused && !can_reuse_left) || (right_reused && !can_reuse_right)) {
            set_wait_status("waiting paired fresh frame", &metrics_.wait_paired_fresh_count);
            record_wait_paired_fresh_locked(left_reused && !can_reuse_left, right_reused && !can_reuse_right);
            return;
        }
        metrics_.status = "stitching realtime fallback pair";
        metrics_.realtime_fallback_pair_count += 1;
    }

    const auto pair_ts_ns = std::max(pair.left_ts_ns, pair.right_ts_ns);
    const auto scheduler_pair_ts_ns = std::max(
        pair.left_ts_ns,
        pair.right_ts_ns + static_cast<std::int64_t>(config_.sync_manual_offset_ms * 1'000'000.0));
    const double pair_age_ms = static_cast<double>(std::max<std::int64_t>(0, now_ns - pair_ts_ns)) / 1'000'000.0;
    if (last_worker_timestamp_ns_ > 0) {
        metrics_.worker_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.worker_fps = 0.0;
    }

    if (has_new_left) {
        last_left_seq_ = pair.left_seq;
        consecutive_left_reuse_ = 0;
    } else {
        consecutive_left_reuse_ += 1;
    }
    if (has_new_right) {
        last_right_seq_ = pair.right_seq;
        consecutive_right_reuse_ = 0;
    } else {
        consecutive_right_reuse_ += 1;
    }
    if (config_.sync_pair_mode == "service") {
        last_service_pair_ts_ns_ = scheduler_pair_ts_ns;
    }
    if (config_.stitch_every_n > 1 && (std::max(pair.left_seq, pair.right_seq) % config_.stitch_every_n) != 0) {
        metrics_.reused_count += 1;
        metrics_.status = "skipping per stitch_every_n";
        last_worker_timestamp_ns_ = pair_ts_ns;
        return;
    }

    const double output_scale = clamp_output_scale(config_.stitch_output_scale);
    const bool use_gpu_nv12_fast_path =
        gpu_available_ &&
        gpu_nv12_input_supported_ &&
        input_pipe_format_is_nv12(config_.left) &&
        input_pipe_format_is_nv12(config_.right);
    cv::Mat left_frame;
    cv::Mat right_frame;
    const cv::Mat* left_raw_input = nullptr;
    const cv::Mat* right_raw_input = nullptr;
    if (use_gpu_nv12_fast_path) {
        left_raw_input = &pair.left_frame;
        right_raw_input = &pair.right_frame;
    } else {
        cv::Mat left_decoded = decode_input_frame_for_stitch(pair.left_frame, config_.left);
        cv::Mat right_decoded = decode_input_frame_for_stitch(pair.right_frame, config_.right);
        if (left_decoded.empty() || right_decoded.empty()) {
            metrics_.status = "input decode failed";
            return;
        }
        left_frame = resize_frame_for_runtime(left_decoded, output_scale);
        right_frame = resize_frame_for_runtime(right_decoded, output_scale);
    }
    const bool stitched_ok = stitch_pair_locked(
        left_frame,
        right_frame,
        pair_ts_ns,
        pair,
        !has_new_left,
        !has_new_right,
        pair_age_ms,
        left_raw_input,
        right_raw_input,
        output_scale);
    last_worker_timestamp_ns_ = pair_ts_ns;

    if (!stitched_ok) {
        return;
    }
}

}  // namespace hogak::engine
