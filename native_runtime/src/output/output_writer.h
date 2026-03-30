#pragma once

#include <cstdint>
#include <string>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>

#include "engine/engine_config.h"

namespace hogak::output {

struct OutputFrame {
    const cv::Mat* cpu_frame = nullptr;
    const cv::cuda::GpuMat* gpu_frame = nullptr;
    bool input_prepared = false;

    bool empty() const noexcept {
        const bool has_cpu = cpu_frame != nullptr && !cpu_frame->empty();
        const bool has_gpu = gpu_frame != nullptr && !gpu_frame->empty();
        return !has_cpu && !has_gpu;
    }

    int width() const noexcept {
        if (cpu_frame != nullptr && !cpu_frame->empty()) {
            return cpu_frame->cols;
        }
        if (gpu_frame != nullptr && !gpu_frame->empty()) {
            return gpu_frame->cols;
        }
        return 0;
    }

    int height() const noexcept {
        if (cpu_frame != nullptr && !cpu_frame->empty()) {
            return cpu_frame->rows;
        }
        if (gpu_frame != nullptr && !gpu_frame->empty()) {
            return gpu_frame->rows;
        }
        return 0;
    }
};

class OutputWriter {
public:
    virtual ~OutputWriter() = default;

    virtual bool start(
        const hogak::engine::OutputConfig& config,
        const std::string& ffmpeg_bin,
        int width,
        int height,
        double fps,
        bool input_prepared = false) = 0;
    virtual void submit(const OutputFrame& frame, std::int64_t timestamp_ns) = 0;
    virtual void stop() = 0;

    virtual bool active() const noexcept = 0;
    virtual std::int64_t frames_written() const noexcept = 0;
    virtual std::int64_t frames_dropped() const noexcept = 0;
    virtual std::string last_error() const = 0;
    virtual std::string effective_codec() const = 0;
    virtual std::string command_line() const = 0;
    virtual std::string runtime_mode() const = 0;
    virtual std::string muxer() const = 0;
};

}  // namespace hogak::output
