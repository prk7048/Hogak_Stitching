#include "engine/engine.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudaarithm.hpp>
#include <opencv2/cudawarping.hpp>
#include <opencv2/imgproc.hpp>

#include "engine/input_pipeline.h"
#include "engine/metrics_pipeline.h"
#include "engine/output_pipeline.h"
#include "output/output_writer_factory.h"

namespace hogak::engine {

namespace {

constexpr int kMotionProbeWidth = 64;
constexpr int kMotionProbeHeight = 36;
constexpr double kReaderRestartAgeMs = 1500.0;
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

std::int64_t wallclock_now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

}  // namespace

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
    cv::Mat right_aligned_cpu;
    bool used_gpu_blend = false;
    bool used_gpu_geometry = false;
    bool gpu_output_frame_ready = false;
    bool used_dynamic_seam = false;
    bool used_exposure_compensation = false;
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
            const bool left_nv12_input_available =
                left_raw_input != nullptr &&
                input_pipe_format_is_nv12(config_.left);
            const bool right_nv12_input_available =
                right_raw_input != nullptr &&
                input_pipe_format_is_nv12(config_.right);
            const bool left_gpu_fast =
                gpu_nv12_input_supported_ &&
                left_nv12_input_available;
            const bool right_gpu_fast =
                gpu_nv12_input_supported_ &&
                right_nv12_input_available;
            if (rectilinear_runtime && (!left_nv12_input_available || !right_nv12_input_available)) {
                throw cv::Exception(
                    cv::Error::StsError,
                    "virtual-center-rectilinear requires NV12 input on both cameras",
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
            auto upload_cpu_frame = [&](const cv::Mat* cpu_frame,
                                        cv::cuda::GpuMat* gpu_frame,
                                        const char* label,
                                        bool allow_cpu_staging) {
                if ((gpu_only_mode || rectilinear_runtime) && !allow_cpu_staging) {
                    throw cv::Exception(
                        cv::Error::StsError,
                        std::string(label) + " input would require CPU upload in a GPU-only stitch path",
                        __FUNCTION__,
                        __FILE__,
                        __LINE__);
                }
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
                    if (gpu_only_mode || rectilinear_runtime) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            std::string(label) + " input would require CPU staging in a GPU-only stitch path",
                            __FUNCTION__,
                            __FILE__,
                            __LINE__);
                    }
                    if (!ensure_cpu_frame(cpu_frame, raw_input, stream_config, input_seq, cached_frame, cached_seq)) {
                        throw cv::Exception(cv::Error::StsError, std::string(label) + " input frame unavailable", __FUNCTION__, __FILE__, __LINE__);
                    }
                    upload_cpu_frame(cpu_frame, final_bgr_gpu, label, true);
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
                    if (!ensure_cpu_frame(cpu_frame, raw_input, stream_config, input_seq, cached_frame, cached_seq)) {
                        throw;
                    }
                    upload_cpu_frame(cpu_frame, final_bgr_gpu, label, true);
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
                    upload_cpu_frame(&left_cpu_frame, &gpu_left_input_, "left", true);
                }
                cached_left_gpu_input_seq_ = selected_pair.left_seq;
            }
            if (cached_left_canvas_seq_ != selected_pair.left_seq || gpu_left_canvas_.empty()) {
                const cv::cuda::GpuMat* left_gpu_for_stitch = &gpu_left_input_;
                if (rectilinear_runtime) {
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
                if (left_gpu_for_stitch->size() != output_size_) {
                    throw cv::Exception(
                        cv::Error::StsError,
                        "left virtual-center projection size does not match output canvas",
                        __FUNCTION__,
                        __FILE__,
                        __LINE__);
                }
                left_gpu_for_stitch->copyTo(gpu_left_canvas_);
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
                    upload_cpu_frame(&right_cpu_frame, &gpu_right_input_, "right", true);
                }
                cached_right_gpu_input_seq_ = selected_pair.right_seq;
            }
            if (cached_right_warped_seq_ != selected_pair.right_seq || gpu_right_warped_.empty()) {
                const cv::cuda::GpuMat* right_gpu_for_stitch = &gpu_right_input_;
                if (rectilinear_runtime) {
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
                cv::cuda::warpPerspective(
                    *right_gpu_for_stitch,
                    gpu_right_aligned_,
                    homography_adjusted_,
                    output_size_);
                if (runtime_geometry_.mesh_enabled) {
                    if (runtime_geometry_.mesh_map_x_gpu.empty() ||
                        runtime_geometry_.mesh_map_y_gpu.empty()) {
                        throw cv::Exception(
                            cv::Error::StsError,
                            "right rectilinear mesh gpu map unavailable",
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
                } else {
                    gpu_right_aligned_.copyTo(gpu_right_warped_);
                }
                metrics_.gpu_warp_count += 1;
                cached_right_warped_seq_ = selected_pair.right_seq;
            }
            used_gpu_geometry = true;
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

            if (rectilinear_runtime) {
                cv::Mat valid_mask;
                cv::bitwise_or(left_mask_template_, right_mask_template_, valid_mask);
                const cv::Rect crop_rect = resolve_runtime_crop_rect_locked(valid_mask);
                const bool crop_valid =
                    crop_rect.width > 0 &&
                    crop_rect.height > 0 &&
                    crop_rect.x >= 0 &&
                    crop_rect.y >= 0 &&
                    crop_rect.x + crop_rect.width <= gpu_stitched_.cols &&
                    crop_rect.y + crop_rect.height <= gpu_stitched_.rows;
                if (crop_valid &&
                    (crop_rect.width != gpu_stitched_.cols || crop_rect.height != gpu_stitched_.rows)) {
                    cv::cuda::GpuMat cropped_view(gpu_stitched_, crop_rect);
                    cv::cuda::GpuMat cropped_copy;
                    cropped_view.copyTo(cropped_copy);
                    gpu_stitched_ = cropped_copy;
                }
            }

            if (need_cpu_stitched) {
                gpu_stitched_.download(stitched);
            }
            metrics_.gpu_blend_count += 1;
            metrics_.overlap_diff_mean = 0.0;
            used_gpu_blend = true;
        } catch (const cv::Exception& e) {
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = std::string("virtual-center-rectilinear gpu stitch failed: ") + e.what();
            metrics_.status = "virtual_center_rectilinear_gpu_stitch_failed";
            metrics_.stitch_fps = 0.0;
            return false;
        }
    }

    if (!used_gpu_blend && stitched.empty()) {
        metrics_.status = rectilinear_runtime
            ? "virtual_center_rectilinear_cpu_fallback_blocked"
            : "gpu_only_path_unavailable";
        metrics_.stitch_fps = 0.0;
        metrics_.gpu_feature_enabled = false;
        metrics_.gpu_feature_reason = rectilinear_runtime
            ? "virtual-center-rectilinear requires a fully GPU-resident stitch path"
            : "gpu-only mode could not keep the stitch path on GPU";
        return false;
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
    if (rectilinear_runtime) {
        metrics_.seam_mode = "fixed-seam";
        metrics_.blend_mode = "simple-feather";
        metrics_.exposure_mode = "off";
    } else {
        metrics_.seam_mode = used_dynamic_seam ? "min-cost-seam" : "seam_feather";
        metrics_.blend_mode = used_dynamic_seam ? "narrow-seam-feather" : "seam_feather";
        metrics_.exposure_mode = used_exposure_compensation ? "gain-bias" : "off";
    }
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

    const cv::cuda::GpuMat* stitched_gpu_source =
        ((used_gpu_blend || gpu_output_frame_ready) && !gpu_stitched_.empty()) ? &gpu_stitched_ : nullptr;
    OutputPrepareScratch output_scratch{
        &gpu_output_scaled_,
        &gpu_output_canvas_,
    };
    const double fallback_output_fps = std::max({metrics_.worker_fps, metrics_.left_fps, metrics_.right_fps, 30.0});
    const auto launch_output_writer = [&](const char* overlay_label,
                                          const OutputConfig& output_config,
                                          std::unique_ptr<hogak::output::OutputWriter>* writer,
                                          std::string* last_error,
                                          std::string* target,
                                          std::string* effective_codec) {
        if (writer == nullptr || last_error == nullptr || target == nullptr || effective_codec == nullptr) {
            return;
        }
        OutputSubmitRequest request;
        request.overlay_label = overlay_label;
        request.output_config = &output_config;
        request.ffmpeg_bin = &config_.ffmpeg_bin;
        request.stitched_cpu = &stitched;
        request.stitched_gpu = stitched_gpu_source;
        request.timestamp_ns = pair_ts_ns;
        request.gpu_only_mode = gpu_only_mode;
        request.fallback_output_fps = fallback_output_fps;
        request.overlay.frame_index = metrics_.frame_index;
        request.overlay.status = metrics_.status;
        request.overlay.left_seq = selected_pair.left_seq;
        request.overlay.right_seq = selected_pair.right_seq;
        request.overlay.left_reused = left_reused;
        request.overlay.right_reused = right_reused;
        request.overlay.pair_age_ms = pair_age_ms;
        request.overlay.pair_skew_ms = metrics_.pair_skew_ms_mean;
        request.overlay.left_age_ms = metrics_.left_age_ms;
        request.overlay.right_age_ms = metrics_.right_age_ms;

        OutputSubmitResult result;
        submit_output_frame(request, &output_scratch, writer, &result);
        if (result.gpu_prepare_failed) {
            gpu_available_ = false;
            metrics_.gpu_errors += 1;
            metrics_.gpu_reason = result.gpu_prepare_error;
        }
        if (result.gpu_only_output_blocked) {
            metrics_.status = "gpu_only_output_blocked";
        }
        if (!result.last_error.empty()) {
            *last_error = result.last_error;
        }
        if (!result.target.empty()) {
            *target = result.target;
        }
        if (!result.effective_codec.empty()) {
            *effective_codec = result.effective_codec;
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

void StitchEngine::refresh_output_metrics_locked(std::int64_t now_arrival_ns) {
    refresh_output_metrics(
        output_writer_,
        production_output_writer_,
        now_arrival_ns,
        &metrics_,
        &last_stitched_count_,
        &last_stitch_timestamp_ns_,
        &last_output_frames_written_,
        &last_output_timestamp_ns_,
        &last_production_output_frames_written_,
        &last_production_output_timestamp_ns_);
}

void StitchEngine::maybe_restart_stale_readers_locked() {
    if (metrics_.left_age_ms > kReaderRestartAgeMs) {
        restart_reader_locked(true, "input age exceeded threshold");
    }
    if (metrics_.right_age_ms > kReaderRestartAgeMs) {
        restart_reader_locked(false, "input age exceeded threshold");
    }
}

void StitchEngine::update_metrics_locked() {
    const auto left = input_state_.left_reader.snapshot();
    const auto right = input_state_.right_reader.snapshot();
    const auto now_arrival_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count();
    const auto now_source_wallclock_ns = wallclock_now_ns();
    const bool refresh_metrics_snapshot =
        last_metrics_refresh_ns_ <= 0 ||
        (now_arrival_ns - last_metrics_refresh_ns_) >= kMetricsSnapshotIntervalNs;
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
            ? resolve_service_time_domain(config_, left, right, pair_scheduler_state_.sync_offset_confidence)
            : hogak::input::FrameTimeDomain::kArrival;
    metrics_.source_time_mode = metrics_source_time_mode_name(preselect_time_domain);
    metrics_.sync_effective_offset_ms = 0.0;
    metrics_.sync_offset_source = "arrival-fallback";
    metrics_.sync_offset_confidence = 0.0;
    metrics_.sync_recalibration_count = pair_scheduler_state_.sync_recalibration_count;
    metrics_.sync_estimate_pairs = pair_scheduler_state_.sync_estimate_pairs;
    metrics_.sync_estimate_avg_gap_ms = pair_scheduler_state_.sync_estimate_avg_gap_ms;
    metrics_.sync_estimate_score = pair_scheduler_state_.sync_estimate_score;
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
            pair_scheduler_state_.sync_offset_confidence < std::max(0.0, config_.sync_auto_offset_confidence_min) &&
            std::abs(config_.sync_manual_offset_ms) > 1e-6) {
            metrics_.sync_effective_offset_ms = config_.sync_manual_offset_ms;
            metrics_.sync_offset_source = "manual";
            metrics_.sync_offset_confidence = 1.0;
        } else {
            metrics_.sync_effective_offset_ms = pair_scheduler_state_.effective_sync_offset_ms;
            metrics_.sync_offset_source = pair_scheduler_state_.sync_offset_source;
            metrics_.sync_offset_confidence = pair_scheduler_state_.sync_offset_confidence;
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
    if (refresh_metrics_snapshot) {
        refresh_output_metrics_locked(now_arrival_ns);
        last_metrics_refresh_ns_ = now_arrival_ns;
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

    maybe_restart_stale_readers_locked();

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

    const bool has_new_left = pair.left_seq > pair_scheduler_state_.last_left_seq;
    const bool has_new_right = pair.right_seq > pair_scheduler_state_.last_right_seq;
    const auto pair_time_domain = pair.pair_time_domain;
    const auto latest_left_pair_time_ns = snapshot_latest_time_ns(left, pair_time_domain);
    const auto latest_right_pair_time_ns = snapshot_latest_time_ns(right, pair_time_domain);
    const auto selected_left_pair_time_ns = selected_pair_left_time_ns(pair);
    const auto selected_right_pair_time_ns = selected_pair_right_time_ns(pair);
    const auto current_pair_now_ns =
        selected_pair_now_ns(pair, left, right, now_arrival_ns, now_source_wallclock_ns);
    metrics_.source_time_mode = metrics_source_time_mode_name(pair_time_domain);
    metrics_.pair_source_skew_ms_mean = static_cast<double>(pair.source_skew_ns) / 1'000'000.0;
    metrics_.sync_effective_offset_ms = static_cast<double>(pair.effective_offset_ns) / 1'000'000.0;
    metrics_.sync_offset_source = pair.offset_source;
    metrics_.sync_offset_confidence = pair.offset_confidence;
    metrics_.sync_recalibration_count = pair_scheduler_state_.sync_recalibration_count;
    metrics_.sync_estimate_pairs = pair_scheduler_state_.sync_estimate_pairs;
    metrics_.sync_estimate_avg_gap_ms = pair_scheduler_state_.sync_estimate_avg_gap_ms;
    metrics_.sync_estimate_score = pair_scheduler_state_.sync_estimate_score;
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
            pair_scheduler_state_.consecutive_left_reuse < max_consecutive_reuse;
        const bool can_reuse_right =
            right_reused &&
            right_age_ms <= max_reuse_age_ms &&
            pair_scheduler_state_.consecutive_right_reuse < max_consecutive_reuse;
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
    if (last_worker_timestamp_ns_ > 0 && pair_scheduler_state_.last_pair_time_domain == pair_time_domain) {
        metrics_.worker_fps = fps_from_period_ns(pair_ts_ns - last_worker_timestamp_ns_);
    } else {
        metrics_.worker_fps = 0.0;
    }

    if (has_new_left) {
        pair_scheduler_state_.last_left_seq = pair.left_seq;
        pair_scheduler_state_.consecutive_left_reuse = 0;
    } else {
        pair_scheduler_state_.consecutive_left_reuse += 1;
    }
    if (has_new_right) {
        pair_scheduler_state_.last_right_seq = pair.right_seq;
        pair_scheduler_state_.consecutive_right_reuse = 0;
    } else {
        pair_scheduler_state_.consecutive_right_reuse += 1;
    }
    if (config_.sync_pair_mode == "service") {
        pair_scheduler_state_.last_service_pair_ts_ns = scheduler_pair_ts_ns;
    }
    pair_scheduler_state_.last_pair_time_domain = pair_time_domain;
    if (config_.stitch_every_n > 1 && (std::max(pair.left_seq, pair.right_seq) % config_.stitch_every_n) != 0) {
        metrics_.reused_count += 1;
        metrics_.status = "skipping per stitch_every_n";
        last_worker_timestamp_ns_ = pair_ts_ns;
        return;
    }

    const double output_scale = clamp_output_scale(config_.stitch_output_scale);
    PreparedExecutionInput prepared_input;
    if (!prepare_execution_inputs_locked(pair, output_scale, &prepared_input)) {
        return;
    }
    const bool stitched_ok = stitch_pair_locked(
        prepared_input.left_frame,
        prepared_input.right_frame,
        pair_ts_ns,
        pair,
        !has_new_left,
        !has_new_right,
        pair_age_ms,
        prepared_input.left_raw_input,
        prepared_input.right_raw_input,
        output_scale);
    last_worker_timestamp_ns_ = pair_ts_ns;

    if (!stitched_ok) {
        return;
    }
}

}  // namespace hogak::engine
