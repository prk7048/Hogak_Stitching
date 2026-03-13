#pragma once

#include <cstdint>
#include <string>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"

namespace hogak::output {

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
    virtual void submit(const cv::Mat& frame, std::int64_t timestamp_ns) = 0;
    virtual void stop() = 0;

    virtual bool active() const noexcept = 0;
    virtual std::int64_t frames_written() const noexcept = 0;
    virtual std::int64_t frames_dropped() const noexcept = 0;
    virtual std::string last_error() const = 0;
    virtual std::string effective_codec() const = 0;
    virtual std::string command_line() const = 0;
    virtual std::string muxer() const = 0;
};

}  // namespace hogak::output
