#include "output/gpu_direct_output_writer.h"

namespace hogak::output {

bool GpuDirectOutputWriter::start(
    const hogak::engine::OutputConfig& config,
    const std::string& /*ffmpeg_bin*/,
    int width,
    int height,
    double fps,
    bool /*input_prepared*/) {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    width_ = width;
    height_ = height;
    fps_ = fps;
    active_.store(false);
    last_error_ =
        "gpu-direct output writer placeholder selected but backend is not implemented yet"
        " target=" + config.target +
        " codec=" + config.codec;
    return false;
}

void GpuDirectOutputWriter::submit(const OutputFrame& /*frame*/, std::int64_t /*timestamp_ns*/) {
}

void GpuDirectOutputWriter::stop() {
    active_.store(false);
}

bool GpuDirectOutputWriter::active() const noexcept {
    return active_.load();
}

std::int64_t GpuDirectOutputWriter::frames_written() const noexcept {
    return 0;
}

std::int64_t GpuDirectOutputWriter::frames_dropped() const noexcept {
    return 0;
}

std::string GpuDirectOutputWriter::last_error() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return last_error_;
}

std::string GpuDirectOutputWriter::effective_codec() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return config_.codec;
}

std::string GpuDirectOutputWriter::command_line() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return command_line_;
}

std::string GpuDirectOutputWriter::muxer() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return config_.muxer;
}

}  // namespace hogak::output
