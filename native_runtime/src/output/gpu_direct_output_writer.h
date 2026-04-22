#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>

#include "output/output_writer.h"

namespace hogak::output {

class GpuDirectOutputWriter final : public OutputWriter {
public:
    GpuDirectOutputWriter();
    ~GpuDirectOutputWriter() override;

    GpuDirectOutputWriter(const GpuDirectOutputWriter&) = delete;
    GpuDirectOutputWriter& operator=(const GpuDirectOutputWriter&) = delete;

    bool start(
        const hogak::engine::OutputConfig& config,
        const std::string& ffmpeg_bin,
        int width,
        int height,
        double fps,
        bool input_prepared = false) override;
    OutputSubmitResult submit(const OutputFrame& frame, std::int64_t timestamp_ns) override;
    void stop() override;

    bool active() const noexcept override;
    std::int64_t frames_written() const noexcept override;
    std::int64_t frames_dropped() const noexcept override;
    std::int64_t pending_frames() const noexcept override;
    std::int64_t max_pending_frames() const noexcept override;
    std::string drop_policy() const override;
    std::string last_error() const override;
    std::string effective_codec() const override;
    std::string command_line() const override;
    std::string runtime_mode() const override;
    std::string muxer() const override;

private:
    struct Impl;
    struct PendingFrame {
        cv::Mat cpu_frame{};
        cv::cuda::GpuMat gpu_frame{};
        bool frame_on_gpu = false;
    };

    void run();

    mutable std::mutex mutex_;
    std::condition_variable condition_{};
    std::thread thread_{};
    std::atomic<bool> active_{false};
    hogak::engine::OutputConfig config_{};
    std::string effective_codec_{};
    std::string muxer_{};
    std::string output_target_{};
    std::string last_error_{"gpu-direct output writer not started"};
    std::string command_line_{"gpu-direct://pending-backend"};
    std::string runtime_mode_{"native-nvenc-bridge"};
    int width_ = 0;
    int height_ = 0;
    double fps_ = 0.0;
    std::deque<PendingFrame> pending_frames_{};
    std::int64_t frames_written_ = 0;
    std::int64_t frames_dropped_ = 0;
    std::unique_ptr<Impl> impl_{};
};

}  // namespace hogak::output
