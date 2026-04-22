#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>

#include "engine/engine_config.h"

namespace hogak::output {
class OutputWriter;
}

namespace hogak::engine {

struct OutputOverlayContext {
    std::int64_t frame_index = 0;
    std::string status;
    std::int64_t left_seq = 0;
    std::int64_t right_seq = 0;
    bool left_reused = false;
    bool right_reused = false;
    double pair_age_ms = 0.0;
    double pair_skew_ms = 0.0;
    double left_age_ms = 0.0;
    double right_age_ms = 0.0;
};

struct OutputPrepareScratch {
    cv::cuda::GpuMat* gpu_output_scaled = nullptr;
    cv::cuda::GpuMat* gpu_output_canvas = nullptr;
};

struct OutputSubmitRequest {
    const char* overlay_label = nullptr;
    const OutputConfig* output_config = nullptr;
    const std::string* ffmpeg_bin = nullptr;
    const cv::Mat* stitched_cpu = nullptr;
    const cv::cuda::GpuMat* stitched_gpu = nullptr;
    std::int64_t timestamp_ns = 0;
    bool gpu_only_mode = false;
    double fallback_output_fps = 0.0;
    OutputOverlayContext overlay{};
};

struct OutputSubmitResult {
    bool gpu_prepare_failed = false;
    std::string gpu_prepare_error;
    bool gpu_only_output_blocked = false;
    std::string last_error;
    std::string target;
    std::string effective_codec;
};

void submit_output_frame(
    const OutputSubmitRequest& request,
    OutputPrepareScratch* scratch,
    std::unique_ptr<hogak::output::OutputWriter>* writer,
    OutputSubmitResult* result);

}  // namespace hogak::engine
