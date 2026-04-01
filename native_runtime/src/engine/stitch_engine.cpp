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

bool input_pipe_format_is_nv12(const StreamConfig& config);

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

bool extract_json_object_for_key(const std::string& text, const std::string& key, std::string* object_text_out) {
    if (object_text_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto first_brace = text.find('{', key_pos);
    if (first_brace == std::string::npos) {
        return false;
    }

    int depth = 0;
    for (std::size_t index = first_brace; index < text.size(); ++index) {
        const char ch = text[index];
        if (ch == '{') {
            depth += 1;
        } else if (ch == '}') {
            depth -= 1;
            if (depth == 0) {
                *object_text_out = text.substr(first_brace, index - first_brace + 1);
                return true;
            }
        }
    }
    return false;
}

bool extract_json_string_for_key(const std::string& text, const std::string& key, std::string* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto colon_pos = text.find(':', key_pos + key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    const auto quote_pos = text.find('"', colon_pos + 1);
    if (quote_pos == std::string::npos) {
        return false;
    }
    const auto end_quote_pos = text.find('"', quote_pos + 1);
    if (end_quote_pos == std::string::npos || end_quote_pos <= quote_pos) {
        return false;
    }
    *value_out = text.substr(quote_pos + 1, end_quote_pos - quote_pos - 1);
    return true;
}

bool extract_json_bool(const std::string& text, const std::string& key, bool* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto colon_pos = text.find(':', key_pos + key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    std::size_t value_begin = colon_pos + 1;
    while (value_begin < text.size() && std::isspace(static_cast<unsigned char>(text[value_begin]))) {
        value_begin += 1;
    }
    if (text.compare(value_begin, 4, "true") == 0) {
        *value_out = true;
        return true;
    }
    if (text.compare(value_begin, 5, "false") == 0) {
        *value_out = false;
        return true;
    }
    return false;
}

bool extract_json_number_for_key(const std::string& text, const std::string& key, double* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto colon_pos = text.find(':', key_pos + key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    std::size_t value_begin = colon_pos + 1;
    while (value_begin < text.size() && std::isspace(static_cast<unsigned char>(text[value_begin]))) {
        value_begin += 1;
    }
    std::size_t value_end = value_begin;
    while (value_end < text.size()) {
        const char ch = text[value_end];
        if (!(std::isdigit(static_cast<unsigned char>(ch)) || ch == '-' || ch == '+' || ch == '.' || ch == 'e' || ch == 'E')) {
            break;
        }
        value_end += 1;
    }
    if (value_end <= value_begin) {
        return false;
    }
    try {
        *value_out = std::stod(text.substr(value_begin, value_end - value_begin));
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool parse_numeric_vector(const std::string& text, std::vector<double>* out) {
    if (out == nullptr) {
        return false;
    }
    out->clear();
    std::istringstream values(sanitize_numeric_text(text));
    double value = 0.0;
    while (values >> value) {
        out->push_back(value);
    }
    return !out->empty();
}

std::string runtime_geometry_artifact_candidate_path(const EngineConfig& config) {
    if (!config.geometry.artifact_file.empty()) {
        return config.geometry.artifact_file;
    }
    if (config.homography_file.empty()) {
        return {};
    }
    std::string candidate = config.homography_file;
    const auto homography_pos = candidate.find("homography");
    if (homography_pos != std::string::npos) {
        candidate.replace(homography_pos, std::string("homography").size(), "geometry");
        return candidate;
    }
    const auto dot_pos = candidate.find_last_of('.');
    if (dot_pos != std::string::npos) {
        return candidate.substr(0, dot_pos) + ".geometry" + candidate.substr(dot_pos);
    }
    return candidate + ".geometry.json";
}

struct DistortionProfileData {
    std::string source = "saved";
    std::string model = "opencv_pinhole";
    double confidence = 0.0;
    double fit_score = 0.0;
    std::int64_t line_count = 0;
    std::int64_t frame_count_used = 0;
    cv::Size image_size{};
    cv::Mat camera_matrix{};
    cv::Mat projection_matrix{};
    cv::Mat dist_coeffs{};
};

bool load_homography_from_file(
    const std::string& path,
    cv::Mat* homography_out,
    std::string* distortion_reference_out) {
    if (homography_out == nullptr) {
        return false;
    }
    if (distortion_reference_out != nullptr) {
        *distortion_reference_out = "raw";
    }

    std::ifstream file(path);
    if (!file.is_open()) {
        return false;
    }
    std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (distortion_reference_out != nullptr) {
        std::string distortion_reference;
        if (extract_json_string_for_key(text, "\"distortion_reference\"", &distortion_reference) &&
            !distortion_reference.empty()) {
            *distortion_reference_out = distortion_reference;
        }
    }

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
                if (distortion_reference_out != nullptr) {
                    std::string distortion_reference;
                    cv::FileNode distortion_reference_node = fs["distortion_reference"];
                    if (!distortion_reference_node.empty()) {
                        distortion_reference_node >> distortion_reference;
                        if (!distortion_reference.empty()) {
                            *distortion_reference_out = distortion_reference;
                        }
                    }
                }
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

bool load_distortion_profile_from_file(const std::string& path, DistortionProfileData* profile_out) {
    if (profile_out == nullptr) {
        return false;
    }
    *profile_out = DistortionProfileData{};

    std::ifstream file(path);
    if (!file.is_open()) {
        return false;
    }
    std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());

    try {
        cv::FileStorage fs(path, cv::FileStorage::READ | cv::FileStorage::FORMAT_AUTO);
        if (fs.isOpened() && fs.root().isMap()) {
            DistortionProfileData parsed;
            cv::Mat camera_matrix;
            cv::Mat projection_matrix;
            cv::Mat dist_coeffs;
            std::vector<int> image_size;
            cv::FileNode model_node = fs["model"];
            cv::FileNode source_node = fs["source"];
            cv::FileNode confidence_node = fs["confidence"];
            cv::FileNode fit_score_node = fs["fit_score"];
            cv::FileNode line_count_node = fs["line_count"];
            cv::FileNode frame_count_used_node = fs["frame_count_used"];
            cv::FileNode image_size_node = fs["image_size"];
            fs["camera_matrix"] >> camera_matrix;
            fs["projection_matrix"] >> projection_matrix;
            fs["dist_coeffs"] >> dist_coeffs;
            if (!model_node.empty()) {
                model_node >> parsed.model;
            }
            if (!source_node.empty()) {
                source_node >> parsed.source;
            }
            if (!confidence_node.empty()) {
                confidence_node >> parsed.confidence;
            }
            if (!fit_score_node.empty()) {
                fit_score_node >> parsed.fit_score;
            }
            if (!line_count_node.empty()) {
                line_count_node >> parsed.line_count;
            }
            if (!frame_count_used_node.empty()) {
                frame_count_used_node >> parsed.frame_count_used;
            }
            if (!image_size_node.empty()) {
                image_size_node >> image_size;
            }
            if (!camera_matrix.empty() &&
                !dist_coeffs.empty() &&
                camera_matrix.rows == 3 &&
                camera_matrix.cols == 3 &&
                image_size.size() >= 2 &&
                image_size[0] > 0 &&
                image_size[1] > 0) {
                camera_matrix.convertTo(parsed.camera_matrix, CV_64F);
                if (!projection_matrix.empty() && projection_matrix.rows == 3 && projection_matrix.cols == 3) {
                    projection_matrix.convertTo(parsed.projection_matrix, CV_64F);
                } else {
                    parsed.projection_matrix = parsed.camera_matrix.clone();
                }
                dist_coeffs = dist_coeffs.reshape(1, 1);
                dist_coeffs.convertTo(parsed.dist_coeffs, CV_64F);
                parsed.image_size = cv::Size(image_size[0], image_size[1]);
                if (parsed.model.empty()) {
                    parsed.model = "opencv_pinhole";
                }
                if (parsed.source.empty()) {
                    parsed.source = "saved";
                }
                *profile_out = parsed;
                return true;
            }
        }
    } catch (const cv::Exception&) {
        // Fall through to permissive JSON parsing below.
    }

    DistortionProfileData parsed;
    std::string image_size_text;
    std::string camera_matrix_text;
    std::string dist_coeffs_text;
    std::string projection_matrix_text;
    std::vector<double> image_size_values;
    std::vector<double> camera_matrix_values;
    std::vector<double> projection_matrix_values;
    std::vector<double> dist_coeff_values;
    extract_json_string_for_key(text, "\"model\"", &parsed.model);
    extract_json_string_for_key(text, "\"source\"", &parsed.source);
    extract_json_number_for_key(text, "\"confidence\"", &parsed.confidence);
    extract_json_number_for_key(text, "\"fit_score\"", &parsed.fit_score);
    {
        double numeric_value = 0.0;
        if (extract_json_number_for_key(text, "\"line_count\"", &numeric_value)) {
            parsed.line_count = static_cast<std::int64_t>(std::llround(numeric_value));
        }
        if (extract_json_number_for_key(text, "\"frame_count_used\"", &numeric_value)) {
            parsed.frame_count_used = static_cast<std::int64_t>(std::llround(numeric_value));
        }
    }
    if (!extract_json_array_for_key(text, "\"image_size\"", &image_size_text) ||
        !extract_json_array_for_key(text, "\"camera_matrix\"", &camera_matrix_text) ||
        !extract_json_array_for_key(text, "\"dist_coeffs\"", &dist_coeffs_text) ||
        !parse_numeric_vector(image_size_text, &image_size_values) ||
        !parse_numeric_vector(camera_matrix_text, &camera_matrix_values) ||
        !parse_numeric_vector(dist_coeffs_text, &dist_coeff_values)) {
        return false;
    }
    if (image_size_values.size() < 2 || camera_matrix_values.size() < 9 || dist_coeff_values.empty()) {
        return false;
    }
    parsed.image_size = cv::Size(
        static_cast<int>(std::llround(image_size_values[0])),
        static_cast<int>(std::llround(image_size_values[1])));
    if (parsed.image_size.width <= 0 || parsed.image_size.height <= 0) {
        return false;
    }
    parsed.camera_matrix = cv::Mat(3, 3, CV_64F, camera_matrix_values.data()).clone();
    if (extract_json_array_for_key(text, "\"projection_matrix\"", &projection_matrix_text) &&
        parse_numeric_vector(projection_matrix_text, &projection_matrix_values) &&
        projection_matrix_values.size() >= 9) {
        parsed.projection_matrix = cv::Mat(3, 3, CV_64F, projection_matrix_values.data()).clone();
    } else {
        parsed.projection_matrix = parsed.camera_matrix.clone();
    }
    parsed.dist_coeffs = cv::Mat(1, static_cast<int>(dist_coeff_values.size()), CV_64F, dist_coeff_values.data()).clone();
    if (parsed.model.empty()) {
        parsed.model = "opencv_pinhole";
    }
    if (parsed.source.empty()) {
        parsed.source = "saved";
    }
    *profile_out = parsed;
    return true;
}

struct RuntimeGeometryArtifactData {
    std::string model = "planar-homography";
    std::string alignment_model = "homography";
    std::string residual_model = "none";
    std::string artifact_path;
    std::string source_homography_file;
    std::string source_geometry_file;
    cv::Mat alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
    cv::Size output_size{};
    cv::Size left_input_size{};
    cv::Size right_input_size{};
    std::string left_projection_model = "cylindrical";
    std::string right_projection_model = "cylindrical";
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

bool load_runtime_geometry_artifact_from_file(const std::string& path, RuntimeGeometryArtifactData* artifact_out) {
    if (artifact_out == nullptr) {
        return false;
    }
    *artifact_out = RuntimeGeometryArtifactData{};
    artifact_out->artifact_path = path;

    auto sanitize_projection_side = [&](double* focal_px,
                                        double* center_x,
                                        double* center_y,
                                        const cv::Size& input_size) {
        if (focal_px == nullptr || center_x == nullptr || center_y == nullptr) {
            return;
        }
        const cv::Size reference_size =
            (input_size.width > 0 && input_size.height > 0)
                ? input_size
                : artifact_out->output_size;
        const int reference_width = std::max(1, reference_size.width);
        const int reference_height = std::max(1, reference_size.height);
        const double reference_max_dim =
            static_cast<double>(std::max(reference_width, reference_height));
        const bool center_out_of_bounds =
            *center_x <= 0.0 ||
            *center_x >= static_cast<double>(reference_width) ||
            *center_y <= 0.0 ||
            *center_y >= static_cast<double>(reference_height);
        const bool center_matches_output_canvas =
            artifact_out->output_size.width > 0 &&
            artifact_out->output_size.height > 0 &&
            (reference_width != artifact_out->output_size.width ||
             reference_height != artifact_out->output_size.height) &&
            std::abs(*center_x - static_cast<double>(artifact_out->output_size.width) * 0.5) <= 1.0 &&
            std::abs(*center_y - static_cast<double>(artifact_out->output_size.height) * 0.5) <= 1.0;
        const bool should_reset_center = center_out_of_bounds || center_matches_output_canvas;
        const double output_default_focal_px =
            static_cast<double>(std::max(artifact_out->output_size.width, artifact_out->output_size.height)) * 0.90;
        const bool focal_matches_output_canvas =
            should_reset_center &&
            artifact_out->output_size.width > 0 &&
            artifact_out->output_size.height > 0 &&
            std::abs(*focal_px - output_default_focal_px) <= 1.0;
        if (*focal_px <= 0.0 ||
            focal_matches_output_canvas ||
            (center_out_of_bounds && *focal_px > reference_max_dim * 1.25)) {
            *focal_px = reference_max_dim * 0.90;
        }
        if (should_reset_center || *center_x <= 0.0 || *center_x >= static_cast<double>(reference_width)) {
            *center_x = static_cast<double>(reference_width) * 0.5;
        }
        if (should_reset_center || *center_y <= 0.0 || *center_y >= static_cast<double>(reference_height)) {
            *center_y = static_cast<double>(reference_height) * 0.5;
        }
    };
    auto sanitize_virtual_projection_side = [&](double* source_focal_px,
                                                double* source_center_x,
                                                double* source_center_y,
                                                double* virtual_focal_px,
                                                double* virtual_center_x,
                                                double* virtual_center_y,
                                                const cv::Size& input_size) {
        sanitize_projection_side(source_focal_px, source_center_x, source_center_y, input_size);
        if (virtual_focal_px == nullptr || virtual_center_x == nullptr || virtual_center_y == nullptr) {
            return;
        }
        if (*virtual_focal_px <= 0.0) {
            *virtual_focal_px = *source_focal_px;
        }
        if (*virtual_center_x <= 0.0 || *virtual_center_x >= static_cast<double>(std::max(1, input_size.width))) {
            *virtual_center_x = static_cast<double>(std::max(1, input_size.width)) * 0.5;
        }
        if (*virtual_center_y <= 0.0 || *virtual_center_y >= static_cast<double>(std::max(1, input_size.height))) {
            *virtual_center_y = static_cast<double>(std::max(1, input_size.height)) * 0.5;
        }
    };
    auto sanitize_crop_rect = [&]() {
        if (!artifact_out->crop_enabled) {
            artifact_out->crop_rect = cv::Rect();
            return;
        }
        const int canvas_width = std::max(0, artifact_out->output_size.width);
        const int canvas_height = std::max(0, artifact_out->output_size.height);
        int x = std::max(0, artifact_out->crop_rect.x);
        int y = std::max(0, artifact_out->crop_rect.y);
        int width = std::max(0, artifact_out->crop_rect.width);
        int height = std::max(0, artifact_out->crop_rect.height);
        if (canvas_width > 0) {
            x = std::min(x, canvas_width);
            width = std::min(width, std::max(0, canvas_width - x));
        }
        if (canvas_height > 0) {
            y = std::min(y, canvas_height);
            height = std::min(height, std::max(0, canvas_height - y));
        }
        artifact_out->crop_rect = cv::Rect(x, y, width, height);
        if (width <= 0 || height <= 0) {
            artifact_out->crop_enabled = false;
            artifact_out->crop_rect = cv::Rect();
        }
    };
    auto normalize_projection_model = [](std::string model) {
        std::transform(model.begin(), model.end(), model.begin(), [](unsigned char ch) {
            return static_cast<char>(std::tolower(ch));
        });
        if (model == "virtual_center_rectilinear" || model == "virtual-center-rectilinear") {
            return std::string("virtual-center-rectilinear");
        }
        if (model == "cylindrical_affine" || model == "cylindrical-affine") {
            return std::string("cylindrical");
        }
        if (model.empty()) {
            return std::string("cylindrical");
        }
        return model;
    };

    try {
        cv::FileStorage fs(path, cv::FileStorage::READ | cv::FileStorage::FORMAT_AUTO);
        if (!fs.isOpened()) {
            return false;
        }

        auto read_size = [](const cv::FileNode& node, cv::Size* size_out) {
            if (size_out == nullptr || node.empty() || !node.isSeq()) {
                return false;
            }
            std::vector<double> values;
            node >> values;
            if (values.size() < 2) {
                return false;
            }
            *size_out = cv::Size(static_cast<int>(std::llround(values[0])), static_cast<int>(std::llround(values[1])));
            return size_out->width > 0 && size_out->height > 0;
        };
        auto read_matrix_3x3 = [](const cv::FileNode& node, cv::Mat* matrix_out) {
            if (matrix_out == nullptr || node.empty() || !node.isSeq()) {
                return false;
            }
            std::vector<double> values;
            node >> values;
            if (values.size() < 9) {
                return false;
            }
            *matrix_out = cv::Mat(3, 3, CV_64F, values.data()).clone();
            return true;
        };
        auto read_float_grid = [](const cv::FileNode& node, cv::Mat* grid_out) {
            if (grid_out == nullptr || node.empty() || !node.isSeq()) {
                return false;
            }
            const int rows = static_cast<int>(node.size());
            if (rows <= 0) {
                return false;
            }
            int cols = -1;
            std::vector<float> values;
            values.reserve(static_cast<std::size_t>(rows) * 16U);
            for (const auto& row_node : node) {
                if (!row_node.isSeq()) {
                    return false;
                }
                const int row_cols = static_cast<int>(row_node.size());
                if (row_cols <= 0) {
                    return false;
                }
                if (cols < 0) {
                    cols = row_cols;
                } else if (cols != row_cols) {
                    return false;
                }
                for (const auto& cell_node : row_node) {
                    double numeric_value = 0.0;
                    cell_node >> numeric_value;
                    values.push_back(static_cast<float>(numeric_value));
                }
            }
            if (cols <= 0 || static_cast<int>(values.size()) != rows * cols) {
                return false;
            }
            *grid_out = cv::Mat(rows, cols, CV_32F, values.data()).clone();
            return true;
        };

        cv::FileNode source_node = fs["source"];
        if (!source_node.empty()) {
            source_node["homography_file"] >> artifact_out->source_homography_file;
            source_node["geometry_file"] >> artifact_out->source_geometry_file;
        }

        cv::FileNode geometry_node = fs["geometry"];
        if (!geometry_node.empty()) {
            geometry_node["model"] >> artifact_out->model;
            geometry_node["warp_model"] >> artifact_out->alignment_model;
            geometry_node["residual_model"] >> artifact_out->residual_model;
            cv::FileNode homography_node = geometry_node["homography"];
            if (!homography_node.empty() && homography_node.isSeq()) {
                std::vector<double> values;
                homography_node >> values;
                if (values.size() >= 9) {
                    artifact_out->alignment_matrix =
                        cv::Mat(3, 3, CV_64F, values.data()).clone();
                }
            }
            std::vector<double> output_resolution;
            geometry_node["output_resolution"] >> output_resolution;
            if (output_resolution.size() >= 2) {
                artifact_out->output_size = cv::Size(
                    static_cast<int>(std::llround(output_resolution[0])),
                    static_cast<int>(std::llround(output_resolution[1])));
            }
        }

        cv::FileNode alignment_node = fs["alignment"];
        if (!alignment_node.empty()) {
            alignment_node["model"] >> artifact_out->alignment_model;
            cv::FileNode matrix_node = alignment_node["matrix"];
            if (!matrix_node.empty() && matrix_node.isSeq()) {
                std::vector<double> values;
                matrix_node >> values;
                if (values.size() >= 9) {
                    artifact_out->alignment_matrix =
                        cv::Mat(3, 3, CV_64F, values.data()).clone();
                } else if (values.size() >= 6) {
                    artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
                    artifact_out->alignment_matrix.at<double>(0, 0) = values[0];
                    artifact_out->alignment_matrix.at<double>(0, 1) = values[1];
                    artifact_out->alignment_matrix.at<double>(0, 2) = values[2];
                    artifact_out->alignment_matrix.at<double>(1, 0) = values[3];
                    artifact_out->alignment_matrix.at<double>(1, 1) = values[4];
                    artifact_out->alignment_matrix.at<double>(1, 2) = values[5];
                }
            }
        }
        cv::FileNode mesh_node = fs["mesh"];
        if (!mesh_node.empty()) {
            mesh_node["grid_cols"] >> artifact_out->mesh_grid_cols;
            mesh_node["grid_rows"] >> artifact_out->mesh_grid_rows;
            mesh_node["fallback_used"] >> artifact_out->mesh_fallback_used;
            mesh_node["max_displacement_px"] >> artifact_out->mesh_max_displacement_px;
            mesh_node["max_local_scale_drift"] >> artifact_out->mesh_max_local_scale_drift;
            mesh_node["max_local_rotation_drift"] >> artifact_out->mesh_max_local_rotation_drift;
            read_float_grid(mesh_node["control_displacement_x"], &artifact_out->mesh_control_displacement_x);
            read_float_grid(mesh_node["control_displacement_y"], &artifact_out->mesh_control_displacement_y);
        }

        cv::FileNode projection_node = fs["projection"];
        if (!projection_node.empty()) {
            cv::FileNode left_projection = projection_node["left"];
            cv::FileNode right_projection = projection_node["right"];
            std::vector<double> left_center;
            std::vector<double> right_center;
            std::vector<double> left_virtual_center;
            std::vector<double> right_virtual_center;
            std::vector<double> left_input_resolution;
            std::vector<double> right_input_resolution;
            std::vector<double> left_output_resolution;
            std::vector<double> right_output_resolution;
            if (!left_projection.empty()) {
                left_projection["model"] >> artifact_out->left_projection_model;
                left_projection["focal_px"] >> artifact_out->left_focal_px;
                left_projection["center"] >> left_center;
                left_projection["virtual_focal_px"] >> artifact_out->left_virtual_focal_px;
                left_projection["virtual_center"] >> left_virtual_center;
                read_matrix_3x3(left_projection["virtual_to_source_rotation"], &artifact_out->left_virtual_to_source_rotation);
                left_projection["input_resolution"] >> left_input_resolution;
                left_projection["output_resolution"] >> left_output_resolution;
            }
            if (!right_projection.empty()) {
                right_projection["model"] >> artifact_out->right_projection_model;
                right_projection["focal_px"] >> artifact_out->right_focal_px;
                right_projection["center"] >> right_center;
                right_projection["virtual_focal_px"] >> artifact_out->right_virtual_focal_px;
                right_projection["virtual_center"] >> right_virtual_center;
                read_matrix_3x3(right_projection["virtual_to_source_rotation"], &artifact_out->right_virtual_to_source_rotation);
                right_projection["input_resolution"] >> right_input_resolution;
                right_projection["output_resolution"] >> right_output_resolution;
            }
            if (left_center.size() >= 2) {
                artifact_out->left_center_x = left_center[0];
                artifact_out->left_center_y = left_center[1];
            }
            if (right_center.size() >= 2) {
                artifact_out->right_center_x = right_center[0];
                artifact_out->right_center_y = right_center[1];
            }
            if (left_virtual_center.size() >= 2) {
                artifact_out->left_virtual_center_x = left_virtual_center[0];
                artifact_out->left_virtual_center_y = left_virtual_center[1];
            }
            if (right_virtual_center.size() >= 2) {
                artifact_out->right_virtual_center_x = right_virtual_center[0];
                artifact_out->right_virtual_center_y = right_virtual_center[1];
            }
            if (left_input_resolution.size() >= 2) {
                artifact_out->left_input_size = cv::Size(
                    static_cast<int>(std::llround(left_input_resolution[0])),
                    static_cast<int>(std::llround(left_input_resolution[1])));
            }
            if (right_input_resolution.size() >= 2) {
                artifact_out->right_input_size = cv::Size(
                    static_cast<int>(std::llround(right_input_resolution[0])),
                    static_cast<int>(std::llround(right_input_resolution[1])));
            }
            if (artifact_out->output_size.width <= 0 && left_output_resolution.size() >= 2) {
                artifact_out->output_size = cv::Size(
                    static_cast<int>(std::llround(left_output_resolution[0])),
                    static_cast<int>(std::llround(left_output_resolution[1])));
            }
            if (artifact_out->output_size.width <= 0 && right_output_resolution.size() >= 2) {
                artifact_out->output_size = cv::Size(
                    static_cast<int>(std::llround(right_output_resolution[0])),
                    static_cast<int>(std::llround(right_output_resolution[1])));
            }
        }

        cv::FileNode canvas_node = fs["canvas"];
        if (!canvas_node.empty()) {
            canvas_node["width"] >> artifact_out->output_size.width;
            canvas_node["height"] >> artifact_out->output_size.height;
        }

        cv::FileNode seam_node = fs["seam"];
        if (!seam_node.empty()) {
            seam_node["transition_px"] >> artifact_out->seam_transition_px;
            seam_node["smoothness_penalty"] >> artifact_out->seam_smoothness_penalty;
            seam_node["temporal_penalty"] >> artifact_out->seam_temporal_penalty;
        }

        cv::FileNode exposure_node = fs["exposure"];
        if (!exposure_node.empty()) {
            exposure_node["enabled"] >> artifact_out->exposure_enabled;
            exposure_node["gain_min"] >> artifact_out->exposure_gain_min;
            exposure_node["gain_max"] >> artifact_out->exposure_gain_max;
            exposure_node["bias_abs_max"] >> artifact_out->exposure_bias_abs_max;
        }
        cv::FileNode crop_node = fs["crop"];
        if (!crop_node.empty()) {
            crop_node["enabled"] >> artifact_out->crop_enabled;
            cv::FileNode rect_node = crop_node["rect"];
            if (!rect_node.empty() && rect_node.isSeq()) {
                std::vector<double> crop_rect_values;
                rect_node >> crop_rect_values;
                if (crop_rect_values.size() >= 4) {
                    artifact_out->crop_rect = cv::Rect(
                        static_cast<int>(std::llround(crop_rect_values[0])),
                        static_cast<int>(std::llround(crop_rect_values[1])),
                        static_cast<int>(std::llround(crop_rect_values[2])),
                        static_cast<int>(std::llround(crop_rect_values[3])));
                }
            }
        }
        cv::FileNode calibration_node = fs["calibration"];
        if (!calibration_node.empty()) {
            cv::FileNode metrics_node = calibration_node["metrics"];
            if (!metrics_node.empty()) {
                double residual_error_px = 0.0;
                if (metrics_node["mean_reprojection_error"] >> residual_error_px) {
                    artifact_out->residual_alignment_error_px = residual_error_px;
                } else if (metrics_node["reprojection_error_px"] >> residual_error_px) {
                    artifact_out->residual_alignment_error_px = residual_error_px;
                }
            }
        }

        if (artifact_out->output_size.width <= 0 || artifact_out->output_size.height <= 0) {
            const int base_width = std::max(
                1,
                std::max(
                    artifact_out->left_input_size.width,
                    artifact_out->right_input_size.width));
            const int base_height = std::max(
                1,
                std::max(
                    artifact_out->left_input_size.height,
                    artifact_out->right_input_size.height));
            artifact_out->output_size = cv::Size(base_width, base_height);
        }
        if (artifact_out->left_focal_px <= 0.0) {
            artifact_out->left_focal_px = artifact_out->right_focal_px;
        }
        if (artifact_out->right_focal_px <= 0.0) {
            artifact_out->right_focal_px = artifact_out->left_focal_px;
        }
        if (artifact_out->left_center_x <= 0.0 && artifact_out->right_center_x > 0.0) {
            artifact_out->left_center_x = artifact_out->right_center_x;
            artifact_out->left_center_y = artifact_out->right_center_y;
        }
        if (artifact_out->right_center_x <= 0.0 && artifact_out->left_center_x > 0.0) {
            artifact_out->right_center_x = artifact_out->left_center_x;
            artifact_out->right_center_y = artifact_out->left_center_y;
        }
        artifact_out->left_projection_model = normalize_projection_model(artifact_out->left_projection_model);
        artifact_out->right_projection_model = normalize_projection_model(artifact_out->right_projection_model);
        if (artifact_out->residual_model.empty()) {
            if (artifact_out->model == "virtual-center-rectilinear") {
                artifact_out->residual_model = "rigid";
            } else if (artifact_out->model == "cylindrical-affine") {
                artifact_out->residual_model = "affine";
            } else {
                artifact_out->residual_model = artifact_out->alignment_model;
            }
        }
        sanitize_virtual_projection_side(
            &artifact_out->left_focal_px,
            &artifact_out->left_center_x,
            &artifact_out->left_center_y,
            &artifact_out->left_virtual_focal_px,
            &artifact_out->left_virtual_center_x,
            &artifact_out->left_virtual_center_y,
            artifact_out->left_input_size);
        sanitize_virtual_projection_side(
            &artifact_out->right_focal_px,
            &artifact_out->right_center_x,
            &artifact_out->right_center_y,
            &artifact_out->right_virtual_focal_px,
            &artifact_out->right_virtual_center_x,
            &artifact_out->right_virtual_center_y,
            artifact_out->right_input_size);
        if (artifact_out->left_virtual_to_source_rotation.empty()) {
            artifact_out->left_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
        }
        if (artifact_out->right_virtual_to_source_rotation.empty()) {
            artifact_out->right_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
        }
        if (artifact_out->alignment_matrix.empty()) {
            artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
        }
        sanitize_crop_rect();
        return true;
    } catch (const cv::Exception&) {
        // Fall through to permissive parsing below.
    }

    std::ifstream file(path);
    if (!file.is_open()) {
        return false;
    }
    const std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (text.find("\"artifact_type\"") == std::string::npos) {
        return false;
    }

    extract_json_string_for_key(text, "\"model\"", &artifact_out->model);
    extract_json_string_for_key(text, "\"alignment_model\"", &artifact_out->alignment_model);
    extract_json_string_for_key(text, "\"residual_model\"", &artifact_out->residual_model);
    if (artifact_out->alignment_model.empty()) {
        artifact_out->alignment_model = "affine";
    }
    if (artifact_out->model.empty()) {
        artifact_out->model = "planar-homography";
    }
    if (artifact_out->model == "cylindrical_affine") {
        artifact_out->model = "cylindrical-affine";
    }

    std::string alignment_text;
    if (extract_json_array_for_key(text, "\"alignment\"", &alignment_text)) {
        std::vector<double> values;
        if (parse_numeric_vector(alignment_text, &values)) {
            if (values.size() >= 9) {
                artifact_out->alignment_matrix = cv::Mat(3, 3, CV_64F, values.data()).clone();
            } else if (values.size() >= 6) {
                artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
                artifact_out->alignment_matrix.at<double>(0, 0) = values[0];
                artifact_out->alignment_matrix.at<double>(0, 1) = values[1];
                artifact_out->alignment_matrix.at<double>(0, 2) = values[2];
                artifact_out->alignment_matrix.at<double>(1, 0) = values[3];
                artifact_out->alignment_matrix.at<double>(1, 1) = values[4];
                artifact_out->alignment_matrix.at<double>(1, 2) = values[5];
            }
        }
    }
    if (artifact_out->alignment_matrix.empty()) {
        artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
    }
    if (artifact_out->residual_model.empty()) {
        if (artifact_out->model == "virtual-center-rectilinear") {
            artifact_out->residual_model = "rigid";
        } else if (artifact_out->model == "cylindrical-affine") {
            artifact_out->residual_model = "affine";
        } else {
            artifact_out->residual_model = artifact_out->alignment_model;
        }
    }

    double numeric_value = 0.0;
    if (extract_json_number_for_key(text, "\"focal_px\"", &numeric_value)) {
        artifact_out->left_focal_px = numeric_value;
        artifact_out->right_focal_px = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"transition_px\"", &numeric_value)) {
        artifact_out->seam_transition_px = static_cast<int>(std::llround(numeric_value));
    }
    if (extract_json_number_for_key(text, "\"smoothness_penalty\"", &numeric_value)) {
        artifact_out->seam_smoothness_penalty = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"temporal_penalty\"", &numeric_value)) {
        artifact_out->seam_temporal_penalty = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"gain_min\"", &numeric_value)) {
        artifact_out->exposure_gain_min = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"gain_max\"", &numeric_value)) {
        artifact_out->exposure_gain_max = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"bias_abs_max\"", &numeric_value)) {
        artifact_out->exposure_bias_abs_max = numeric_value;
    }
    std::string exposure_block_text;
    bool exposure_enabled = artifact_out->exposure_enabled;
    if (extract_json_object_for_key(text, "\"exposure\"", &exposure_block_text) &&
        extract_json_bool(exposure_block_text, "\"enabled\"", &exposure_enabled)) {
        artifact_out->exposure_enabled = exposure_enabled;
    }
    std::string crop_block_text;
    if (extract_json_object_for_key(text, "\"crop\"", &crop_block_text)) {
        extract_json_bool(crop_block_text, "\"enabled\"", &artifact_out->crop_enabled);
        std::string crop_rect_text;
        if (extract_json_array_for_key(crop_block_text, "\"rect\"", &crop_rect_text)) {
            std::vector<double> crop_rect_values;
            if (parse_numeric_vector(crop_rect_text, &crop_rect_values) && crop_rect_values.size() >= 4) {
                artifact_out->crop_rect = cv::Rect(
                    static_cast<int>(std::llround(crop_rect_values[0])),
                    static_cast<int>(std::llround(crop_rect_values[1])),
                    static_cast<int>(std::llround(crop_rect_values[2])),
                    static_cast<int>(std::llround(crop_rect_values[3])));
            }
        }
    }
    if (extract_json_number_for_key(text, "\"mean_reprojection_error\"", &numeric_value)) {
        artifact_out->residual_alignment_error_px = numeric_value;
    } else if (extract_json_number_for_key(text, "\"reprojection_error_px\"", &numeric_value)) {
        artifact_out->residual_alignment_error_px = numeric_value;
    }
    std::string mesh_block_text;
    if (extract_json_object_for_key(text, "\"mesh\"", &mesh_block_text)) {
        if (extract_json_number_for_key(mesh_block_text, "\"grid_cols\"", &numeric_value)) {
            artifact_out->mesh_grid_cols = static_cast<int>(std::llround(numeric_value));
        }
        if (extract_json_number_for_key(mesh_block_text, "\"grid_rows\"", &numeric_value)) {
            artifact_out->mesh_grid_rows = static_cast<int>(std::llround(numeric_value));
        }
        extract_json_bool(mesh_block_text, "\"fallback_used\"", &artifact_out->mesh_fallback_used);
        if (extract_json_number_for_key(mesh_block_text, "\"max_displacement_px\"", &numeric_value)) {
            artifact_out->mesh_max_displacement_px = numeric_value;
        }
        if (extract_json_number_for_key(mesh_block_text, "\"max_local_scale_drift\"", &numeric_value)) {
            artifact_out->mesh_max_local_scale_drift = numeric_value;
        }
        if (extract_json_number_for_key(mesh_block_text, "\"max_local_rotation_drift\"", &numeric_value)) {
            artifact_out->mesh_max_local_rotation_drift = numeric_value;
        }
        auto parse_grid_array = [&](const std::string& key, cv::Mat* grid_out) {
            if (grid_out == nullptr) {
                return false;
            }
            std::string grid_text;
            if (!extract_json_array_for_key(mesh_block_text, key, &grid_text)) {
                return false;
            }
            std::vector<double> parsed_values;
            if (!parse_numeric_vector(grid_text, &parsed_values)) {
                return false;
            }
            const int rows = artifact_out->mesh_grid_rows + 1;
            const int cols = artifact_out->mesh_grid_cols + 1;
            if (rows <= 0 || cols <= 0 || static_cast<int>(parsed_values.size()) != rows * cols) {
                return false;
            }
            cv::Mat grid(rows, cols, CV_32F);
            for (int row = 0; row < rows; ++row) {
                auto* grid_row = grid.ptr<float>(row);
                for (int col = 0; col < cols; ++col) {
                    grid_row[col] = static_cast<float>(parsed_values[static_cast<std::size_t>(row * cols + col)]);
                }
            }
            *grid_out = grid;
            return true;
        };
        parse_grid_array("\"control_displacement_x\"", &artifact_out->mesh_control_displacement_x);
        parse_grid_array("\"control_displacement_y\"", &artifact_out->mesh_control_displacement_y);
    }
    if (artifact_out->output_size.width <= 0 || artifact_out->output_size.height <= 0) {
        artifact_out->output_size = cv::Size(artifact_out->left_input_size.width, artifact_out->left_input_size.height);
    }
    std::string projection_block_text;
    const std::string& projection_lookup_text =
        (extract_json_object_for_key(text, "\"projection\"", &projection_block_text) && !projection_block_text.empty())
            ? projection_block_text
            : text;
    std::string left_projection_text;
    if (extract_json_object_for_key(projection_lookup_text, "\"left\"", &left_projection_text)) {
        extract_json_string_for_key(left_projection_text, "\"model\"", &artifact_out->left_projection_model);
        if (extract_json_number_for_key(left_projection_text, "\"focal_px\"", &numeric_value)) {
            artifact_out->left_focal_px = numeric_value;
        }
        std::string center_text;
        if (extract_json_array_for_key(left_projection_text, "\"center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->left_center_x = center_values[0];
                artifact_out->left_center_y = center_values[1];
            }
        }
        if (extract_json_number_for_key(left_projection_text, "\"virtual_focal_px\"", &numeric_value)) {
            artifact_out->left_virtual_focal_px = numeric_value;
        }
        if (extract_json_array_for_key(left_projection_text, "\"virtual_center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->left_virtual_center_x = center_values[0];
                artifact_out->left_virtual_center_y = center_values[1];
            }
        }
        if (extract_json_array_for_key(left_projection_text, "\"virtual_to_source_rotation\"", &center_text)) {
            std::vector<double> rotation_values;
            if (parse_numeric_vector(center_text, &rotation_values) && rotation_values.size() >= 9) {
                artifact_out->left_virtual_to_source_rotation =
                    cv::Mat(3, 3, CV_64F, rotation_values.data()).clone();
            }
        }
    }
    std::string right_projection_text;
    if (extract_json_object_for_key(projection_lookup_text, "\"right\"", &right_projection_text)) {
        extract_json_string_for_key(right_projection_text, "\"model\"", &artifact_out->right_projection_model);
        if (extract_json_number_for_key(right_projection_text, "\"focal_px\"", &numeric_value)) {
            artifact_out->right_focal_px = numeric_value;
        }
        std::string center_text;
        if (extract_json_array_for_key(right_projection_text, "\"center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->right_center_x = center_values[0];
                artifact_out->right_center_y = center_values[1];
            }
        }
        if (extract_json_number_for_key(right_projection_text, "\"virtual_focal_px\"", &numeric_value)) {
            artifact_out->right_virtual_focal_px = numeric_value;
        }
        if (extract_json_array_for_key(right_projection_text, "\"virtual_center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->right_virtual_center_x = center_values[0];
                artifact_out->right_virtual_center_y = center_values[1];
            }
        }
        if (extract_json_array_for_key(right_projection_text, "\"virtual_to_source_rotation\"", &center_text)) {
            std::vector<double> rotation_values;
            if (parse_numeric_vector(center_text, &rotation_values) && rotation_values.size() >= 9) {
                artifact_out->right_virtual_to_source_rotation =
                    cv::Mat(3, 3, CV_64F, rotation_values.data()).clone();
            }
        }
    }
    if (artifact_out->left_focal_px <= 0.0) {
        artifact_out->left_focal_px = artifact_out->right_focal_px;
    }
    if (artifact_out->right_focal_px <= 0.0) {
        artifact_out->right_focal_px = artifact_out->left_focal_px;
    }
    if (artifact_out->left_center_x <= 0.0 && artifact_out->right_center_x > 0.0) {
        artifact_out->left_center_x = artifact_out->right_center_x;
        artifact_out->left_center_y = artifact_out->right_center_y;
    }
    if (artifact_out->right_center_x <= 0.0 && artifact_out->left_center_x > 0.0) {
        artifact_out->right_center_x = artifact_out->left_center_x;
        artifact_out->right_center_y = artifact_out->left_center_y;
    }
    artifact_out->left_projection_model = normalize_projection_model(artifact_out->left_projection_model);
    artifact_out->right_projection_model = normalize_projection_model(artifact_out->right_projection_model);
    sanitize_virtual_projection_side(
        &artifact_out->left_focal_px,
        &artifact_out->left_center_x,
        &artifact_out->left_center_y,
        &artifact_out->left_virtual_focal_px,
        &artifact_out->left_virtual_center_x,
        &artifact_out->left_virtual_center_y,
        artifact_out->left_input_size);
    sanitize_virtual_projection_side(
        &artifact_out->right_focal_px,
        &artifact_out->right_center_x,
        &artifact_out->right_center_y,
        &artifact_out->right_virtual_focal_px,
        &artifact_out->right_virtual_center_x,
        &artifact_out->right_virtual_center_y,
        artifact_out->right_input_size);
    if (artifact_out->left_virtual_to_source_rotation.empty()) {
        artifact_out->left_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
    }
    if (artifact_out->right_virtual_to_source_rotation.empty()) {
        artifact_out->right_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
    }
    sanitize_crop_rect();
    return true;
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

bool StitchEngine::build_cylindrical_maps_locked(
    const cv::Size& image_size,
    double focal_px,
    double center_x,
    double center_y,
    cv::Mat* map_x_out,
    cv::Mat* map_y_out) const {
    if (map_x_out == nullptr || map_y_out == nullptr || image_size.width <= 0 || image_size.height <= 0) {
        return false;
    }

    const double focal = (focal_px > 1e-6)
        ? focal_px
        : static_cast<double>(std::max(image_size.width, image_size.height)) * 0.90;
    const double cx = (center_x > 0.0) ? center_x : static_cast<double>(image_size.width) * 0.5;
    const double cy = (center_y > 0.0) ? center_y : static_cast<double>(image_size.height) * 0.5;

    map_x_out->create(image_size, CV_32FC1);
    map_y_out->create(image_size, CV_32FC1);
    for (int y = 0; y < image_size.height; ++y) {
        auto* map_x_row = map_x_out->ptr<float>(y);
        auto* map_y_row = map_y_out->ptr<float>(y);
        const double dest_y = static_cast<double>(y) - cy;
        for (int x = 0; x < image_size.width; ++x) {
            const double dest_x = static_cast<double>(x) - cx;
            const double theta = dest_x / focal;
            const double source_x = focal * std::tan(theta) + cx;
            const double source_y =
                (dest_y * std::sqrt((source_x - cx) * (source_x - cx) + (focal * focal))) / focal + cy;
            map_x_row[x] = static_cast<float>(source_x);
            map_y_row[x] = static_cast<float>(source_y);
        }
    }
    return true;
}

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
        config_.process_scale <= 0.0 ? 4.0 : config_.process_scale,
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

const char* metrics_source_time_mode_name(hogak::input::FrameTimeDomain time_domain) noexcept {
    switch (time_domain) {
        case hogak::input::FrameTimeDomain::kSourceWallclock:
            return "wallclock";
        case hogak::input::FrameTimeDomain::kSourcePtsOffset:
            return "stream_pts_offset";
        case hogak::input::FrameTimeDomain::kSourceComparable:
            return "stream_pts";
        case hogak::input::FrameTimeDomain::kArrival:
        default:
            return "fallback-arrival";
    }
}

std::string output_runtime_mode_hint(const std::string& runtime) {
    if (runtime == "ffmpeg") {
        return "ffmpeg-process";
    }
    if (runtime == "gpu-direct") {
        return "native-nvenc-bridge";
    }
    if (runtime.empty()) {
        return "none";
    }
    return runtime;
}

double clamp_unit(double value) {
    return std::clamp(value, 0.0, 1.0);
}

std::string normalize_sync_time_source(std::string value) {
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    if (value == "pts-offset-manual" ||
        value == "pts-offset-auto" ||
        value == "pts-offset-hybrid" ||
        value == "arrival" ||
        value == "wallclock") {
        return value;
    }
    return "pts-offset-auto";
}

bool sync_mode_uses_pts_offset(const std::string& value) {
    return value == "pts-offset-manual" || value == "pts-offset-auto" || value == "pts-offset-hybrid";
}

bool snapshot_has_source_pts(const hogak::input::ReaderSnapshot& snapshot) {
    return snapshot.latest_source_time_valid && snapshot.latest_source_pts_ns > 0;
}

bool snapshot_has_wallclock(const hogak::input::ReaderSnapshot& snapshot) {
    return snapshot.latest_source_time_comparable &&
        snapshot.latest_comparable_source_timestamp_ns > 0;
}

bool frame_has_source_pts(const hogak::input::BufferedFrameInfo& info) {
    return info.source_time_valid && info.source_pts_ns > 0;
}

double snapshot_frame_period_ms(
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot) {
    const double fps = std::max({left_snapshot.fps, right_snapshot.fps, 30.0});
    if (!std::isfinite(fps) || fps <= 0.0) {
        return 1000.0 / 30.0;
    }
    return 1000.0 / fps;
}

double pearson_correlation(const std::vector<double>& left, const std::vector<double>& right) {
    if (left.size() != right.size() || left.size() < 3) {
        return 0.0;
    }
    double left_mean = 0.0;
    double right_mean = 0.0;
    for (std::size_t index = 0; index < left.size(); ++index) {
        left_mean += left[index];
        right_mean += right[index];
    }
    left_mean /= static_cast<double>(left.size());
    right_mean /= static_cast<double>(right.size());

    double cov = 0.0;
    double left_var = 0.0;
    double right_var = 0.0;
    for (std::size_t index = 0; index < left.size(); ++index) {
        const double left_delta = left[index] - left_mean;
        const double right_delta = right[index] - right_mean;
        cov += left_delta * right_delta;
        left_var += left_delta * left_delta;
        right_var += right_delta * right_delta;
    }
    if (left_var <= 1e-9 || right_var <= 1e-9) {
        return 0.0;
    }
    return cov / std::sqrt(left_var * right_var);
}

OffsetScore score_pts_offset_candidate(
    const std::vector<hogak::input::BufferedFrameInfo>& left_infos,
    const std::vector<hogak::input::BufferedFrameInfo>& right_infos,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double window_sec,
    double frame_period_ms,
    double offset_ms) {
    OffsetScore score;
    if (!snapshot_has_source_pts(left_snapshot) || !snapshot_has_source_pts(right_snapshot)) {
        return score;
    }
    const auto window_ns = static_cast<std::int64_t>(std::max(1.0, window_sec) * 1'000'000'000.0);
    const auto offset_ns = static_cast<std::int64_t>(std::llround(offset_ms * 1'000'000.0));
    const auto left_cutoff_ns = left_snapshot.latest_source_pts_ns - window_ns;
    const auto right_cutoff_ns = right_snapshot.latest_source_pts_ns - window_ns;
    const double max_gap_ms = std::max(20.0, 1.5 * std::max(1.0, frame_period_ms));
    const auto max_gap_ns = static_cast<std::int64_t>(std::llround(max_gap_ms * 1'000'000.0));

    std::vector<const hogak::input::BufferedFrameInfo*> filtered_left;
    std::vector<const hogak::input::BufferedFrameInfo*> filtered_right;
    filtered_left.reserve(left_infos.size());
    filtered_right.reserve(right_infos.size());
    for (const auto& info : left_infos) {
        if (frame_has_source_pts(info) && info.source_pts_ns >= left_cutoff_ns) {
            filtered_left.push_back(&info);
        }
    }
    for (const auto& info : right_infos) {
        if (frame_has_source_pts(info) && info.source_pts_ns >= right_cutoff_ns) {
            filtered_right.push_back(&info);
        }
    }
    if (filtered_left.size() < 4 || filtered_right.size() < 4) {
        return score;
    }

    std::vector<double> left_motion;
    std::vector<double> right_motion;
    std::vector<double> left_luma;
    std::vector<double> right_luma;
    double gap_sum_ms = 0.0;

    for (const auto* left_info : filtered_left) {
        const auto target_right_pts_ns = left_info->source_pts_ns - offset_ns;
        const hogak::input::BufferedFrameInfo* best_right = nullptr;
        auto best_gap_ns = max_gap_ns + 1;
        for (const auto* right_info : filtered_right) {
            const auto gap_ns = std::llabs(right_info->source_pts_ns - target_right_pts_ns);
            if (gap_ns < best_gap_ns) {
                best_gap_ns = gap_ns;
                best_right = right_info;
            }
        }
        if (best_right == nullptr || best_gap_ns > max_gap_ns) {
            continue;
        }
        left_motion.push_back(left_info->motion_score);
        right_motion.push_back(best_right->motion_score);
        left_luma.push_back(left_info->luma_mean);
        right_luma.push_back(best_right->luma_mean);
        gap_sum_ms += static_cast<double>(best_gap_ns) / 1'000'000.0;
    }

    if (left_motion.size() < 4) {
        return score;
    }

    score.valid = true;
    score.matched_pairs = static_cast<int>(left_motion.size());
    score.avg_gap_ms = gap_sum_ms / static_cast<double>(score.matched_pairs);
    score.motion_corr = pearson_correlation(left_motion, right_motion);
    score.luma_corr = pearson_correlation(left_luma, right_luma);
    score.overlap_ratio = static_cast<double>(score.matched_pairs) /
        static_cast<double>(std::max<std::size_t>(1, std::min(filtered_left.size(), filtered_right.size())));
    score.combined_score =
        score.motion_corr +
        (0.20 * score.luma_corr) +
        (0.35 * score.overlap_ratio) -
        (0.002 * score.avg_gap_ms) -
        (0.0015 * std::abs(offset_ms));
    return score;
}

OffsetEstimateResult estimate_pts_offset_from_buffers(
    const std::vector<hogak::input::BufferedFrameInfo>& left_infos,
    const std::vector<hogak::input::BufferedFrameInfo>& right_infos,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double window_sec,
    double max_search_ms,
    double center_offset_ms,
    bool local_search_only) {
    OffsetEstimateResult result;
    if (!snapshot_has_source_pts(left_snapshot) || !snapshot_has_source_pts(right_snapshot)) {
        return result;
    }

    const double frame_period_ms = snapshot_frame_period_ms(left_snapshot, right_snapshot);
    double search_center_ms = std::isfinite(center_offset_ms) ? center_offset_ms : 0.0;
    const double bounded_search_ms = std::max(50.0, std::abs(max_search_ms));
    search_center_ms = std::clamp(search_center_ms, -bounded_search_ms, bounded_search_ms);

    const auto scan_range = [&](double start_ms,
                                double end_ms,
                                double step_ms,
                                OffsetScore* best_score_out,
                                double* best_offset_out,
                                double* best_selection_score_out,
                                double* second_best_score_out) {
        for (double offset_ms = start_ms; offset_ms <= end_ms + (step_ms * 0.5); offset_ms += step_ms) {
            const auto score = score_pts_offset_candidate(
                left_infos,
                right_infos,
                left_snapshot,
                right_snapshot,
                window_sec,
                frame_period_ms,
                offset_ms);
            if (!score.valid) {
                continue;
            }
            const double stability_penalty = 0.0020 * std::abs(offset_ms - search_center_ms);
            const double selection_score = score.combined_score - stability_penalty;
            const bool is_better =
                !best_score_out->valid ||
                (selection_score > (*best_selection_score_out + 1e-9)) ||
                (std::abs(selection_score - *best_selection_score_out) <= 1e-9 &&
                 std::abs(offset_ms - search_center_ms) < std::abs(*best_offset_out - search_center_ms));
            if (is_better) {
                *second_best_score_out = best_score_out->valid ? *best_selection_score_out : *second_best_score_out;
                *best_score_out = score;
                *best_offset_out = offset_ms;
                *best_selection_score_out = selection_score;
            } else if (selection_score > *second_best_score_out) {
                *second_best_score_out = selection_score;
            }
        }
    };

    const double coarse_radius_ms = local_search_only ? std::min(bounded_search_ms, 125.0) : bounded_search_ms;
    double best_offset_ms = search_center_ms;
    OffsetScore best_score;
    double best_selection_score = -1'000'000.0;
    double second_best_score = -1'000'000.0;
    scan_range(
        std::max(-bounded_search_ms, search_center_ms - coarse_radius_ms),
        std::min(bounded_search_ms, search_center_ms + coarse_radius_ms),
        25.0,
        &best_score,
        &best_offset_ms,
        &best_selection_score,
        &second_best_score);
    if (!best_score.valid && !local_search_only) {
        scan_range(
            -bounded_search_ms,
            bounded_search_ms,
            25.0,
            &best_score,
            &best_offset_ms,
            &best_selection_score,
            &second_best_score);
    }
    if (!best_score.valid) {
        return result;
    }

    scan_range(
        std::max(-bounded_search_ms, best_offset_ms - 40.0),
        std::min(bounded_search_ms, best_offset_ms + 40.0),
        5.0,
        &best_score,
        &best_offset_ms,
        &best_selection_score,
        &second_best_score);
    scan_range(
        std::max(-bounded_search_ms, best_offset_ms - 10.0),
        std::min(bounded_search_ms, best_offset_ms + 10.0),
        1.0,
        &best_score,
        &best_offset_ms,
        &best_selection_score,
        &second_best_score);

    const double motion_component = clamp_unit(std::max(0.0, best_score.motion_corr));
    const double luma_component = clamp_unit(std::max(0.0, best_score.luma_corr));
    const double overlap_component = clamp_unit(best_score.overlap_ratio);
    const double max_gap_ms = std::max(20.0, 1.5 * std::max(1.0, frame_period_ms));
    const double gap_component = clamp_unit(1.0 - (best_score.avg_gap_ms / max_gap_ms));
    const double peak_component = clamp_unit((best_selection_score - second_best_score + 0.5) / 1.0);
    const double pairs_component = clamp_unit((static_cast<double>(best_score.matched_pairs) - 4.0) / 8.0);

    result.valid = true;
    result.offset_ms = best_offset_ms;
    result.best_score = best_score;
    result.second_best_score = second_best_score;
    result.selection_score = best_selection_score;
    result.confidence = clamp_unit(
        (0.30 * motion_component) +
        (0.05 * luma_component) +
        (0.20 * overlap_component) +
        (0.20 * gap_component) +
        (0.15 * peak_component) +
        (0.10 * pairs_component));
    return result;
}

hogak::input::FrameTimeDomain resolve_service_time_domain(
    const EngineConfig& config,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double sync_offset_confidence) {
    const std::string sync_time_source = normalize_sync_time_source(config.sync_time_source);
    if (sync_time_source == "wallclock") {
        return (snapshot_has_wallclock(left_snapshot) && snapshot_has_wallclock(right_snapshot))
            ? hogak::input::FrameTimeDomain::kSourceWallclock
            : hogak::input::FrameTimeDomain::kArrival;
    }
    if (sync_time_source == "arrival") {
        return hogak::input::FrameTimeDomain::kArrival;
    }
    if (!sync_mode_uses_pts_offset(sync_time_source) ||
        !snapshot_has_source_pts(left_snapshot) ||
        !snapshot_has_source_pts(right_snapshot)) {
        return hogak::input::FrameTimeDomain::kArrival;
    }
    if (sync_time_source == "pts-offset-manual") {
        return hogak::input::FrameTimeDomain::kSourcePtsOffset;
    }
    if (sync_time_source == "pts-offset-auto") {
        return hogak::input::FrameTimeDomain::kSourcePtsOffset;
    }
    if (sync_time_source == "pts-offset-hybrid" && std::abs(config.sync_manual_offset_ms) > 1e-6) {
        if (sync_offset_confidence < std::max(0.0, config.sync_auto_offset_confidence_min)) {
            return hogak::input::FrameTimeDomain::kSourcePtsOffset;
        }
    }
    return (sync_offset_confidence >= std::max(0.0, config.sync_auto_offset_confidence_min))
        ? hogak::input::FrameTimeDomain::kSourcePtsOffset
        : hogak::input::FrameTimeDomain::kArrival;
}

std::int64_t snapshot_latest_time_ns(
    const hogak::input::ReaderSnapshot& snapshot,
    hogak::input::FrameTimeDomain time_domain) {
    switch (time_domain) {
        case hogak::input::FrameTimeDomain::kSourceWallclock:
            return snapshot.latest_comparable_source_timestamp_ns;
        case hogak::input::FrameTimeDomain::kSourcePtsOffset:
        case hogak::input::FrameTimeDomain::kSourceComparable:
            return snapshot.latest_source_pts_ns;
        case hogak::input::FrameTimeDomain::kArrival:
        default:
            return snapshot.latest_timestamp_ns;
    }
}

double cumulative_wait_sync_ratio(const EngineMetrics& metrics) {
    const double total =
        static_cast<double>(
            metrics.stitched_count +
            metrics.realtime_fallback_pair_count +
            metrics.wait_sync_pair_count +
            metrics.wait_next_frame_count +
            metrics.wait_paired_fresh_count +
            metrics.wait_both_streams_count);
    if (total <= 0.0) {
        return 0.0;
    }
    return static_cast<double>(metrics.wait_sync_pair_count) / total;
}

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
    if (candidate.left_info.seq != best.left_info.seq) {
        return candidate.left_info.seq > best.left_info.seq;
    }
    return candidate.right_info.seq > best.right_info.seq;
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
    const std::string sync_time_source = normalize_sync_time_source(config_.sync_time_source);
    std::int64_t effective_offset_ns = 0;
    double effective_offset_confidence = 0.0;
    std::string effective_offset_source = "arrival-fallback";

    auto fill_pair = [&](const hogak::input::BufferedFrameInfo& left_info,
                         const hogak::input::BufferedFrameInfo& right_info) {
        pair_out->left_frame = left_info.frame;
        pair_out->right_frame = right_info.frame;
        pair_out->left_seq = left_info.seq;
        pair_out->right_seq = right_info.seq;
        pair_out->left_ts_ns = left_info.arrival_timestamp_ns;
        pair_out->right_ts_ns = right_info.arrival_timestamp_ns;
        pair_out->left_arrival_ts_ns = left_info.arrival_timestamp_ns;
        pair_out->right_arrival_ts_ns = right_info.arrival_timestamp_ns;
        pair_out->left_source_pts_ns = left_info.source_pts_ns;
        pair_out->right_source_pts_ns = right_info.source_pts_ns;
        pair_out->left_source_dts_ns = left_info.source_dts_ns;
        pair_out->right_source_dts_ns = right_info.source_dts_ns;
        pair_out->left_source_wallclock_ns = left_info.source_wallclock_ns;
        pair_out->right_source_wallclock_ns = right_info.source_wallclock_ns;
        pair_out->left_source_time_valid = left_info.source_time_valid;
        pair_out->right_source_time_valid = right_info.source_time_valid;
        pair_out->left_source_time_comparable = left_info.source_time_comparable;
        pair_out->right_source_time_comparable = right_info.source_time_comparable;
        pair_out->left_source_time_kind = left_info.source_time_kind;
        pair_out->right_source_time_kind = right_info.source_time_kind;
        pair_out->effective_offset_ns = effective_offset_ns;
        pair_out->offset_source = effective_offset_source;
        pair_out->offset_confidence = effective_offset_confidence;
    };

    auto compute_source_skew_ns = [&](const hogak::input::BufferedFrameInfo& left_info,
                                      const hogak::input::BufferedFrameInfo& right_info,
                                      hogak::input::FrameTimeDomain time_domain) {
        if (time_domain == hogak::input::FrameTimeDomain::kSourceWallclock) {
            if (left_info.source_wallclock_ns <= 0 || right_info.source_wallclock_ns <= 0) {
                return std::int64_t{0};
            }
            return std::llabs(left_info.source_wallclock_ns - right_info.source_wallclock_ns);
        }
        if (time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset ||
            time_domain == hogak::input::FrameTimeDomain::kSourceComparable) {
            if (!frame_has_source_pts(left_info) || !frame_has_source_pts(right_info)) {
                return std::int64_t{0};
            }
            return std::llabs(left_info.source_pts_ns - (right_info.source_pts_ns + effective_offset_ns));
        }
        return std::int64_t{0};
    };

    bool left_ok = false;
    bool right_ok = false;
    if (mode == "none") {
        hogak::input::BufferedFrameInfo left_info;
        hogak::input::BufferedFrameInfo right_info;
        left_ok = g_left_reader.copy_latest_frame(&pair_out->left_frame, &pair_out->left_seq, &pair_out->left_ts_ns, &left_info);
        right_ok = g_right_reader.copy_latest_frame(&pair_out->right_frame, &pair_out->right_seq, &pair_out->right_ts_ns, &right_info);
        if (left_ok && right_ok) {
            fill_pair(left_info, right_info);
            pair_out->pair_time_domain = hogak::input::FrameTimeDomain::kArrival;
            pair_out->pair_time_ns = std::max(pair_out->left_arrival_ts_ns, pair_out->right_arrival_ts_ns);
            pair_out->scheduler_pair_time_ns = pair_out->pair_time_ns;
            pair_out->arrival_skew_ns = std::llabs(pair_out->left_arrival_ts_ns - pair_out->right_arrival_ts_ns);
            pair_out->source_skew_ns = compute_source_skew_ns(
                left_info,
                right_info,
                hogak::input::FrameTimeDomain::kArrival);
        }
    } else if (mode == "service") {
        g_left_reader.buffered_frame_infos(&left_buffered_infos_cache_);
        g_right_reader.buffered_frame_infos(&right_buffered_infos_cache_);
        const auto& left_infos = left_buffered_infos_cache_;
        const auto& right_infos = right_buffered_infos_cache_;
        const bool has_pts_time =
            snapshot_has_source_pts(left_snapshot) &&
            snapshot_has_source_pts(right_snapshot);
        const auto now_arrival_ns =
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now().time_since_epoch()).count();

        if (sync_time_source == "pts-offset-manual" && has_pts_time) {
            effective_offset_ns = manual_offset_ns;
            effective_offset_confidence = 1.0;
            effective_offset_source = "manual";
            effective_sync_offset_ms_ = config_.sync_manual_offset_ms;
            sync_offset_confidence_ = effective_offset_confidence;
            sync_offset_source_ = effective_offset_source;
        } else if ((sync_time_source == "pts-offset-auto" || sync_time_source == "pts-offset-hybrid") && has_pts_time) {
            const double confidence_min = std::max(0.0, config_.sync_auto_offset_confidence_min);
            const bool have_auto_estimate =
                (sync_offset_source_ == "auto" || sync_offset_source_ == "recalibration");
            const bool periodic_recalibration_due =
                last_sync_recalibration_ns_ <= 0 ||
                (now_arrival_ns - last_sync_recalibration_ns_) >= static_cast<std::int64_t>(
                    std::max(1.0, config_.sync_recalibration_interval_sec) * 1'000'000'000.0);
            const bool recalibration_cooldown_elapsed =
                last_sync_recalibration_ns_ <= 0 ||
                (now_arrival_ns - last_sync_recalibration_ns_) >= 5'000'000'000LL;
            const bool sync_quality_degraded =
                metrics_.pair_source_skew_ms_mean >= std::max(0.0, config_.sync_recalibration_trigger_skew_ms) ||
                cumulative_wait_sync_ratio(metrics_) >= std::max(0.0, config_.sync_recalibration_trigger_wait_ratio);
            const bool should_estimate =
                !have_auto_estimate || periodic_recalibration_due || (sync_quality_degraded && recalibration_cooldown_elapsed);
            if (should_estimate) {
                const auto estimate = estimate_pts_offset_from_buffers(
                    left_infos,
                    right_infos,
                    left_snapshot,
                    right_snapshot,
                    config_.sync_auto_offset_window_sec,
                    config_.sync_auto_offset_max_search_ms,
                    have_auto_estimate ? effective_sync_offset_ms_ : 0.0,
                    have_auto_estimate);
                sync_estimate_pairs_ = estimate.valid ? estimate.best_score.matched_pairs : 0;
                sync_estimate_avg_gap_ms_ = estimate.valid ? estimate.best_score.avg_gap_ms : 0.0;
                sync_estimate_score_ = estimate.valid ? estimate.selection_score : 0.0;
                const bool estimate_meets_baseline =
                    estimate.valid &&
                    estimate.confidence >= confidence_min &&
                    estimate.best_score.matched_pairs >= 8 &&
                    estimate.best_score.avg_gap_ms <= 15.0;
                const bool requires_extra_confirmation =
                    have_auto_estimate &&
                    std::abs(estimate.offset_ms - effective_sync_offset_ms_) >= 20.0;
                const bool strong_estimate =
                    estimate_meets_baseline &&
                    (!requires_extra_confirmation || estimate.confidence >= 0.95);
                if (strong_estimate) {
                    if (have_auto_estimate) {
                        const double blended_offset_ms =
                            (0.9 * effective_sync_offset_ms_) + (0.1 * estimate.offset_ms);
                        const double limited_delta_ms = std::clamp(
                            blended_offset_ms - effective_sync_offset_ms_,
                            -10.0,
                            10.0);
                        effective_sync_offset_ms_ += limited_delta_ms;
                        sync_offset_source_ = "recalibration";
                        sync_recalibration_count_ += 1;
                    } else {
                        effective_sync_offset_ms_ = estimate.offset_ms;
                        sync_offset_source_ = "auto";
                    }
                    sync_offset_confidence_ = estimate.confidence;
                    last_sync_recalibration_ns_ = now_arrival_ns;
                } else if (!have_auto_estimate) {
                    effective_sync_offset_ms_ = 0.0;
                    sync_offset_confidence_ = estimate.valid ? estimate.confidence : 0.0;
                    sync_offset_source_ = "auto";
                    last_sync_recalibration_ns_ = now_arrival_ns;
                }
            }
            if (sync_time_source == "pts-offset-auto") {
                effective_offset_ns = static_cast<std::int64_t>(std::llround(effective_sync_offset_ms_ * 1'000'000.0));
                effective_offset_confidence = sync_offset_confidence_;
                effective_offset_source = sync_offset_source_;
            } else if (sync_offset_confidence_ >= confidence_min) {
                effective_offset_ns = static_cast<std::int64_t>(std::llround(effective_sync_offset_ms_ * 1'000'000.0));
                effective_offset_confidence = sync_offset_confidence_;
                effective_offset_source = sync_offset_source_;
            } else if (sync_time_source == "pts-offset-hybrid" && std::abs(config_.sync_manual_offset_ms) > 1e-6) {
                effective_offset_ns = manual_offset_ns;
                effective_offset_confidence = 1.0;
                effective_offset_source = "manual";
                effective_sync_offset_ms_ = config_.sync_manual_offset_ms;
                sync_offset_confidence_ = effective_offset_confidence;
                sync_offset_source_ = effective_offset_source;
            }
        } else if (sync_time_source == "wallclock") {
            effective_offset_source = "wallclock";
            effective_offset_confidence = 1.0;
            effective_sync_offset_ms_ = 0.0;
            sync_offset_confidence_ = 1.0;
            sync_offset_source_ = "wallclock";
            sync_estimate_pairs_ = 0;
            sync_estimate_avg_gap_ms_ = 0.0;
            sync_estimate_score_ = 0.0;
        } else {
            effective_sync_offset_ms_ = 0.0;
            sync_offset_confidence_ = 0.0;
            sync_offset_source_ = "arrival-fallback";
            sync_estimate_pairs_ = 0;
            sync_estimate_avg_gap_ms_ = 0.0;
            sync_estimate_score_ = 0.0;
        }
        const auto service_time_domain = resolve_service_time_domain(
            config_,
            left_snapshot,
            right_snapshot,
            effective_offset_confidence);
        if (service_time_domain == hogak::input::FrameTimeDomain::kArrival) {
            effective_offset_ns = 0;
            effective_offset_confidence = 0.0;
            effective_offset_source = "arrival-fallback";
        } else if (service_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock) {
            effective_offset_ns = 0;
            effective_offset_confidence = 1.0;
            effective_offset_source = "wallclock";
        }
        const double target_fps = resolve_service_target_fps(config_, left_snapshot, right_snapshot);
        const auto target_period_ns = static_cast<std::int64_t>(
            std::max(1.0, std::round(1'000'000'000.0 / std::max(1.0, target_fps))));
        const auto fresh_pair_slack_ns = std::min<std::int64_t>(
            max_delta_ns / 4,
            std::max<std::int64_t>(1'000'000, target_period_ns / 2));
        const auto max_reuse_age_ns = static_cast<std::int64_t>(
            std::max(1.0, config_.pair_reuse_max_age_ms) * 1'000'000.0);
        const int max_consecutive_reuse = std::max(1, config_.pair_reuse_max_consecutive);
        const auto latest_left_pair_time_ns = snapshot_latest_time_ns(left_snapshot, service_time_domain);
        const auto latest_right_pair_time_ns = snapshot_latest_time_ns(right_snapshot, service_time_domain) + effective_offset_ns;
        const auto latest_pair_time_ns = std::max(latest_left_pair_time_ns, latest_right_pair_time_ns);
        const bool same_service_domain = last_pair_time_domain_ == service_time_domain;
        const auto unclamped_target_pair_time_ns =
            (same_service_domain && last_service_pair_ts_ns_ > 0)
                ? (last_service_pair_ts_ns_ + target_period_ns)
                : latest_pair_time_ns;
        const auto min_target_pair_time_ns = latest_pair_time_ns - target_period_ns;
        const auto target_pair_time_ns = std::max(unclamped_target_pair_time_ns, min_target_pair_time_ns);
        ServicePairCandidate best_candidate;
        bool have_candidate = false;
        bool had_reuse_limited_candidate = false;
        bool had_repeat_only_candidate = false;
        bool had_reuse_limited_left_only = false;
        bool had_reuse_limited_right_only = false;
        bool had_reuse_limited_both = false;

        for (const auto& left_info : left_infos) {
            if (!left_info.has_time(service_time_domain)) {
                continue;
            }
            for (const auto& right_info : right_infos) {
                if (!right_info.has_time(service_time_domain)) {
                    continue;
                }
                const auto left_pair_time_ns = left_info.resolve_time_ns(service_time_domain);
                const auto right_pair_time_ns = right_info.resolve_time_ns(service_time_domain);
                const auto adjusted_right_pair_time_ns = right_pair_time_ns + effective_offset_ns;
                const auto skew_ns = std::llabs(left_pair_time_ns - adjusted_right_pair_time_ns);
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
                candidate.left_info = left_info;
                candidate.right_info = right_info;
                candidate.time_domain = service_time_domain;
                candidate.left_pair_time_ns = left_pair_time_ns;
                candidate.right_pair_time_ns = right_pair_time_ns;
                candidate.skew_ns = skew_ns;
                candidate.arrival_skew_ns =
                    std::llabs(left_info.arrival_timestamp_ns - right_info.arrival_timestamp_ns);
                candidate.source_skew_ns = compute_source_skew_ns(left_info, right_info, service_time_domain);
                candidate.sync_overage_ns = std::max<std::int64_t>(0, skew_ns - max_delta_ns);
                candidate.pair_time_ns = std::max(left_pair_time_ns, adjusted_right_pair_time_ns);
                candidate.scheduler_pair_time_ns = candidate.pair_time_ns;
                candidate.cadence_error_ns = std::llabs(candidate.pair_time_ns - target_pair_time_ns);
                candidate.freshness_ns = std::min(left_pair_time_ns, adjusted_right_pair_time_ns);
                candidate.newest_ns = candidate.pair_time_ns;
                const auto left_age_ns = std::max<std::int64_t>(0, latest_pair_time_ns - left_pair_time_ns);
                const auto right_age_ns = std::max<std::int64_t>(0, latest_pair_time_ns - adjusted_right_pair_time_ns);
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

        fill_pair(best_candidate.left_info, best_candidate.right_info);
        pair_out->pair_time_domain = best_candidate.time_domain;
        pair_out->pair_time_ns = best_candidate.pair_time_ns;
        pair_out->scheduler_pair_time_ns = best_candidate.scheduler_pair_time_ns;
        pair_out->arrival_skew_ns = best_candidate.arrival_skew_ns;
        pair_out->source_skew_ns = best_candidate.source_skew_ns;
        left_ok = !pair_out->left_frame.empty();
        right_ok = !pair_out->right_frame.empty();
    } else {
        hogak::input::BufferedFrameInfo left_info;
        hogak::input::BufferedFrameInfo right_info;
        const auto left_target_ns = left_snapshot.latest_timestamp_ns;
        const auto right_target_ns = right_snapshot.latest_timestamp_ns;
        const auto common_target_ns = (mode == "oldest")
            ? std::min(left_target_ns, right_target_ns)
            : std::max(left_target_ns, right_target_ns);
        const bool prefer_past = (mode == "oldest");
        left_ok = g_left_reader.copy_closest_frame(
            common_target_ns,
            prefer_past,
            &pair_out->left_frame,
            &pair_out->left_seq,
            &pair_out->left_ts_ns,
            hogak::input::FrameTimeDomain::kArrival,
            &left_info);
        right_ok = g_right_reader.copy_closest_frame(
            common_target_ns,
            prefer_past,
            &pair_out->right_frame,
            &pair_out->right_seq,
            &pair_out->right_ts_ns,
            hogak::input::FrameTimeDomain::kArrival,
            &right_info);
        if (left_ok && right_ok) {
            fill_pair(left_info, right_info);
            pair_out->pair_time_domain = hogak::input::FrameTimeDomain::kArrival;
            pair_out->pair_time_ns = std::max(pair_out->left_arrival_ts_ns, pair_out->right_arrival_ts_ns);
            pair_out->scheduler_pair_time_ns = pair_out->pair_time_ns;
            pair_out->arrival_skew_ns = std::llabs(pair_out->left_arrival_ts_ns - pair_out->right_arrival_ts_ns);
            pair_out->source_skew_ns = 0;
        }
    }

    if (!left_ok || !right_ok) {
        return false;
    }

    metrics_.pair_skew_ms_mean =
        static_cast<double>(pair_out->arrival_skew_ns) / 1'000'000.0;
    metrics_.pair_source_skew_ms_mean =
        static_cast<double>(pair_out->source_skew_ns) / 1'000'000.0;

    const auto left_sync_time_ns =
        (pair_out->pair_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock)
            ? pair_out->left_source_wallclock_ns
            : (pair_out->pair_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset)
                ? pair_out->left_source_pts_ns
            : pair_out->left_arrival_ts_ns;
    const auto right_sync_time_ns =
        (pair_out->pair_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock)
            ? pair_out->right_source_wallclock_ns
            : (pair_out->pair_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset)
                ? pair_out->right_source_pts_ns
            : pair_out->right_arrival_ts_ns;
    if (mode != "none" &&
        left_sync_time_ns > 0 &&
        right_sync_time_ns > 0 &&
        std::llabs(left_sync_time_ns - (right_sync_time_ns + effective_offset_ns)) > max_delta_ns) {
        metrics_.status = "waiting sync pair";
        return false;
    }
    return true;
}

void StitchEngine::clear_calibration_state_locked() {
    calibrated_ = false;
    homography_distortion_reference_ = "raw";
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
    gpu_left_corrected_.release();
    gpu_left_canvas_.release();
    gpu_stitched_.release();
    gpu_right_nv12_y_.release();
    gpu_right_nv12_uv_.release();
    gpu_right_decoded_.release();
    gpu_right_input_.release();
    gpu_right_corrected_.release();
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
    left_distortion_ = DistortionState{};
    right_distortion_ = DistortionState{};
    metrics_.output_width = 0;
    metrics_.output_height = 0;
    metrics_.production_output_width = 0;
    metrics_.production_output_height = 0;
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.overlap_diff_mean = 0.0;
    metrics_.geometry_mode = "planar-homography";
    metrics_.alignment_mode = "homography";
    metrics_.seam_mode = "seam_feather";
    metrics_.exposure_mode = "off";
    metrics_.geometry_artifact_path.clear();
    metrics_.geometry_artifact_model = "planar-homography";
    metrics_.cylindrical_focal_px = 0.0;
    metrics_.cylindrical_center_x = 0.0;
    metrics_.cylindrical_center_y = 0.0;
    metrics_.residual_alignment_error_px = 0.0;
    metrics_.seam_path_jitter_px = 0.0;
    metrics_.exposure_gain = 1.0;
    metrics_.exposure_bias = 0.0;
    metrics_.blend_mode = "seam_feather";
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
    metrics_.geometry_mode = "planar-homography";
    metrics_.output_target = config.output.target;
    metrics_.output_command_line.clear();
    metrics_.output_runtime_mode = output_runtime_mode_hint(config.output.runtime);
    metrics_.production_output_target = config.production_output.target;
    metrics_.production_output_command_line.clear();
    metrics_.production_output_runtime_mode = output_runtime_mode_hint(config.production_output.runtime);
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_service_pair_ts_ns_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_pair_time_domain_ = hogak::input::FrameTimeDomain::kArrival;
    last_sync_recalibration_ns_ = 0;
    effective_sync_offset_ms_ = 0.0;
    sync_offset_confidence_ = 0.0;
    sync_recalibration_count_ = 0;
    sync_offset_source_ = "arrival-fallback";
    sync_estimate_pairs_ = 0;
    sync_estimate_avg_gap_ms_ = 0.0;
    sync_estimate_score_ = 0.0;
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
    load_runtime_geometry_locked();
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
    last_pair_time_domain_ = hogak::input::FrameTimeDomain::kArrival;
    last_sync_recalibration_ns_ = 0;
    effective_sync_offset_ms_ = 0.0;
    sync_offset_confidence_ = 0.0;
    sync_recalibration_count_ = 0;
    sync_offset_source_ = "arrival-fallback";
    sync_estimate_pairs_ = 0;
    sync_estimate_avg_gap_ms_ = 0.0;
    sync_estimate_score_ = 0.0;
    consecutive_left_reuse_ = 0;
    consecutive_right_reuse_ = 0;
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
        g_left_reader.stop();
        g_right_reader.stop();
    }

    config_ = config;
    clear_calibration_state_locked();
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_service_pair_ts_ns_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_pair_time_domain_ = hogak::input::FrameTimeDomain::kArrival;
    last_sync_recalibration_ns_ = 0;
    effective_sync_offset_ms_ = 0.0;
    sync_offset_confidence_ = 0.0;
    sync_recalibration_count_ = 0;
    sync_offset_source_ = "arrival-fallback";
    sync_estimate_pairs_ = 0;
    sync_estimate_avg_gap_ms_ = 0.0;
    sync_estimate_score_ = 0.0;
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
    metrics_.output_runtime_mode = output_runtime_mode_hint(config.output.runtime);
    metrics_.output_target = config.output.target;
    metrics_.production_output_last_error.clear();
    metrics_.production_output_command_line.clear();
    metrics_.production_output_effective_codec.clear();
    metrics_.production_output_runtime_mode = output_runtime_mode_hint(config.production_output.runtime);
    metrics_.production_output_target = config.production_output.target;
    metrics_.status = "config reloaded";
    load_runtime_geometry_locked();

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
    load_runtime_geometry_locked();
    metrics_.output_last_error.clear();
    metrics_.output_command_line.clear();
    metrics_.output_effective_codec.clear();
    metrics_.output_runtime_mode = output_runtime_mode_hint(config_.output.runtime);
    metrics_.production_output_last_error.clear();
    metrics_.production_output_command_line.clear();
    metrics_.production_output_effective_codec.clear();
    metrics_.production_output_runtime_mode = output_runtime_mode_hint(config_.production_output.runtime);
    metrics_.status = "calibration reset";
    last_left_seq_ = 0;
    last_right_seq_ = 0;
    last_service_pair_ts_ns_ = 0;
    last_worker_timestamp_ns_ = 0;
    last_pair_time_domain_ = hogak::input::FrameTimeDomain::kArrival;
    last_sync_recalibration_ns_ = 0;
    effective_sync_offset_ms_ = 0.0;
    sync_offset_confidence_ = 0.0;
    sync_recalibration_count_ = 0;
    sync_offset_source_ = "arrival-fallback";
    sync_estimate_pairs_ = 0;
    sync_estimate_avg_gap_ms_ = 0.0;
    sync_estimate_score_ = 0.0;
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
    homography_distortion_reference_ = "raw";
    if (config_.homography_file.empty()) {
        *homography_out = cv::Mat::eye(3, 3, CV_64F);
        return true;
    }
    return load_homography_from_file(config_.homography_file, homography_out, &homography_distortion_reference_);
}

bool StitchEngine::load_runtime_geometry_from_file(const std::string& path, RuntimeGeometryState* state) {
    if (state == nullptr || path.empty()) {
        return false;
    }

    RuntimeGeometryArtifactData artifact;
    if (!load_runtime_geometry_artifact_from_file(path, &artifact)) {
        return false;
    }

    state->model = artifact.model.empty() ? "planar-homography" : artifact.model;
    state->alignment_model = artifact.alignment_model.empty() ? "homography" : artifact.alignment_model;
    state->residual_model = artifact.residual_model.empty() ? state->alignment_model : artifact.residual_model;
    if (state->model == "cylindrical_affine") {
        state->model = "cylindrical-affine";
    } else if (state->model == "virtual_center_rectilinear") {
        state->model = "virtual-center-rectilinear";
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
    state->mesh_fallback_used = artifact.mesh_fallback_used;
    state->mesh_grid_cols = artifact.mesh_grid_cols;
    state->mesh_grid_rows = artifact.mesh_grid_rows;
    state->mesh_control_displacement_x = artifact.mesh_control_displacement_x.clone();
    state->mesh_control_displacement_y = artifact.mesh_control_displacement_y.clone();
    state->mesh_max_displacement_px = artifact.mesh_max_displacement_px;
    state->mesh_max_local_scale_drift = artifact.mesh_max_local_scale_drift;
    state->mesh_max_local_rotation_drift = artifact.mesh_max_local_rotation_drift;
    state->mesh_enabled =
        state->model == "virtual-center-rectilinear" &&
        state->residual_model == "mesh" &&
        !state->mesh_fallback_used;
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
    return runtime_geometry_.model == "virtual-center-rectilinear" &&
           runtime_geometry_.residual_model == "mesh";
}

bool StitchEngine::runtime_geometry_mesh_active_locked() const {
    return runtime_geometry_requests_mesh_locked() &&
           runtime_geometry_.mesh_enabled &&
           !runtime_geometry_.mesh_fallback_used;
}

std::string StitchEngine::runtime_geometry_public_model_locked() const {
    if (runtime_geometry_.model == "virtual-center-rectilinear") {
        return runtime_geometry_mesh_active_locked()
            ? "virtual-center-rectilinear-mesh"
            : "virtual-center-rectilinear-rigid";
    }
    return runtime_geometry_.model.empty() ? "planar-homography" : runtime_geometry_.model;
}

std::string StitchEngine::runtime_geometry_artifact_truth_locked() const {
    if (runtime_geometry_.model == "virtual-center-rectilinear") {
        return runtime_geometry_requests_mesh_locked()
            ? "virtual-center-rectilinear-mesh"
            : "virtual-center-rectilinear-rigid";
    }
    return runtime_geometry_.model.empty() ? "planar-homography" : runtime_geometry_.model;
}

std::string StitchEngine::runtime_alignment_truth_locked() const {
    if (runtime_geometry_.model == "virtual-center-rectilinear") {
        return runtime_geometry_mesh_active_locked() ? "mesh" : "rigid";
    }
    return runtime_geometry_.alignment_model.empty() ? "homography" : runtime_geometry_.alignment_model;
}

std::string StitchEngine::runtime_seam_truth_locked() const {
    if (runtime_geometry_.model == "cylindrical-affine" ||
        runtime_geometry_.model == "virtual-center-rectilinear") {
        return "min-cost-seam";
    }
    return "seam_feather";
}

std::string StitchEngine::runtime_exposure_truth_locked() const {
    return ((runtime_geometry_.model == "cylindrical-affine" ||
             runtime_geometry_.model == "virtual-center-rectilinear") &&
            runtime_geometry_.exposure_enabled)
        ? "gain-bias"
        : "off";
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
    const bool cylindrical_runtime = runtime_geometry_.model == "cylindrical-affine";
    const bool rectilinear_runtime = runtime_geometry_.model == "virtual-center-rectilinear";
    const bool rectilinear_mesh_requested = rectilinear_runtime && runtime_geometry_requests_mesh_locked();
    if (!cylindrical_runtime && !rectilinear_runtime) {
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
    if (cylindrical_runtime) {
        if (!build_cylindrical_maps_locked(
                left_size,
                runtime_geometry_.left_focal_px,
                runtime_geometry_.left_center_x,
                runtime_geometry_.left_center_y,
                &runtime_geometry_.cylindrical_left_map_x,
                &runtime_geometry_.cylindrical_left_map_y)) {
            return false;
        }
        if (!build_cylindrical_maps_locked(
                right_size,
                runtime_geometry_.right_focal_px,
                runtime_geometry_.right_center_x,
                runtime_geometry_.right_center_y,
                &runtime_geometry_.cylindrical_right_map_x,
                &runtime_geometry_.cylindrical_right_map_y)) {
            return false;
        }
    } else {
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
    }
    if (gpu_available_) {
        try {
            if (cylindrical_runtime) {
                runtime_geometry_.cylindrical_left_map_x_gpu.upload(runtime_geometry_.cylindrical_left_map_x);
                runtime_geometry_.cylindrical_left_map_y_gpu.upload(runtime_geometry_.cylindrical_left_map_y);
                runtime_geometry_.cylindrical_right_map_x_gpu.upload(runtime_geometry_.cylindrical_right_map_x);
                runtime_geometry_.cylindrical_right_map_y_gpu.upload(runtime_geometry_.cylindrical_right_map_y);
            } else {
                runtime_geometry_.rectilinear_left_map_x_gpu.upload(runtime_geometry_.rectilinear_left_map_x);
                runtime_geometry_.rectilinear_left_map_y_gpu.upload(runtime_geometry_.rectilinear_left_map_y);
                runtime_geometry_.rectilinear_right_map_x_gpu.upload(runtime_geometry_.rectilinear_right_map_x);
                runtime_geometry_.rectilinear_right_map_y_gpu.upload(runtime_geometry_.rectilinear_right_map_y);
                if (runtime_geometry_.mesh_enabled) {
                    runtime_geometry_.mesh_map_x_gpu.upload(runtime_geometry_.mesh_map_x);
                    runtime_geometry_.mesh_map_y_gpu.upload(runtime_geometry_.mesh_map_y);
                }
            }
        } catch (const cv::Exception& e) {
            if (rectilinear_runtime) {
                metrics_.gpu_errors += 1;
                metrics_.status = "virtual_center_rectilinear_map_upload_failed";
                metrics_.gpu_reason = std::string("cuda rectilinear map upload failed: ") + e.what();
                return false;
            }
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = std::string("cuda cylindrical map upload failed: ") + e.what();
        }
    }

    cv::Size output_size;
    cv::Rect left_roi;
    cv::Rect overlap_roi_unused;
    cv::Mat adjusted_affine;
    if (rectilinear_runtime) {
        output_size = rectilinear_output_size;
        left_roi = cv::Rect(0, 0, output_size.width, output_size.height);
        overlap_roi_unused = cv::Rect();
        adjusted_affine = runtime_geometry_.alignment_matrix.empty()
            ? cv::Mat::eye(3, 3, CV_64F)
            : runtime_geometry_.alignment_matrix.clone();
    } else if (!build_affine_output_plan_locked(
                   left_size,
                   right_size,
                   runtime_geometry_.alignment_matrix,
                   &output_size,
                   &left_roi,
                   &overlap_roi_unused,
                   &adjusted_affine)) {
        return false;
    }

    runtime_geometry_.output_size = output_size;
    runtime_geometry_.alignment_matrix = adjusted_affine;
    last_residual_alignment_error_px_ = runtime_geometry_.residual_alignment_error_px;
    left_roi_ = left_roi;
    output_size_ = output_size;
    homography_ = runtime_geometry_.alignment_matrix.clone();
    homography_adjusted_ = runtime_geometry_.alignment_matrix.clone();

    if (rectilinear_runtime) {
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
    } else {
        left_mask_template_ = cv::Mat::zeros(output_size_, CV_8UC1);
        if (left_roi_.area() > 0) {
            left_mask_template_(left_roi_).setTo(cv::Scalar(255));
        }
        cv::Mat right_mask_source(right_size, CV_8UC1, cv::Scalar(255));
        cv::warpPerspective(
            right_mask_source,
            right_mask_template_,
            runtime_geometry_.alignment_matrix,
            output_size_,
            cv::INTER_NEAREST,
            cv::BORDER_CONSTANT);
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
    metrics_.cylindrical_focal_px = runtime_geometry_.left_focal_px;
    metrics_.cylindrical_center_x = runtime_geometry_.left_center_x;
    metrics_.cylindrical_center_y = runtime_geometry_.left_center_y;
    metrics_.residual_alignment_error_px = last_residual_alignment_error_px_;
    metrics_.seam_path_jitter_px = last_seam_path_jitter_px_;
    metrics_.exposure_gain = last_exposure_gain_;
    metrics_.exposure_bias = last_exposure_bias_;
    metrics_.blend_mode = metrics_.seam_mode;
    return true;
}

void StitchEngine::apply_runtime_geometry_to_metrics_locked() {
    metrics_.geometry_mode = runtime_geometry_public_model_locked();
    metrics_.alignment_mode = runtime_alignment_truth_locked();
    metrics_.seam_mode = runtime_seam_truth_locked();
    metrics_.exposure_mode = runtime_exposure_truth_locked();
    metrics_.geometry_artifact_path = runtime_geometry_source_path_;
    metrics_.geometry_artifact_model = runtime_geometry_artifact_truth_locked();
    metrics_.cylindrical_focal_px = runtime_geometry_.left_focal_px;
    metrics_.cylindrical_center_x = runtime_geometry_.left_center_x;
    metrics_.cylindrical_center_y = runtime_geometry_.left_center_y;
    metrics_.residual_alignment_error_px = last_residual_alignment_error_px_;
    metrics_.seam_path_jitter_px = last_seam_path_jitter_px_;
    metrics_.exposure_gain = last_exposure_gain_;
    metrics_.exposure_bias = last_exposure_bias_;
    metrics_.blend_mode = metrics_.seam_mode;
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
            config_.process_scale <= 0.0 ? 4.0 : config_.process_scale,
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

    auto configure_distortion_state = [&](const std::string& configured_path,
                                          const std::string& configured_hint,
                                          const cv::Size& runtime_size,
                                          DistortionState* state) {
        if (state == nullptr) {
            return;
        }
        *state = DistortionState{};
        state->model = "opencv_pinhole";
        if (config_.distortion_mode == "off") {
            return;
        }
        if (homography_distortion_reference_ != "undistorted") {
            return;
        }
        if (runtime_size.width <= 0 || runtime_size.height <= 0) {
            return;
        }

        std::string source_hint = configured_hint;
        for (char& ch : source_hint) {
            ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
        }
        if (source_hint.empty()) {
            source_hint = "off";
        }
        if (source_hint == "off" && config_.use_saved_distortion && !configured_path.empty()) {
            source_hint = "saved";
        }
        if (source_hint == "off" || configured_path.empty()) {
            return;
        }

        DistortionProfileData profile;
        if (!load_distortion_profile_from_file(configured_path, &profile)) {
            return;
        }

        cv::Mat scaled_camera_matrix =
            scale_camera_matrix_to_runtime(profile.camera_matrix, profile.image_size, runtime_size);
        cv::Mat scaled_projection_matrix =
            scale_camera_matrix_to_runtime(
                profile.projection_matrix.empty() ? profile.camera_matrix : profile.projection_matrix,
                profile.image_size,
                runtime_size);
        if (scaled_camera_matrix.empty() || profile.dist_coeffs.empty()) {
            return;
        }
        if (scaled_projection_matrix.empty()) {
            scaled_projection_matrix = scaled_camera_matrix.clone();
        }

        cv::Mat map_x_cpu;
        cv::Mat map_y_cpu;
        if (profile.model == "opencv_fisheye") {
            cv::Mat fisheye_dist = profile.dist_coeffs.reshape(1, 1).clone();
            if (fisheye_dist.cols < 4) {
                return;
            }
            if (fisheye_dist.cols > 4) {
                fisheye_dist = fisheye_dist.colRange(0, 4).clone();
            }
            cv::fisheye::initUndistortRectifyMap(
                scaled_camera_matrix,
                fisheye_dist,
                cv::Mat::eye(3, 3, CV_64F),
                scaled_projection_matrix,
                runtime_size,
                CV_32FC1,
                map_x_cpu,
                map_y_cpu);
        } else {
            cv::initUndistortRectifyMap(
                scaled_camera_matrix,
                profile.dist_coeffs,
                cv::Mat(),
                scaled_projection_matrix,
                runtime_size,
                CV_32FC1,
                map_x_cpu,
                map_y_cpu);
        }
        if (map_x_cpu.empty() || map_y_cpu.empty()) {
            return;
        }

        state->enabled = true;
        state->source = source_hint;
        state->model = profile.model.empty() ? "opencv_pinhole" : profile.model;
        state->confidence = std::max(0.0, profile.confidence);
        state->fit_score = std::max(0.0, profile.fit_score);
        state->line_count = std::max<std::int64_t>(0, profile.line_count);
        state->frame_count_used = std::max<std::int64_t>(0, profile.frame_count_used);
        state->image_size = runtime_size;
        state->camera_matrix = scaled_camera_matrix;
        state->projection_matrix = scaled_projection_matrix;
        state->dist_coeffs = profile.dist_coeffs.clone();
        state->map_x_cpu = map_x_cpu;
        state->map_y_cpu = map_y_cpu;

        if (gpu_available_) {
            try {
                state->map_x_gpu.upload(state->map_x_cpu);
                state->map_y_gpu.upload(state->map_y_cpu);
            } catch (const cv::Exception& e) {
                gpu_available_ = false;
                metrics_.gpu_errors += 1;
                metrics_.gpu_reason = std::string("cuda distortion map upload failed: ") + e.what();
            }
        }
    };

    if (runtime_geometry_.model == "cylindrical-affine" && prepare_runtime_geometry_locked(left_size, right_size)) {
        configure_distortion_state(
            config_.left_distortion_file,
            config_.left_distortion_source_hint,
            left_size,
            &left_distortion_);
        configure_distortion_state(
            config_.right_distortion_file,
            config_.right_distortion_source_hint,
            right_size,
            &right_distortion_);
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
    if (runtime_geometry_.model == "cylindrical-affine") {
        runtime_geometry_ = RuntimeGeometryState{};
        runtime_geometry_source_path_.clear();
        last_residual_alignment_error_px_ = 0.0;
        apply_runtime_geometry_to_metrics_locked();
    }

    configure_distortion_state(
        config_.left_distortion_file,
        config_.left_distortion_source_hint,
        left_size,
        &left_distortion_);
    configure_distortion_state(
        config_.right_distortion_file,
        config_.right_distortion_source_hint,
        right_size,
        &right_distortion_);

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
    const bool stale_pair = left_reused || right_reused || metrics_.left_content_frozen || metrics_.right_content_frozen;
    const bool gpu_only_mode = gpu_only_mode_enabled(config_);
    const bool sample_heavy_metrics = !gpu_only_mode && should_sample_heavy_metrics(next_frame_index) && !stale_pair;
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
    cv::Mat left_corrected_cpu;
    cv::Mat right_corrected_cpu;
    bool used_gpu_blend = false;
    bool used_gpu_geometry = false;
    bool gpu_output_frame_ready = false;
    bool used_dynamic_seam = false;
    bool used_exposure_compensation = false;
    const bool cylindrical_runtime = runtime_geometry_.model == "cylindrical-affine";
    const bool rectilinear_runtime = runtime_geometry_.model == "virtual-center-rectilinear";
    if (rectilinear_runtime) {
        if (!gpu_available_) {
            metrics_.status = "virtual_center_rectilinear_requires_gpu";
            metrics_.stitch_fps = 0.0;
            return false;
        }
    }

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
            if (rectilinear_runtime && (!left_gpu_fast || !right_gpu_fast)) {
                throw cv::Exception(
                    cv::Error::StsError,
                    "virtual-center-rectilinear requires NV12 GPU input on both cameras",
                    __FUNCTION__,
                    __FILE__,
                    __LINE__);
            }
            auto ensure_cpu_frame = [&](cv::Mat* cpu_frame,
                                        const cv::Mat* raw_input,
                                        const StreamConfig& stream_config,
                                        std::int64_t input_seq,
                                        cv::Mat* cached_frame,
                                        std::int64_t* cached_seq) -> bool {
                if (cpu_frame == nullptr || !cpu_frame->empty()) {
                    return cpu_frame != nullptr;
                }
                if (cached_frame != nullptr &&
                    cached_seq != nullptr &&
                    input_seq > 0 &&
                    *cached_seq == input_seq &&
                    !cached_frame->empty()) {
                    *cpu_frame = *cached_frame;
                    return true;
                }
                if (raw_input == nullptr) {
                    return false;
                }
                cv::Mat decoded = decode_input_frame_for_stitch(*raw_input, stream_config);
                if (decoded.empty()) {
                    return false;
                }
                *cpu_frame = resize_frame_for_runtime(decoded, output_scale);
                if (cached_frame != nullptr && cached_seq != nullptr && !cpu_frame->empty()) {
                    *cached_frame = *cpu_frame;
                    *cached_seq = input_seq;
                }
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
                                                     const char* label,
                                                     std::int64_t input_seq,
                                                     cv::Mat* cached_frame,
                                                     std::int64_t* cached_seq) {
                if (raw_input == nullptr) {
                    if (!ensure_cpu_frame(cpu_frame, raw_input, stream_config, input_seq, cached_frame, cached_seq)) {
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
                    if (gpu_only_mode) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "gpu-only nv12 upload path unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    if (!ensure_cpu_frame(cpu_frame, raw_input, stream_config, input_seq, cached_frame, cached_seq)) {
                        throw;
                    }
                    upload_cpu_frame(cpu_frame, final_bgr_gpu, label);
                }
            };

            const bool reuse_left_gpu_input =
                selected_pair.left_seq > 0 &&
                cached_left_gpu_input_seq_ == selected_pair.left_seq &&
                !gpu_left_input_.empty();
            if (!reuse_left_gpu_input) {
                if (left_gpu_fast) {
                    upload_with_nv12_gpu_fallback(
                        left_raw_input,
                        config_.left,
                        &left_cpu_frame,
                        &gpu_left_nv12_y_,
                        &gpu_left_nv12_uv_,
                        &gpu_left_decoded_,
                        &gpu_left_input_,
                        "left",
                        selected_pair.left_seq,
                        &cached_left_cpu_frame_,
                        &cached_left_cpu_seq_);
                } else {
                    if (!ensure_cpu_frame(
                            &left_cpu_frame,
                            left_raw_input,
                            config_.left,
                            selected_pair.left_seq,
                            &cached_left_cpu_frame_,
                            &cached_left_cpu_seq_)) {
                        throw cv::Exception(cv::Error::StsError, "left input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                    }
                    upload_cpu_frame(&left_cpu_frame, &gpu_left_input_, "left");
                }
                cached_left_gpu_input_seq_ = selected_pair.left_seq;
            }
            if (cached_left_canvas_seq_ != selected_pair.left_seq || gpu_left_canvas_.empty()) {
                const cv::cuda::GpuMat* left_gpu_for_stitch = &gpu_left_input_;
                if (left_distortion_.enabled) {
                    if (left_distortion_.map_x_gpu.empty() || left_distortion_.map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "left distortion gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        gpu_left_input_,
                        gpu_left_corrected_,
                        left_distortion_.map_x_gpu,
                        left_distortion_.map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                    left_gpu_for_stitch = &gpu_left_corrected_;
                }
                if (cylindrical_runtime) {
                    if (runtime_geometry_.cylindrical_left_map_x_gpu.empty() ||
                        runtime_geometry_.cylindrical_left_map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "left cylindrical gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        *left_gpu_for_stitch,
                        gpu_left_cylindrical_,
                        runtime_geometry_.cylindrical_left_map_x_gpu,
                        runtime_geometry_.cylindrical_left_map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                    left_gpu_for_stitch = &gpu_left_cylindrical_;
                } else if (rectilinear_runtime) {
                    if (runtime_geometry_.rectilinear_left_map_x_gpu.empty() ||
                        runtime_geometry_.rectilinear_left_map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "left rectilinear gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        *left_gpu_for_stitch,
                        gpu_left_rectilinear_,
                        runtime_geometry_.rectilinear_left_map_x_gpu,
                        runtime_geometry_.rectilinear_left_map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                    left_gpu_for_stitch = &gpu_left_rectilinear_;
                }
                if (rectilinear_runtime) {
                    if (left_gpu_for_stitch->size() != output_size_) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "left virtual-center projection size does not match output canvas",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    left_gpu_for_stitch->copyTo(gpu_left_canvas_);
                } else {
                    gpu_left_canvas_.create(output_size_, CV_8UC3);
                    gpu_left_canvas_.setTo(cv::Scalar::all(0));
                    cv::cuda::GpuMat left_roi_gpu(gpu_left_canvas_, left_roi_);
                    left_gpu_for_stitch->copyTo(left_roi_gpu);
                }
                cached_left_canvas_seq_ = selected_pair.left_seq;
            }

            const bool reuse_right_gpu_input =
                selected_pair.right_seq > 0 &&
                cached_right_gpu_input_seq_ == selected_pair.right_seq &&
                !gpu_right_input_.empty();
            if (!reuse_right_gpu_input) {
                if (right_gpu_fast) {
                    upload_with_nv12_gpu_fallback(
                        right_raw_input,
                        config_.right,
                        &right_cpu_frame,
                        &gpu_right_nv12_y_,
                        &gpu_right_nv12_uv_,
                        &gpu_right_decoded_,
                        &gpu_right_input_,
                        "right",
                        selected_pair.right_seq,
                        &cached_right_cpu_frame_,
                        &cached_right_cpu_seq_);
                } else {
                    if (!ensure_cpu_frame(
                            &right_cpu_frame,
                            right_raw_input,
                            config_.right,
                            selected_pair.right_seq,
                            &cached_right_cpu_frame_,
                            &cached_right_cpu_seq_)) {
                        throw cv::Exception(cv::Error::StsError, "right input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                    }
                    upload_cpu_frame(&right_cpu_frame, &gpu_right_input_, "right");
                }
                cached_right_gpu_input_seq_ = selected_pair.right_seq;
            }
            if (cached_right_warped_seq_ != selected_pair.right_seq || gpu_right_warped_.empty()) {
                const cv::cuda::GpuMat* right_gpu_for_stitch = &gpu_right_input_;
                if (right_distortion_.enabled) {
                    if (right_distortion_.map_x_gpu.empty() || right_distortion_.map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "right distortion gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        gpu_right_input_,
                        gpu_right_corrected_,
                        right_distortion_.map_x_gpu,
                        right_distortion_.map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                    right_gpu_for_stitch = &gpu_right_corrected_;
                }
                if (cylindrical_runtime) {
                    if (runtime_geometry_.cylindrical_right_map_x_gpu.empty() ||
                        runtime_geometry_.cylindrical_right_map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "right cylindrical gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        *right_gpu_for_stitch,
                        gpu_right_cylindrical_,
                        runtime_geometry_.cylindrical_right_map_x_gpu,
                        runtime_geometry_.cylindrical_right_map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                    right_gpu_for_stitch = &gpu_right_cylindrical_;
                } else if (rectilinear_runtime) {
                    if (runtime_geometry_.rectilinear_right_map_x_gpu.empty() ||
                        runtime_geometry_.rectilinear_right_map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "right rectilinear gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        *right_gpu_for_stitch,
                        gpu_right_rectilinear_,
                        runtime_geometry_.rectilinear_right_map_x_gpu,
                        runtime_geometry_.rectilinear_right_map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                    right_gpu_for_stitch = &gpu_right_rectilinear_;
                }
                if (rectilinear_runtime) {
                    cv::cuda::warpPerspective(
                        *right_gpu_for_stitch,
                        gpu_right_aligned_,
                        homography_adjusted_,
                        output_size_);
                } else {
                    cv::cuda::warpPerspective(
                        *right_gpu_for_stitch,
                        gpu_right_warped_,
                        homography_adjusted_,
                        output_size_);
                }
                if (rectilinear_runtime && runtime_geometry_.mesh_enabled) {
                    if (runtime_geometry_.mesh_map_x_gpu.empty() || runtime_geometry_.mesh_map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "right virtual-center mesh gpu map unavailable",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    cv::cuda::remap(
                        gpu_right_aligned_,
                        gpu_right_warped_,
                        runtime_geometry_.mesh_map_x_gpu,
                        runtime_geometry_.mesh_map_y_gpu,
                        cv::INTER_LINEAR,
                        cv::BORDER_CONSTANT,
                        cv::Scalar());
                } else if (rectilinear_runtime) {
                    gpu_right_aligned_.copyTo(gpu_right_warped_);
                }
                metrics_.gpu_warp_count += 1;
                cached_right_warped_seq_ = selected_pair.right_seq;
            }
            used_gpu_geometry = true;
            if (rectilinear_runtime) {
                gpu_left_canvas_.download(canvas_left);
                gpu_right_warped_.download(warped_right);
                if (!compose_stitched_video_quality_locked(
                        canvas_left,
                        warped_right,
                        &stitched,
                        &used_exposure_compensation,
                        &used_dynamic_seam)) {
                    throw cv::Exception(
                        cv::Error::StsError,
                        "virtual-center stitched-video quality compose failed",
                        __FUNCTION__,
                        __FILE__,
                        __LINE__);
                }
                try {
                    gpu_stitched_.upload(stitched);
                    gpu_output_frame_ready = !gpu_stitched_.empty();
                } catch (const cv::Exception& upload_error) {
                    metrics_.gpu_errors += 1;
                    metrics_.gpu_reason =
                        std::string("virtual-center cropped stitch upload failed: ") + upload_error.what();
                    gpu_stitched_.release();
                    gpu_output_frame_ready = false;
                }
            } else {
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
            }
        } catch (const cv::Exception& e) {
            if (rectilinear_runtime) {
                metrics_.gpu_errors += 1;
                metrics_.gpu_reason = std::string("virtual-center-rectilinear gpu stitch failed: ") + e.what();
                metrics_.status = "virtual_center_rectilinear_gpu_stitch_failed";
                metrics_.stitch_fps = 0.0;
                return false;
            }
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = std::string("cuda stitch failed: ") + e.what();
        }
    }

    if (!used_gpu_blend && stitched.empty()) {
        if (gpu_only_mode) {
            metrics_.status = "gpu_only_path_unavailable";
            metrics_.stitch_fps = 0.0;
            metrics_.gpu_feature_enabled = false;
            metrics_.gpu_feature_reason = "gpu-only mode could not keep the stitch path on GPU";
            return false;
        }
        if (runtime_geometry_.model == "cylindrical-affine") {
            if (left_cpu_frame.empty()) {
                if (selected_pair.left_seq > 0 &&
                    cached_left_cpu_seq_ == selected_pair.left_seq &&
                    !cached_left_cpu_frame_.empty()) {
                    left_cpu_frame = cached_left_cpu_frame_;
                } else if (left_raw_input != nullptr) {
                    cv::Mat left_decoded = decode_input_frame_for_stitch(*left_raw_input, config_.left);
                    if (!left_decoded.empty()) {
                        left_cpu_frame = resize_frame_for_runtime(left_decoded, output_scale);
                        cached_left_cpu_frame_ = left_cpu_frame;
                        cached_left_cpu_seq_ = selected_pair.left_seq;
                    }
                }
            }
            if (right_cpu_frame.empty()) {
                if (selected_pair.right_seq > 0 &&
                    cached_right_cpu_seq_ == selected_pair.right_seq &&
                    !cached_right_cpu_frame_.empty()) {
                    right_cpu_frame = cached_right_cpu_frame_;
                } else if (right_raw_input != nullptr) {
                    cv::Mat right_decoded = decode_input_frame_for_stitch(*right_raw_input, config_.right);
                    if (!right_decoded.empty()) {
                        right_cpu_frame = resize_frame_for_runtime(right_decoded, output_scale);
                        cached_right_cpu_frame_ = right_cpu_frame;
                        cached_right_cpu_seq_ = selected_pair.right_seq;
                    }
                }
            }
            if (left_cpu_frame.empty() || right_cpu_frame.empty()) {
                metrics_.status = "input decode failed";
                metrics_.stitch_fps = 0.0;
                return false;
            }
            const cv::Mat* left_cylindrical = &left_cpu_frame;
            const cv::Mat* right_cylindrical = &right_cpu_frame;
            if (!runtime_geometry_.cylindrical_left_map_x.empty() && !runtime_geometry_.cylindrical_left_map_y.empty()) {
                cv::remap(
                    left_cpu_frame,
                    left_corrected_cpu,
                    runtime_geometry_.cylindrical_left_map_x,
                    runtime_geometry_.cylindrical_left_map_y,
                    cv::INTER_LINEAR,
                    cv::BORDER_CONSTANT);
                if (!left_corrected_cpu.empty()) {
                    left_cylindrical = &left_corrected_cpu;
                }
            }
            if (!runtime_geometry_.cylindrical_right_map_x.empty() && !runtime_geometry_.cylindrical_right_map_y.empty()) {
                cv::remap(
                    right_cpu_frame,
                    right_corrected_cpu,
                    runtime_geometry_.cylindrical_right_map_x,
                    runtime_geometry_.cylindrical_right_map_y,
                    cv::INTER_LINEAR,
                    cv::BORDER_CONSTANT);
                if (!right_corrected_cpu.empty()) {
                    right_cylindrical = &right_corrected_cpu;
                }
            }
            if (cached_left_canvas_seq_ == selected_pair.left_seq && !cached_left_canvas_cpu_.empty()) {
                canvas_left = cached_left_canvas_cpu_;
            } else {
                canvas_left = cv::Mat::zeros(output_size_, CV_8UC3);
                if (left_roi_.area() > 0) {
                    left_cylindrical->copyTo(canvas_left(left_roi_));
                }
                cached_left_canvas_cpu_ = canvas_left;
                cached_left_canvas_seq_ = selected_pair.left_seq;
            }
            if (cached_right_warped_seq_ == selected_pair.right_seq && !cached_right_warped_cpu_.empty()) {
                warped_right = cached_right_warped_cpu_;
            } else {
                cv::warpPerspective(
                    *right_cylindrical,
                    warped_right,
                    homography_adjusted_,
                    output_size_);
                metrics_.cpu_warp_count += 1;
                cached_right_warped_cpu_ = warped_right;
                cached_right_warped_seq_ = selected_pair.right_seq;
            }

            if (!compose_stitched_video_quality_locked(
                    canvas_left,
                    warped_right,
                    &stitched,
                    &used_exposure_compensation,
                    &used_dynamic_seam)) {
                metrics_.status = "dynamic_seam_compose_failed";
                metrics_.stitch_fps = 0.0;
                return false;
            }
        } else {
        if (left_cpu_frame.empty()) {
            if (selected_pair.left_seq > 0 &&
                cached_left_cpu_seq_ == selected_pair.left_seq &&
                !cached_left_cpu_frame_.empty()) {
                left_cpu_frame = cached_left_cpu_frame_;
            } else if (left_raw_input != nullptr) {
                cv::Mat left_decoded = decode_input_frame_for_stitch(*left_raw_input, config_.left);
                if (!left_decoded.empty()) {
                    left_cpu_frame = resize_frame_for_runtime(left_decoded, output_scale);
                    cached_left_cpu_frame_ = left_cpu_frame;
                    cached_left_cpu_seq_ = selected_pair.left_seq;
                }
            }
        }
        if (right_cpu_frame.empty()) {
            if (selected_pair.right_seq > 0 &&
                cached_right_cpu_seq_ == selected_pair.right_seq &&
                !cached_right_cpu_frame_.empty()) {
                right_cpu_frame = cached_right_cpu_frame_;
            } else if (right_raw_input != nullptr) {
                cv::Mat right_decoded = decode_input_frame_for_stitch(*right_raw_input, config_.right);
                if (!right_decoded.empty()) {
                    right_cpu_frame = resize_frame_for_runtime(right_decoded, output_scale);
                    cached_right_cpu_frame_ = right_cpu_frame;
                    cached_right_cpu_seq_ = selected_pair.right_seq;
                }
            }
        }
        if (left_cpu_frame.empty() || right_cpu_frame.empty()) {
            metrics_.status = "input decode failed";
            metrics_.stitch_fps = 0.0;
            return false;
        }
        const cv::Mat* left_cpu_for_stitch = &left_cpu_frame;
        if (left_distortion_.enabled && !left_distortion_.map_x_cpu.empty() && !left_distortion_.map_y_cpu.empty()) {
            cv::remap(
                left_cpu_frame,
                left_corrected_cpu,
                left_distortion_.map_x_cpu,
                left_distortion_.map_y_cpu,
                cv::INTER_LINEAR,
                cv::BORDER_CONSTANT);
            if (!left_corrected_cpu.empty()) {
                left_cpu_for_stitch = &left_corrected_cpu;
            }
        }
        const cv::Mat* right_cpu_for_stitch = &right_cpu_frame;
        if (right_distortion_.enabled && !right_distortion_.map_x_cpu.empty() && !right_distortion_.map_y_cpu.empty()) {
            cv::remap(
                right_cpu_frame,
                right_corrected_cpu,
                right_distortion_.map_x_cpu,
                right_distortion_.map_y_cpu,
                cv::INTER_LINEAR,
                cv::BORDER_CONSTANT);
            if (!right_corrected_cpu.empty()) {
                right_cpu_for_stitch = &right_corrected_cpu;
            }
        }
        if (cached_left_canvas_seq_ == selected_pair.left_seq && !cached_left_canvas_cpu_.empty()) {
            canvas_left = cached_left_canvas_cpu_;
        } else {
            canvas_left = cv::Mat::zeros(output_size_, CV_8UC3);
            left_cpu_for_stitch->copyTo(canvas_left(left_roi_));
            cached_left_canvas_cpu_ = canvas_left;
            cached_left_canvas_seq_ = selected_pair.left_seq;
        }
        if (cached_right_warped_seq_ == selected_pair.right_seq && !cached_right_warped_cpu_.empty()) {
            warped_right = cached_right_warped_cpu_;
        } else {
            cv::warpPerspective(
                *right_cpu_for_stitch,
                warped_right,
                homography_adjusted_,
                output_size_);
            metrics_.cpu_warp_count += 1;
            cached_right_warped_cpu_ = warped_right;
            cached_right_warped_seq_ = selected_pair.right_seq;
        }
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
    if (!used_dynamic_seam) {
        last_seam_path_jitter_px_ = 0.0;
        metrics_.seam_path_jitter_px = 0.0;
    }
    if (!used_exposure_compensation) {
        last_exposure_gain_ = 1.0;
        last_exposure_bias_ = 0.0;
        metrics_.exposure_gain = 1.0;
        metrics_.exposure_bias = 0.0;
    }
    metrics_.seam_mode = used_dynamic_seam ? "min-cost-seam" : "seam_feather";
    metrics_.blend_mode = used_dynamic_seam ? "narrow-seam-feather" : "seam_feather";
    metrics_.exposure_mode = used_exposure_compensation ? "gain-bias" : "off";
    metrics_.gpu_feature_enabled = used_gpu_blend || used_gpu_geometry || gpu_output_frame_ready;
    if (used_dynamic_seam && (used_gpu_geometry || gpu_output_frame_ready)) {
        metrics_.gpu_feature_reason = "gpu remap with stitched-video postprocess active";
    } else if (used_gpu_blend) {
        metrics_.gpu_feature_reason = "gpu-resident stitch path active";
    } else if (used_gpu_geometry || gpu_output_frame_ready) {
        metrics_.gpu_feature_reason = "gpu-assisted stitch path active";
    } else {
        metrics_.gpu_feature_reason = gpu_only_mode
            ? "gpu-only mode blocked CPU fallback"
            : "cpu stitch fallback active";
    }
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
        const cv::cuda::GpuMat* stitched_gpu_source =
            ((used_gpu_blend || gpu_output_frame_ready) && !gpu_stitched_.empty()) ? &gpu_stitched_ : nullptr;
        const auto output_caps = hogak::output::get_output_runtime_capabilities(output_config.runtime);
        if (gpu_only_mode &&
            (output_config.debug_overlay || output_caps.requires_cpu_input || !output_caps.supports_gpu_input)) {
            *last_error = "gpu-only mode requires a GPU-capable output path without debug overlay";
            metrics_.status = "gpu_only_output_blocked";
            return;
        }
        const bool needs_cpu_prepared_frame =
            output_config.debug_overlay || output_caps.requires_cpu_input || !output_caps.supports_gpu_input;
        const bool prepared_ok = prepare_output_frame_locked(
            output_config,
            stitched,
            stitched_gpu_source,
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
            if (stitched_gpu_source != nullptr) {
                gpu_submit_frame = stitched_gpu_source;
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
    const auto now_arrival_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count();
    const auto now_source_wallclock_ns = wallclock_now_ns();
    apply_runtime_geometry_to_metrics_locked();

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
        left.latest_timestamp_ns > 0 ? static_cast<double>(now_arrival_ns - left.latest_timestamp_ns) / 1'000'000.0 : 0.0;
    metrics_.right_age_ms =
        right.latest_timestamp_ns > 0 ? static_cast<double>(now_arrival_ns - right.latest_timestamp_ns) / 1'000'000.0 : 0.0;
    metrics_.left_source_age_ms = 0.0;
    metrics_.right_source_age_ms = 0.0;
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
    metrics_.source_time_valid_left = left.latest_source_time_valid;
    metrics_.source_time_valid_right = right.latest_source_time_valid;
    const std::string current_sync_time_source = normalize_sync_time_source(config_.sync_time_source);
    const auto preselect_time_domain =
        (config_.sync_pair_mode == "service")
            ? resolve_service_time_domain(config_, left, right, sync_offset_confidence_)
            : hogak::input::FrameTimeDomain::kArrival;
    metrics_.source_time_mode = metrics_source_time_mode_name(preselect_time_domain);
    metrics_.sync_effective_offset_ms = 0.0;
    metrics_.sync_offset_source = "arrival-fallback";
    metrics_.sync_offset_confidence = 0.0;
    metrics_.sync_recalibration_count = sync_recalibration_count_;
    metrics_.sync_estimate_pairs = sync_estimate_pairs_;
    metrics_.sync_estimate_avg_gap_ms = sync_estimate_avg_gap_ms_;
    metrics_.sync_estimate_score = sync_estimate_score_;
    metrics_.distortion_enabled_left = left_distortion_.enabled;
    metrics_.distortion_enabled_right = right_distortion_.enabled;
    metrics_.distortion_source_left = left_distortion_.enabled ? left_distortion_.source : "off";
    metrics_.distortion_source_right = right_distortion_.enabled ? right_distortion_.source : "off";
    metrics_.distortion_confidence_left = left_distortion_.enabled ? left_distortion_.confidence : 0.0;
    metrics_.distortion_confidence_right = right_distortion_.enabled ? right_distortion_.confidence : 0.0;
    metrics_.distortion_model =
        (left_distortion_.enabled && right_distortion_.enabled && left_distortion_.model != right_distortion_.model)
            ? "mixed"
            : (left_distortion_.enabled
                ? left_distortion_.model
                : (right_distortion_.enabled ? right_distortion_.model : "opencv_pinhole"));
    metrics_.distortion_fit_score_left = left_distortion_.enabled ? left_distortion_.fit_score : 0.0;
    metrics_.distortion_fit_score_right = right_distortion_.enabled ? right_distortion_.fit_score : 0.0;
    metrics_.distortion_line_count_left = left_distortion_.enabled ? left_distortion_.line_count : 0;
    metrics_.distortion_line_count_right = right_distortion_.enabled ? right_distortion_.line_count : 0;
    metrics_.distortion_frame_count_left = left_distortion_.enabled ? left_distortion_.frame_count_used : 0;
    metrics_.distortion_frame_count_right = right_distortion_.enabled ? right_distortion_.frame_count_used : 0;
    metrics_.distortion_lens_model_left = left_distortion_.enabled ? left_distortion_.model : "opencv_pinhole";
    metrics_.distortion_lens_model_right = right_distortion_.enabled ? right_distortion_.model : "opencv_pinhole";
    if (preselect_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock) {
        metrics_.sync_offset_source = "wallclock";
        metrics_.sync_offset_confidence = 1.0;
        metrics_.left_source_age_ms =
            left.latest_source_wallclock_ns > 0
                ? static_cast<double>(std::max<std::int64_t>(0, now_source_wallclock_ns - left.latest_source_wallclock_ns)) / 1'000'000.0
                : 0.0;
        metrics_.right_source_age_ms =
            right.latest_source_wallclock_ns > 0
                ? static_cast<double>(std::max<std::int64_t>(0, now_source_wallclock_ns - right.latest_source_wallclock_ns)) / 1'000'000.0
                : 0.0;
    } else if (preselect_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset) {
        if (current_sync_time_source == "pts-offset-manual") {
            metrics_.sync_effective_offset_ms = config_.sync_manual_offset_ms;
            metrics_.sync_offset_source = "manual";
            metrics_.sync_offset_confidence = 1.0;
        } else if (
            current_sync_time_source == "pts-offset-hybrid" &&
            sync_offset_confidence_ < std::max(0.0, config_.sync_auto_offset_confidence_min) &&
            std::abs(config_.sync_manual_offset_ms) > 1e-6) {
            metrics_.sync_effective_offset_ms = config_.sync_manual_offset_ms;
            metrics_.sync_offset_source = "manual";
            metrics_.sync_offset_confidence = 1.0;
        } else {
            metrics_.sync_effective_offset_ms = effective_sync_offset_ms_;
            metrics_.sync_offset_source = sync_offset_source_;
            metrics_.sync_offset_confidence = sync_offset_confidence_;
        }
    }
    const auto preselect_offset_ns =
        static_cast<std::int64_t>(std::llround(metrics_.sync_effective_offset_ms * 1'000'000.0));
    metrics_.pair_source_skew_ms_mean =
        (preselect_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock &&
         left.latest_comparable_source_timestamp_ns > 0 &&
         right.latest_comparable_source_timestamp_ns > 0)
            ? std::abs(static_cast<double>(
                  left.latest_comparable_source_timestamp_ns -
                  right.latest_comparable_source_timestamp_ns)) / 1'000'000.0
            : ((preselect_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset &&
                left.latest_source_pts_ns > 0 &&
                right.latest_source_pts_ns > 0)
                    ? std::abs(static_cast<double>(
                          left.latest_source_pts_ns -
                          (right.latest_source_pts_ns + preselect_offset_ns))) / 1'000'000.0
                    : 0.0);
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
    metrics_.gpu_feature_enabled = gpu_only_mode_enabled(config_);
    metrics_.gpu_feature_reason = gpu_only_mode_enabled(config_)
        ? "gpu-only mode expects GPU-resident decode, stitch, and transmit"
        : "gpu stitch path is used opportunistically";
    metrics_.matches = 0;
    metrics_.inliers = 0;
    metrics_.calibrated = calibrated_;
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
    metrics_.output_active = (output_writer_ != nullptr) && output_writer_->active();
    metrics_.output_frames_written = (output_writer_ != nullptr) ? output_writer_->frames_written() : 0;
    metrics_.output_frames_dropped = (output_writer_ != nullptr) ? output_writer_->frames_dropped() : 0;
    metrics_.output_command_line =
        (output_writer_ != nullptr) ? output_writer_->command_line() : metrics_.output_command_line;
    metrics_.output_effective_codec =
        (output_writer_ != nullptr) ? output_writer_->effective_codec() : metrics_.output_effective_codec;
    metrics_.output_runtime_mode =
        (output_writer_ != nullptr) ? output_writer_->runtime_mode() : metrics_.output_runtime_mode;
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
    metrics_.production_output_runtime_mode =
        (production_output_writer_ != nullptr)
            ? production_output_writer_->runtime_mode()
            : metrics_.production_output_runtime_mode;
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
        const auto delta_ns = now_arrival_ns - last_stitch_timestamp_ns_;
        if (last_stitch_timestamp_ns_ > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics_.stitch_actual_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        last_stitched_count_ = metrics_.stitched_count;
        last_stitch_timestamp_ns_ = now_arrival_ns;
    }
    if (metrics_.output_frames_written < last_output_frames_written_) {
        last_output_frames_written_ = metrics_.output_frames_written;
        last_output_timestamp_ns_ = 0;
    }
    if (metrics_.output_frames_written > last_output_frames_written_) {
        const auto delta_frames = metrics_.output_frames_written - last_output_frames_written_;
        const auto delta_ns = now_arrival_ns - last_output_timestamp_ns_;
        if (last_output_timestamp_ns_ > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics_.output_written_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        last_output_frames_written_ = metrics_.output_frames_written;
        last_output_timestamp_ns_ = now_arrival_ns;
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
        const auto delta_ns = now_arrival_ns - last_production_output_timestamp_ns_;
        if (last_production_output_timestamp_ns_ > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics_.production_output_written_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        last_production_output_frames_written_ = metrics_.production_output_frames_written;
        last_production_output_timestamp_ns_ = now_arrival_ns;
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
    const auto pair_time_domain = pair.pair_time_domain;
    const auto latest_left_pair_time_ns = snapshot_latest_time_ns(left, pair_time_domain);
    const auto latest_right_pair_time_ns = snapshot_latest_time_ns(right, pair_time_domain);
    const auto selected_left_pair_time_ns =
        (pair_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock)
            ? pair.left_source_wallclock_ns
            : (pair_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset)
                ? pair.left_source_pts_ns
            : pair.left_arrival_ts_ns;
    const auto selected_right_pair_time_ns =
        (pair_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock)
            ? pair.right_source_wallclock_ns
            : (pair_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset)
                ? pair.right_source_pts_ns
            : pair.right_arrival_ts_ns;
    const auto current_pair_now_ns =
        (pair_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock)
            ? now_source_wallclock_ns
            : (pair_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset)
                ? std::max(
                    latest_left_pair_time_ns,
                    latest_right_pair_time_ns + pair.effective_offset_ns)
            : now_arrival_ns;
    metrics_.source_time_mode = metrics_source_time_mode_name(pair_time_domain);
    metrics_.pair_source_skew_ms_mean = static_cast<double>(pair.source_skew_ns) / 1'000'000.0;
    metrics_.sync_effective_offset_ms = static_cast<double>(pair.effective_offset_ns) / 1'000'000.0;
    metrics_.sync_offset_source = pair.offset_source;
    metrics_.sync_offset_confidence = pair.offset_confidence;
    metrics_.sync_recalibration_count = sync_recalibration_count_;
    metrics_.sync_estimate_pairs = sync_estimate_pairs_;
    metrics_.sync_estimate_avg_gap_ms = sync_estimate_avg_gap_ms_;
    metrics_.sync_estimate_score = sync_estimate_score_;
    metrics_.selected_left_lag_frames = std::max<std::int64_t>(0, left.latest_seq - pair.left_seq);
    metrics_.selected_right_lag_frames = std::max<std::int64_t>(0, right.latest_seq - pair.right_seq);
    metrics_.selected_left_lag_ms =
        (latest_left_pair_time_ns > 0 && selected_left_pair_time_ns > 0 && latest_left_pair_time_ns >= selected_left_pair_time_ns)
            ? static_cast<double>(latest_left_pair_time_ns - selected_left_pair_time_ns) / 1'000'000.0
            : 0.0;
    metrics_.selected_right_lag_ms =
        (latest_right_pair_time_ns > 0 && selected_right_pair_time_ns > 0 && latest_right_pair_time_ns >= selected_right_pair_time_ns)
            ? static_cast<double>(latest_right_pair_time_ns - selected_right_pair_time_ns) / 1'000'000.0
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
        const double left_age_ms =
            static_cast<double>(std::max<std::int64_t>(0, current_pair_now_ns - selected_left_pair_time_ns)) / 1'000'000.0;
        const double right_age_ms =
            static_cast<double>(std::max<std::int64_t>(0, current_pair_now_ns - selected_right_pair_time_ns)) / 1'000'000.0;
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

    const auto pair_ts_ns = pair.pair_time_ns;
    const auto scheduler_pair_ts_ns = pair.scheduler_pair_time_ns;
    const double pair_age_ms = static_cast<double>(std::max<std::int64_t>(0, current_pair_now_ns - pair_ts_ns)) / 1'000'000.0;
    if (last_worker_timestamp_ns_ > 0 && last_pair_time_domain_ == pair_time_domain) {
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
    last_pair_time_domain_ = pair_time_domain;
    if (config_.stitch_every_n > 1 && (std::max(pair.left_seq, pair.right_seq) % config_.stitch_every_n) != 0) {
        metrics_.reused_count += 1;
        metrics_.status = "skipping per stitch_every_n";
        last_worker_timestamp_ns_ = pair_ts_ns;
        return;
    }

    const double output_scale = clamp_output_scale(config_.stitch_output_scale);
    const bool gpu_only_mode = gpu_only_mode_enabled(config_);
    const bool use_gpu_nv12_fast_path =
        gpu_available_ &&
        gpu_nv12_input_supported_ &&
        input_pipe_format_is_nv12(config_.left) &&
        input_pipe_format_is_nv12(config_.right);
    if (gpu_only_mode && !use_gpu_nv12_fast_path) {
        metrics_.status = "gpu_only_input_unavailable";
        metrics_.gpu_reason = "gpu-only mode requires NV12 fast-path input without CPU staging";
        return;
    }
    cv::Mat left_frame;
    cv::Mat right_frame;
    const cv::Mat* left_raw_input = nullptr;
    const cv::Mat* right_raw_input = nullptr;
    if (use_gpu_nv12_fast_path) {
        left_raw_input = &pair.left_frame;
        right_raw_input = &pair.right_frame;
    } else {
        if (pair.left_seq > 0 && cached_left_cpu_seq_ == pair.left_seq && !cached_left_cpu_frame_.empty()) {
            left_frame = cached_left_cpu_frame_;
        } else {
            cv::Mat left_decoded = decode_input_frame_for_stitch(pair.left_frame, config_.left);
            if (left_decoded.empty()) {
                metrics_.status = "input decode failed";
                return;
            }
            left_frame = resize_frame_for_runtime(left_decoded, output_scale);
            cached_left_cpu_frame_ = left_frame;
            cached_left_cpu_seq_ = pair.left_seq;
        }
        if (pair.right_seq > 0 && cached_right_cpu_seq_ == pair.right_seq && !cached_right_cpu_frame_.empty()) {
            right_frame = cached_right_cpu_frame_;
        } else {
            cv::Mat right_decoded = decode_input_frame_for_stitch(pair.right_frame, config_.right);
            if (right_decoded.empty()) {
                metrics_.status = "input decode failed";
                return;
            }
            right_frame = resize_frame_for_runtime(right_decoded, output_scale);
            cached_right_cpu_frame_ = right_frame;
            cached_right_cpu_seq_ = pair.right_seq;
        }
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
