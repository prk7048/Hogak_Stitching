#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>

#include "output/output_writer.h"

namespace hogak::output {

class GpuDirectOutputWriter final : public OutputWriter {
public:
    bool start(
        const hogak::engine::OutputConfig& config,
        const std::string& ffmpeg_bin,
        int width,
        int height,
        double fps,
        bool input_prepared = false) override;
    void submit(const OutputFrame& frame, std::int64_t timestamp_ns) override;
    void stop() override;

    bool active() const noexcept override;
    std::int64_t frames_written() const noexcept override;
    std::int64_t frames_dropped() const noexcept override;
    std::string last_error() const override;
    std::string effective_codec() const override;
    std::string command_line() const override;
    std::string muxer() const override;

private:
    mutable std::mutex mutex_;
    std::atomic<bool> active_{false};
    hogak::engine::OutputConfig config_{};
    std::string last_error_{"gpu-direct output writer not implemented yet"};
    std::string command_line_{"gpu-direct://not-implemented"};
    int width_ = 0;
    int height_ = 0;
    double fps_ = 0.0;
};

}  // namespace hogak::output
