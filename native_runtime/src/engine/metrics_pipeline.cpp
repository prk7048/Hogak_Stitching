#include "engine/metrics_pipeline.h"

#include <memory>
#include <string>

#include "output/output_writer.h"

namespace hogak::engine {

namespace {

double fps_from_period_ns(std::int64_t delta_ns) {
    if (delta_ns <= 0) {
        return 0.0;
    }
    return 1'000'000'000.0 / static_cast<double>(delta_ns);
}

void refresh_writer_metrics(
    const std::unique_ptr<hogak::output::OutputWriter>& writer,
    bool* active_out,
    std::int64_t* frames_written_out,
    std::int64_t* frames_dropped_out,
    std::int64_t* pending_frames_out,
    std::int64_t* queue_capacity_out,
    std::string* drop_policy_out,
    std::string* command_line_out,
    std::string* effective_codec_out,
    std::string* runtime_mode_out,
    std::string* last_error_out) {
    if (active_out == nullptr || frames_written_out == nullptr || frames_dropped_out == nullptr ||
        pending_frames_out == nullptr || queue_capacity_out == nullptr || drop_policy_out == nullptr ||
        command_line_out == nullptr || effective_codec_out == nullptr || runtime_mode_out == nullptr ||
        last_error_out == nullptr) {
        return;
    }
    if (writer == nullptr) {
        *active_out = false;
        *frames_written_out = 0;
        *frames_dropped_out = 0;
        *pending_frames_out = 0;
        *queue_capacity_out = 0;
        drop_policy_out->clear();
        return;
    }
    *active_out = writer->active();
    *frames_written_out = writer->frames_written();
    *frames_dropped_out = writer->frames_dropped();
    *pending_frames_out = writer->pending_frames();
    *queue_capacity_out = writer->max_pending_frames();
    *drop_policy_out = writer->drop_policy();
    *command_line_out = writer->command_line();
    *effective_codec_out = writer->effective_codec();
    *runtime_mode_out = writer->runtime_mode();
    *last_error_out = writer->last_error();
}

void refresh_stream_fps_metric(
    std::int64_t current_frames_written,
    bool stream_active,
    double* fps_out,
    std::int64_t now_arrival_ns,
    std::int64_t* last_frames_written,
    std::int64_t* last_timestamp_ns) {
    if (fps_out == nullptr || last_frames_written == nullptr || last_timestamp_ns == nullptr) {
        return;
    }
    if (current_frames_written < *last_frames_written) {
        *last_frames_written = current_frames_written;
        *last_timestamp_ns = 0;
    }
    if (current_frames_written > *last_frames_written) {
        const auto delta_frames = current_frames_written - *last_frames_written;
        const auto delta_ns = now_arrival_ns - *last_timestamp_ns;
        if (*last_timestamp_ns > 0 && delta_frames > 0 && delta_ns > 0) {
            *fps_out = fps_from_period_ns(delta_ns) * static_cast<double>(delta_frames);
        }
        *last_frames_written = current_frames_written;
        *last_timestamp_ns = now_arrival_ns;
    } else if (!stream_active) {
        *fps_out = 0.0;
    }
}

}  // namespace

const char* metrics_source_time_mode_name(hogak::input::FrameTimeDomain time_domain) noexcept {
    switch (time_domain) {
        case hogak::input::FrameTimeDomain::kSourceWallclock:
            return "wallclock";
        case hogak::input::FrameTimeDomain::kSourcePtsOffset:
            return "stream_pts_offset";
        case hogak::input::FrameTimeDomain::kSourceComparable:
            return "stream_pts";
        case hogak::input::FrameTimeDomain::kArrival:
        default:
            return "fallback-arrival";
    }
}

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
    std::int64_t* last_production_output_timestamp_ns) {
    if (metrics == nullptr ||
        last_stitched_count == nullptr ||
        last_stitch_timestamp_ns == nullptr ||
        last_output_frames_written == nullptr ||
        last_output_timestamp_ns == nullptr ||
        last_production_output_frames_written == nullptr ||
        last_production_output_timestamp_ns == nullptr) {
        return;
    }

    refresh_writer_metrics(
        output_writer,
        &metrics->output_active,
        &metrics->output_frames_written,
        &metrics->output_frames_dropped,
        &metrics->output_pending_frames,
        &metrics->output_queue_capacity,
        &metrics->output_drop_policy,
        &metrics->output_command_line,
        &metrics->output_effective_codec,
        &metrics->output_runtime_mode,
        &metrics->output_last_error);
    refresh_writer_metrics(
        production_output_writer,
        &metrics->production_output_active,
        &metrics->production_output_frames_written,
        &metrics->production_output_frames_dropped,
        &metrics->production_output_pending_frames,
        &metrics->production_output_queue_capacity,
        &metrics->production_output_drop_policy,
        &metrics->production_output_command_line,
        &metrics->production_output_effective_codec,
        &metrics->production_output_runtime_mode,
        &metrics->production_output_last_error);

    if (metrics->stitched_count < *last_stitched_count) {
        *last_stitched_count = metrics->stitched_count;
        *last_stitch_timestamp_ns = 0;
    }
    if (metrics->stitched_count > *last_stitched_count) {
        const auto delta_frames = metrics->stitched_count - *last_stitched_count;
        const auto delta_ns = now_arrival_ns - *last_stitch_timestamp_ns;
        if (*last_stitch_timestamp_ns > 0 && delta_frames > 0 && delta_ns > 0) {
            metrics->stitch_actual_fps =
                static_cast<double>(delta_frames) * 1'000'000'000.0 / static_cast<double>(delta_ns);
        }
        *last_stitched_count = metrics->stitched_count;
        *last_stitch_timestamp_ns = now_arrival_ns;
    }

    refresh_stream_fps_metric(
        metrics->output_frames_written,
        metrics->output_active,
        &metrics->output_written_fps,
        now_arrival_ns,
        last_output_frames_written,
        last_output_timestamp_ns);
    refresh_stream_fps_metric(
        metrics->production_output_frames_written,
        metrics->production_output_active,
        &metrics->production_output_written_fps,
        now_arrival_ns,
        last_production_output_frames_written,
        last_production_output_timestamp_ns);
}

}  // namespace hogak::engine
