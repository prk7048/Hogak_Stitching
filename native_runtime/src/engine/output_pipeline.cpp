#include "engine/output_pipeline.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudawarping.hpp>
#include <opencv2/imgproc.hpp>

#include "output/output_writer.h"
#include "output/output_writer_factory.h"

namespace hogak::engine {

namespace {

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

bool prepare_output_frame(
    const OutputConfig& output_config,
    const cv::Mat& stitched_cpu,
    const cv::cuda::GpuMat* stitched_gpu,
    OutputPrepareScratch* scratch,
    cv::Mat* prepared_frame_out,
    const cv::cuda::GpuMat** prepared_gpu_frame_out,
    bool* gpu_prepare_failed_out,
    std::string* gpu_prepare_error_out) {
    if (prepared_frame_out != nullptr) {
        prepared_frame_out->release();
    }
    if (prepared_gpu_frame_out != nullptr) {
        *prepared_gpu_frame_out = nullptr;
    }
    if (gpu_prepare_failed_out != nullptr) {
        *gpu_prepare_failed_out = false;
    }
    if (gpu_prepare_error_out != nullptr) {
        gpu_prepare_error_out->clear();
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
    const bool can_try_gpu_prepare =
        has_gpu_source &&
        scratch != nullptr &&
        scratch->gpu_output_scaled != nullptr &&
        scratch->gpu_output_canvas != nullptr;
    if (can_try_gpu_prepare) {
        try {
            const cv::cuda::GpuMat* scaled_source = stitched_gpu;
            if (scaled_size != source_size) {
                cv::cuda::resize(*stitched_gpu, *scratch->gpu_output_scaled, scaled_size, 0.0, 0.0, cv::INTER_LINEAR);
                scaled_source = scratch->gpu_output_scaled;
            }

            scratch->gpu_output_canvas->create(target_size, CV_8UC3);
            scratch->gpu_output_canvas->setTo(cv::Scalar::all(0));
            const cv::Rect roi(
                std::max(0, (target_size.width - scaled_size.width) / 2),
                std::max(0, (target_size.height - scaled_size.height) / 2),
                scaled_size.width,
                scaled_size.height);
            cv::cuda::GpuMat target_roi(*scratch->gpu_output_canvas, roi);
            scaled_source->copyTo(target_roi);
            if (prepared_gpu_frame_out != nullptr) {
                *prepared_gpu_frame_out = scratch->gpu_output_canvas;
            }
            if (prepared_frame_out != nullptr) {
                scratch->gpu_output_canvas->download(*prepared_frame_out);
            }
            return true;
        } catch (const cv::Exception& e) {
            if (gpu_prepare_failed_out != nullptr) {
                *gpu_prepare_failed_out = true;
            }
            if (gpu_prepare_error_out != nullptr) {
                *gpu_prepare_error_out = std::string("cuda output prep failed: ") + e.what();
            }
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

void annotate_output_debug_overlay(
    cv::Mat* frame,
    const char* label,
    const OutputOverlayContext& context) {
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
        std::string(context.left_reused ? "L" : "-") + std::string(context.right_reused ? "R" : "-");
    const std::vector<std::string> lines = {
        std::string(label != nullptr ? label : "OUT") + " frame=" + std::to_string(context.frame_index) +
            " status=" + context.status,
        "seq L=" + std::to_string(context.left_seq) +
            " R=" + std::to_string(context.right_seq) +
            " reuse=" + reuse_text +
            " pair_age=" + std::to_string(static_cast<int>(std::llround(context.pair_age_ms))) + "ms" +
            " skew=" + std::to_string(static_cast<int>(std::llround(context.pair_skew_ms))) + "ms",
        "input_age L=" + std::to_string(static_cast<int>(std::llround(context.left_age_ms))) +
            "ms R=" + std::to_string(static_cast<int>(std::llround(context.right_age_ms))) +
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

}  // namespace

void submit_output_frame(
    const OutputSubmitRequest& request,
    OutputPrepareScratch* scratch,
    std::unique_ptr<hogak::output::OutputWriter>* writer,
    OutputSubmitResult* result) {
    if (result == nullptr) {
        return;
    }
    *result = OutputSubmitResult{};
    if (writer == nullptr || request.output_config == nullptr || request.ffmpeg_bin == nullptr) {
        return;
    }

    const auto& output_config = *request.output_config;
    if (output_config.runtime == "none" || output_config.target.empty()) {
        if (*writer != nullptr) {
            (*writer)->stop();
            writer->reset();
        }
        return;
    }

    const auto output_caps = hogak::output::get_output_runtime_capabilities(output_config.runtime);
    if (request.gpu_only_mode &&
        (output_config.debug_overlay || output_caps.requires_cpu_input || !output_caps.supports_gpu_input)) {
        result->gpu_only_output_blocked = true;
        result->last_error = "gpu-only mode requires a GPU-capable output path without debug overlay";
        return;
    }

    cv::Mat prepared_frame;
    const cv::cuda::GpuMat* prepared_gpu_frame = nullptr;
    const cv::Mat stitched_cpu = (request.stitched_cpu != nullptr) ? *request.stitched_cpu : cv::Mat{};
    const cv::cuda::GpuMat* stitched_gpu_source =
        (request.stitched_gpu != nullptr && !request.stitched_gpu->empty()) ? request.stitched_gpu : nullptr;
    const bool needs_cpu_prepared_frame =
        output_config.debug_overlay || output_caps.requires_cpu_input || !output_caps.supports_gpu_input;
    const bool prepared_ok = prepare_output_frame(
        output_config,
        stitched_cpu,
        stitched_gpu_source,
        scratch,
        needs_cpu_prepared_frame ? &prepared_frame : nullptr,
        &prepared_gpu_frame,
        &result->gpu_prepare_failed,
        &result->gpu_prepare_error);

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
        if (request.stitched_cpu != nullptr) {
            cpu_submit_frame = request.stitched_cpu;
        }
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
                result->last_error = std::string("debug overlay gpu download failed: ") + e.what();
                return;
            }
        }
        if (annotated_frame.empty()) {
            result->last_error = "debug overlay frame unavailable";
            return;
        }
        annotate_output_debug_overlay(&annotated_frame, request.overlay_label, request.overlay);
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
        result->last_error = "output submit frame unavailable";
        return;
    }

    const int stitched_width = (request.stitched_cpu != nullptr) ? request.stitched_cpu->cols : 0;
    const int stitched_height = (request.stitched_cpu != nullptr) ? request.stitched_cpu->rows : 0;
    submit_frame.input_prepared =
        (submit_frame.width() != stitched_width) || (submit_frame.height() != stitched_height);

    if (*writer == nullptr) {
        *writer = hogak::output::create_output_writer(output_config.runtime);
        if (*writer == nullptr) {
            result->last_error = "unsupported output runtime: " + output_config.runtime;
            return;
        }
        const double requested_output_fps = (output_config.fps > 0.0) ? output_config.fps : 0.0;
        const double output_fps = (requested_output_fps > 0.0)
            ? requested_output_fps
            : std::max(request.fallback_output_fps, 30.0);
        if (!(*writer)->start(
                output_config,
                *request.ffmpeg_bin,
                submit_frame.width(),
                submit_frame.height(),
                output_fps,
                submit_frame.input_prepared)) {
            result->last_error = (*writer)->last_error();
            if (result->last_error.empty()) {
                result->last_error = "failed to start output writer";
            }
            writer->reset();
            return;
        }
        result->target = output_config.target;
        result->effective_codec = (*writer)->effective_codec();
    }

    if (*writer != nullptr) {
        const auto submit_result = (*writer)->submit(submit_frame, request.timestamp_ns);
        if (submit_result == hogak::output::OutputSubmitResult::kRejected) {
            result->last_error = (*writer)->last_error();
            if (result->last_error.empty()) {
                result->last_error = "output writer rejected the submitted frame";
            }
        }
    }
}

}  // namespace hogak::engine
