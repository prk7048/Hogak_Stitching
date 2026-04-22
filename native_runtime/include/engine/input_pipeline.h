#pragma once

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>

#include "engine/engine_config.h"

namespace hogak::engine {

bool input_pipe_format_is_nv12(const StreamConfig& config);
bool is_nv12_gpu_conversion_unsupported(const cv::Exception& error);
cv::Size input_frame_size_for_runtime(const cv::Mat& frame, const StreamConfig& config, double scale);
double input_frame_mean_luma(const cv::Mat& frame, const StreamConfig& config);
cv::Mat decode_input_frame_for_stitch(const cv::Mat& frame, const StreamConfig& config);
bool upload_input_frame_for_gpu_stitch(
    const cv::Mat& raw_input,
    const StreamConfig& config,
    double output_scale,
    const cv::Mat* cpu_fallback_bgr,
    cv::cuda::GpuMat* nv12_y_gpu,
    cv::cuda::GpuMat* nv12_uv_gpu,
    cv::cuda::GpuMat* decoded_bgr_gpu,
    cv::cuda::GpuMat* final_bgr_gpu);
cv::Mat resize_frame_for_runtime(const cv::Mat& frame, double scale);

}  // namespace hogak::engine
