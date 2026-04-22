#pragma once

#include <cstdint>
#include <memory>

#include "engine/engine_metrics.h"
#include "input/ffmpeg_rtsp_reader.h"

namespace hogak::output {
class OutputWriter;
}

namespace hogak::engine {

const char* metrics_source_time_mode_name(hogak::input::FrameTimeDomain time_domain) noexcept;

void refresh_output_metrics(
    const std::unique_ptr<hogak::output::OutputWriter>& output_writer,
    const std::unique_ptr<hogak::output::OutputWriter>& production_output_writer,
    std::int64_t now_arrival_ns,
    EngineMetrics* metrics,
    std::int64_t* last_stitched_count,
    std::int64_t* last_stitch_timestamp_ns,
    std::int64_t* last_output_frames_written,
    std::int64_t* last_output_timestamp_ns,
    std::int64_t* last_production_output_frames_written,
    std::int64_t* last_production_output_timestamp_ns);

}  // namespace hogak::engine
