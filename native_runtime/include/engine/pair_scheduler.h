#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "engine/engine_config.h"
#include "engine/engine_metrics.h"
#include "input/ffmpeg_rtsp_reader.h"

namespace hogak::engine {

struct PairSchedulerSelectedPair {
    cv::Mat left_frame;
    cv::Mat right_frame;
    std::int64_t left_seq = 0;
    std::int64_t right_seq = 0;
    std::int64_t left_ts_ns = 0;
    std::int64_t right_ts_ns = 0;
    std::int64_t left_arrival_ts_ns = 0;
    std::int64_t right_arrival_ts_ns = 0;
    std::int64_t left_source_pts_ns = 0;
    std::int64_t right_source_pts_ns = 0;
    std::int64_t left_source_dts_ns = 0;
    std::int64_t right_source_dts_ns = 0;
    std::int64_t left_source_wallclock_ns = 0;
    std::int64_t right_source_wallclock_ns = 0;
    bool left_source_time_valid = false;
    bool right_source_time_valid = false;
    bool left_source_time_comparable = false;
    bool right_source_time_comparable = false;
    hogak::input::SourceTimeKind left_source_time_kind = hogak::input::SourceTimeKind::kNone;
    hogak::input::SourceTimeKind right_source_time_kind = hogak::input::SourceTimeKind::kNone;
    hogak::input::FrameTimeDomain pair_time_domain = hogak::input::FrameTimeDomain::kArrival;
    std::int64_t pair_time_ns = 0;
    std::int64_t scheduler_pair_time_ns = 0;
    std::int64_t arrival_skew_ns = 0;
    std::int64_t source_skew_ns = 0;
    std::int64_t effective_offset_ns = 0;
    std::string offset_source = "arrival-fallback";
    double offset_confidence = 0.0;
};

struct PairSchedulerState {
    std::int64_t last_left_seq = 0;
    std::int64_t last_right_seq = 0;
    std::int64_t last_service_pair_ts_ns = 0;
    hogak::input::FrameTimeDomain last_pair_time_domain = hogak::input::FrameTimeDomain::kArrival;
    std::int64_t last_sync_recalibration_ns = 0;
    double effective_sync_offset_ms = 0.0;
    double sync_offset_confidence = 0.0;
    std::int64_t sync_recalibration_count = 0;
    std::string sync_offset_source = "arrival-fallback";
    std::int64_t sync_estimate_pairs = 0;
    double sync_estimate_avg_gap_ms = 0.0;
    double sync_estimate_score = 0.0;
    std::int32_t consecutive_left_reuse = 0;
    std::int32_t consecutive_right_reuse = 0;
};

struct PairSelectionContext {
    const EngineConfig& config;
    const EngineMetrics& metrics;
    const hogak::input::ReaderSnapshot& left_snapshot;
    const hogak::input::ReaderSnapshot& right_snapshot;
    const std::vector<hogak::input::BufferedFrameInfo>& left_infos;
    const std::vector<hogak::input::BufferedFrameInfo>& right_infos;
};

std::string normalize_sync_time_source(std::string value);
hogak::input::FrameTimeDomain resolve_service_time_domain(
    const EngineConfig& config,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double sync_offset_confidence);
std::int64_t snapshot_latest_time_ns(
    const hogak::input::ReaderSnapshot& snapshot,
    hogak::input::FrameTimeDomain time_domain);
std::int64_t selected_pair_left_time_ns(const PairSchedulerSelectedPair& pair) noexcept;
std::int64_t selected_pair_right_time_ns(const PairSchedulerSelectedPair& pair) noexcept;
std::int64_t selected_pair_now_ns(
    const PairSchedulerSelectedPair& pair,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    std::int64_t now_arrival_ns,
    std::int64_t now_source_wallclock_ns);
bool select_pair(
    const PairSelectionContext& context,
    PairSchedulerState* state,
    PairSchedulerSelectedPair* pair_out);

}  // namespace hogak::engine
