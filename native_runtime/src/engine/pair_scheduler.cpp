#include "engine/pair_scheduler.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace hogak::engine {

static bool sync_mode_uses_pts_offset(const std::string& value);
static bool snapshot_has_source_pts(const hogak::input::ReaderSnapshot& snapshot);
static bool snapshot_has_wallclock(const hogak::input::ReaderSnapshot& snapshot);
static double cumulative_wait_sync_ratio(const EngineMetrics& metrics);
static double resolve_service_target_fps(
    const EngineConfig& config,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot);

namespace {

bool output_config_enabled(const OutputConfig& config) {
    return config.runtime != "none" && !config.target.empty();
}

bool frame_has_source_pts(const hogak::input::BufferedFrameInfo& info) {
    return info.source_time_valid && info.source_pts_ns > 0;
}

double clamp_unit(double value) {
    return std::clamp(value, 0.0, 1.0);
}

double snapshot_frame_period_ms(
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot) {
    const double fps = std::max({left_snapshot.fps, right_snapshot.fps, 30.0});
    if (!std::isfinite(fps) || fps <= 0.0) {
        return 1000.0 / 30.0;
    }
    return 1000.0 / fps;
}

double pearson_correlation(const std::vector<double>& left, const std::vector<double>& right) {
    if (left.size() != right.size() || left.size() < 3) {
        return 0.0;
    }
    double left_mean = 0.0;
    double right_mean = 0.0;
    for (std::size_t index = 0; index < left.size(); ++index) {
        left_mean += left[index];
        right_mean += right[index];
    }
    left_mean /= static_cast<double>(left.size());
    right_mean /= static_cast<double>(right.size());

    double cov = 0.0;
    double left_var = 0.0;
    double right_var = 0.0;
    for (std::size_t index = 0; index < left.size(); ++index) {
        const double left_delta = left[index] - left_mean;
        const double right_delta = right[index] - right_mean;
        cov += left_delta * right_delta;
        left_var += left_delta * left_delta;
        right_var += right_delta * right_delta;
    }
    if (left_var <= 1e-9 || right_var <= 1e-9) {
        return 0.0;
    }
    return cov / std::sqrt(left_var * right_var);
}

struct OffsetScore {
    bool valid = false;
    double combined_score = -1'000'000.0;
    int matched_pairs = 0;
    double avg_gap_ms = 0.0;
    double motion_corr = 0.0;
    double luma_corr = 0.0;
    double overlap_ratio = 0.0;
};

struct OffsetEstimateResult {
    bool valid = false;
    double offset_ms = 0.0;
    double confidence = 0.0;
    OffsetScore best_score;
    double second_best_score = -1'000'000.0;
    double selection_score = -1'000'000.0;
};

struct ServicePairCandidate {
    const hogak::input::BufferedFrameInfo* left_info = nullptr;
    const hogak::input::BufferedFrameInfo* right_info = nullptr;
    hogak::input::FrameTimeDomain time_domain = hogak::input::FrameTimeDomain::kArrival;
    std::int64_t left_pair_time_ns = 0;
    std::int64_t right_pair_time_ns = 0;
    std::int64_t pair_time_ns = 0;
    std::int64_t scheduler_pair_time_ns = 0;
    std::int64_t arrival_skew_ns = 0;
    std::int64_t source_skew_ns = 0;
    std::int64_t skew_ns = std::numeric_limits<std::int64_t>::max();
    std::int64_t sync_overage_ns = 0;
    std::int64_t cadence_error_ns = std::numeric_limits<std::int64_t>::max();
    std::int64_t freshness_ns = 0;
    std::int64_t newest_ns = 0;
    std::int64_t reuse_age_ns = 0;
    int reuse_streak = 0;
    int advance_score = 0;
};

struct IndexedRightFrame {
    const hogak::input::BufferedFrameInfo* info = nullptr;
    std::int64_t pair_time_ns = 0;
    std::int64_t adjusted_pair_time_ns = 0;
};

bool service_pair_candidate_better(
    const ServicePairCandidate& candidate,
    const ServicePairCandidate& best) {
    if (candidate.advance_score != best.advance_score) {
        return candidate.advance_score > best.advance_score;
    }
    if (candidate.sync_overage_ns != best.sync_overage_ns) {
        return candidate.sync_overage_ns < best.sync_overage_ns;
    }
    if (candidate.reuse_streak != best.reuse_streak) {
        return candidate.reuse_streak < best.reuse_streak;
    }
    if (candidate.reuse_age_ns != best.reuse_age_ns) {
        return candidate.reuse_age_ns < best.reuse_age_ns;
    }
    if (candidate.cadence_error_ns != best.cadence_error_ns) {
        return candidate.cadence_error_ns < best.cadence_error_ns;
    }
    if (candidate.skew_ns != best.skew_ns) {
        return candidate.skew_ns < best.skew_ns;
    }
    if (candidate.freshness_ns != best.freshness_ns) {
        return candidate.freshness_ns > best.freshness_ns;
    }
    if (candidate.newest_ns != best.newest_ns) {
        return candidate.newest_ns > best.newest_ns;
    }
    if (candidate.left_info != nullptr && best.left_info != nullptr && candidate.left_info->seq != best.left_info->seq) {
        return candidate.left_info->seq > best.left_info->seq;
    }
    if (candidate.right_info != nullptr && best.right_info != nullptr && candidate.right_info->seq != best.right_info->seq) {
        return candidate.right_info->seq > best.right_info->seq;
    }
    return false;
}

OffsetScore score_pts_offset_candidate(
    const std::vector<hogak::input::BufferedFrameInfo>& left_infos,
    const std::vector<hogak::input::BufferedFrameInfo>& right_infos,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double window_sec,
    double frame_period_ms,
    double offset_ms) {
    OffsetScore score;
    if (!snapshot_has_source_pts(left_snapshot) || !snapshot_has_source_pts(right_snapshot)) {
        return score;
    }
    const auto window_ns = static_cast<std::int64_t>(std::max(1.0, window_sec) * 1'000'000'000.0);
    const auto offset_ns = static_cast<std::int64_t>(std::llround(offset_ms * 1'000'000.0));
    const auto left_cutoff_ns = left_snapshot.latest_source_pts_ns - window_ns;
    const auto right_cutoff_ns = right_snapshot.latest_source_pts_ns - window_ns;
    const double max_gap_ms = std::max(20.0, 1.5 * std::max(1.0, frame_period_ms));
    const auto max_gap_ns = static_cast<std::int64_t>(std::llround(max_gap_ms * 1'000'000.0));

    std::vector<const hogak::input::BufferedFrameInfo*> filtered_left;
    std::vector<const hogak::input::BufferedFrameInfo*> filtered_right;
    filtered_left.reserve(left_infos.size());
    filtered_right.reserve(right_infos.size());
    for (const auto& info : left_infos) {
        if (frame_has_source_pts(info) && info.source_pts_ns >= left_cutoff_ns) {
            filtered_left.push_back(&info);
        }
    }
    for (const auto& info : right_infos) {
        if (frame_has_source_pts(info) && info.source_pts_ns >= right_cutoff_ns) {
            filtered_right.push_back(&info);
        }
    }
    if (filtered_left.size() < 4 || filtered_right.size() < 4) {
        return score;
    }

    std::vector<double> left_motion;
    std::vector<double> right_motion;
    std::vector<double> left_luma;
    std::vector<double> right_luma;
    double gap_sum_ms = 0.0;

    for (const auto* left_info : filtered_left) {
        const auto target_right_pts_ns = left_info->source_pts_ns - offset_ns;
        const hogak::input::BufferedFrameInfo* best_right = nullptr;
        auto best_gap_ns = max_gap_ns + 1;
        for (const auto* right_info : filtered_right) {
            const auto gap_ns = std::llabs(right_info->source_pts_ns - target_right_pts_ns);
            if (gap_ns < best_gap_ns) {
                best_gap_ns = gap_ns;
                best_right = right_info;
            }
        }
        if (best_right == nullptr || best_gap_ns > max_gap_ns) {
            continue;
        }
        left_motion.push_back(left_info->motion_score);
        right_motion.push_back(best_right->motion_score);
        left_luma.push_back(left_info->luma_mean);
        right_luma.push_back(best_right->luma_mean);
        gap_sum_ms += static_cast<double>(best_gap_ns) / 1'000'000.0;
    }

    if (left_motion.size() < 4) {
        return score;
    }

    score.valid = true;
    score.matched_pairs = static_cast<int>(left_motion.size());
    score.avg_gap_ms = gap_sum_ms / static_cast<double>(score.matched_pairs);
    score.motion_corr = pearson_correlation(left_motion, right_motion);
    score.luma_corr = pearson_correlation(left_luma, right_luma);
    score.overlap_ratio = static_cast<double>(score.matched_pairs) /
        static_cast<double>(std::max<std::size_t>(1, std::min(filtered_left.size(), filtered_right.size())));
    score.combined_score =
        score.motion_corr +
        (0.20 * score.luma_corr) +
        (0.35 * score.overlap_ratio) -
        (0.002 * score.avg_gap_ms) -
        (0.0015 * std::abs(offset_ms));
    return score;
}

OffsetEstimateResult estimate_pts_offset_from_buffers(
    const std::vector<hogak::input::BufferedFrameInfo>& left_infos,
    const std::vector<hogak::input::BufferedFrameInfo>& right_infos,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double window_sec,
    double max_search_ms,
    double center_offset_ms,
    bool local_search_only) {
    OffsetEstimateResult result;
    if (!snapshot_has_source_pts(left_snapshot) || !snapshot_has_source_pts(right_snapshot)) {
        return result;
    }

    const double frame_period_ms = snapshot_frame_period_ms(left_snapshot, right_snapshot);
    const double latest_pts_skew_ms =
        static_cast<double>(left_snapshot.latest_source_pts_ns - right_snapshot.latest_source_pts_ns) / 1'000'000.0;
    const double adaptive_margin_ms = std::max(50.0, 3.0 * std::max(1.0, frame_period_ms));
    const double bounded_search_ms = std::max(
        50.0,
        local_search_only
            ? std::abs(max_search_ms)
            : std::max(std::abs(max_search_ms), std::abs(latest_pts_skew_ms) + adaptive_margin_ms));
    double search_center_ms = std::isfinite(center_offset_ms) ? center_offset_ms : 0.0;
    if (!local_search_only && std::abs(search_center_ms) <= adaptive_margin_ms) {
        search_center_ms = latest_pts_skew_ms;
    }
    search_center_ms = std::clamp(search_center_ms, -bounded_search_ms, bounded_search_ms);

    const auto scan_range = [&](double start_ms,
                                double end_ms,
                                double step_ms,
                                OffsetScore* best_score_out,
                                double* best_offset_out,
                                double* best_selection_score_out,
                                double* second_best_score_out) {
        for (double offset_ms = start_ms; offset_ms <= end_ms + (step_ms * 0.5); offset_ms += step_ms) {
            const auto score = score_pts_offset_candidate(
                left_infos,
                right_infos,
                left_snapshot,
                right_snapshot,
                window_sec,
                frame_period_ms,
                offset_ms);
            if (!score.valid) {
                continue;
            }
            const double stability_penalty = 0.0020 * std::abs(offset_ms - search_center_ms);
            const double selection_score = score.combined_score - stability_penalty;
            const bool is_better =
                !best_score_out->valid ||
                (selection_score > (*best_selection_score_out + 1e-9)) ||
                (std::abs(selection_score - *best_selection_score_out) <= 1e-9 &&
                 std::abs(offset_ms - search_center_ms) < std::abs(*best_offset_out - search_center_ms));
            if (is_better) {
                *second_best_score_out = best_score_out->valid ? *best_selection_score_out : *second_best_score_out;
                *best_score_out = score;
                *best_offset_out = offset_ms;
                *best_selection_score_out = selection_score;
            } else if (selection_score > *second_best_score_out) {
                *second_best_score_out = selection_score;
            }
        }
    };

    const double coarse_radius_ms = local_search_only ? std::min(bounded_search_ms, 125.0) : bounded_search_ms;
    double best_offset_ms = search_center_ms;
    OffsetScore best_score;
    double best_selection_score = -1'000'000.0;
    double second_best_score = -1'000'000.0;
    scan_range(
        std::max(-bounded_search_ms, search_center_ms - coarse_radius_ms),
        std::min(bounded_search_ms, search_center_ms + coarse_radius_ms),
        25.0,
        &best_score,
        &best_offset_ms,
        &best_selection_score,
        &second_best_score);
    if (!best_score.valid && !local_search_only) {
        scan_range(
            -bounded_search_ms,
            bounded_search_ms,
            25.0,
            &best_score,
            &best_offset_ms,
            &best_selection_score,
            &second_best_score);
    }
    if (!best_score.valid) {
        return result;
    }

    scan_range(
        std::max(-bounded_search_ms, best_offset_ms - 40.0),
        std::min(bounded_search_ms, best_offset_ms + 40.0),
        5.0,
        &best_score,
        &best_offset_ms,
        &best_selection_score,
        &second_best_score);
    scan_range(
        std::max(-bounded_search_ms, best_offset_ms - 10.0),
        std::min(bounded_search_ms, best_offset_ms + 10.0),
        1.0,
        &best_score,
        &best_offset_ms,
        &best_selection_score,
        &second_best_score);

    const double motion_component = clamp_unit(std::max(0.0, best_score.motion_corr));
    const double luma_component = clamp_unit(std::max(0.0, best_score.luma_corr));
    const double overlap_component = clamp_unit(best_score.overlap_ratio);
    const double max_gap_ms = std::max(20.0, 1.5 * std::max(1.0, frame_period_ms));
    const double gap_component = clamp_unit(1.0 - (best_score.avg_gap_ms / max_gap_ms));
    const double peak_component = clamp_unit((best_selection_score - second_best_score + 0.5) / 1.0);
    const double pairs_component = clamp_unit((static_cast<double>(best_score.matched_pairs) - 4.0) / 8.0);

    result.valid = true;
    result.offset_ms = best_offset_ms;
    result.best_score = best_score;
    result.second_best_score = second_best_score;
    result.selection_score = best_selection_score;
    result.confidence = clamp_unit(
        (0.30 * motion_component) +
        (0.05 * luma_component) +
        (0.20 * overlap_component) +
        (0.20 * gap_component) +
        (0.15 * peak_component) +
        (0.10 * pairs_component));
    return result;
}

std::int64_t compute_source_skew_ns(
    const hogak::input::BufferedFrameInfo& left_info,
    const hogak::input::BufferedFrameInfo& right_info,
    hogak::input::FrameTimeDomain time_domain,
    std::int64_t effective_offset_ns) {
    if (time_domain == hogak::input::FrameTimeDomain::kSourceWallclock) {
        if (left_info.source_wallclock_ns <= 0 || right_info.source_wallclock_ns <= 0) {
            return std::int64_t{0};
        }
        return std::llabs(left_info.source_wallclock_ns - right_info.source_wallclock_ns);
    }
    if (time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset ||
        time_domain == hogak::input::FrameTimeDomain::kSourceComparable) {
        if (!frame_has_source_pts(left_info) || !frame_has_source_pts(right_info)) {
            return std::int64_t{0};
        }
        return std::llabs(left_info.source_pts_ns - (right_info.source_pts_ns + effective_offset_ns));
    }
    return std::int64_t{0};
}

void fill_pair_from_infos(
    const hogak::input::BufferedFrameInfo& left_info,
    const hogak::input::BufferedFrameInfo& right_info,
    std::int64_t effective_offset_ns,
    const std::string& effective_offset_source,
    double effective_offset_confidence,
    PairSchedulerSelectedPair* pair_out) {
    pair_out->left_frame = left_info.frame;
    pair_out->right_frame = right_info.frame;
    pair_out->left_seq = left_info.seq;
    pair_out->right_seq = right_info.seq;
    pair_out->left_ts_ns = left_info.arrival_timestamp_ns;
    pair_out->right_ts_ns = right_info.arrival_timestamp_ns;
    pair_out->left_arrival_ts_ns = left_info.arrival_timestamp_ns;
    pair_out->right_arrival_ts_ns = right_info.arrival_timestamp_ns;
    pair_out->left_source_pts_ns = left_info.source_pts_ns;
    pair_out->right_source_pts_ns = right_info.source_pts_ns;
    pair_out->left_source_dts_ns = left_info.source_dts_ns;
    pair_out->right_source_dts_ns = right_info.source_dts_ns;
    pair_out->left_source_wallclock_ns = left_info.source_wallclock_ns;
    pair_out->right_source_wallclock_ns = right_info.source_wallclock_ns;
    pair_out->left_source_time_valid = left_info.source_time_valid;
    pair_out->right_source_time_valid = right_info.source_time_valid;
    pair_out->left_source_time_comparable = left_info.source_time_comparable;
    pair_out->right_source_time_comparable = right_info.source_time_comparable;
    pair_out->left_source_time_kind = left_info.source_time_kind;
    pair_out->right_source_time_kind = right_info.source_time_kind;
    pair_out->effective_offset_ns = effective_offset_ns;
    pair_out->offset_source = effective_offset_source;
    pair_out->offset_confidence = effective_offset_confidence;
}

bool build_service_pair(
    const PairSelectionContext& context,
    PairSchedulerState* state,
    std::int64_t effective_offset_ns,
    double effective_offset_confidence,
    const std::string& effective_offset_source,
    PairSchedulerSelectedPair* pair_out) {
    const auto service_time_domain = resolve_service_time_domain(
        context.config,
        context.left_snapshot,
        context.right_snapshot,
        effective_offset_confidence);

    std::int64_t resolved_offset_ns = effective_offset_ns;
    double resolved_offset_confidence = effective_offset_confidence;
    std::string resolved_offset_source = effective_offset_source;
    if (service_time_domain == hogak::input::FrameTimeDomain::kArrival) {
        resolved_offset_ns = 0;
        resolved_offset_confidence = 0.0;
        resolved_offset_source = "arrival-fallback";
    } else if (service_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock) {
        resolved_offset_ns = 0;
        resolved_offset_confidence = 1.0;
        resolved_offset_source = "wallclock";
    }

    const auto max_delta_ns = static_cast<std::int64_t>(
        std::max(1.0, context.config.sync_match_max_delta_ms) * 1'000'000.0);
    const double target_fps = resolve_service_target_fps(context.config, context.left_snapshot, context.right_snapshot);
    const auto target_period_ns = static_cast<std::int64_t>(
        std::max(1.0, std::round(1'000'000'000.0 / std::max(1.0, target_fps))));
    const auto fresh_pair_slack_ns = std::min<std::int64_t>(
        max_delta_ns / 4,
        std::max<std::int64_t>(1'000'000, target_period_ns / 2));
    const auto max_reuse_age_ns = static_cast<std::int64_t>(
        std::max(1.0, context.config.pair_reuse_max_age_ms) * 1'000'000.0);
    const int max_consecutive_reuse = std::max(1, context.config.pair_reuse_max_consecutive);
    const auto latest_left_pair_time_ns = snapshot_latest_time_ns(context.left_snapshot, service_time_domain);
    const auto latest_right_pair_time_ns = snapshot_latest_time_ns(context.right_snapshot, service_time_domain) + resolved_offset_ns;
    const auto latest_pair_time_ns = std::max(latest_left_pair_time_ns, latest_right_pair_time_ns);
    const bool same_service_domain = state->last_pair_time_domain == service_time_domain;
    const auto unclamped_target_pair_time_ns =
        (same_service_domain && state->last_service_pair_ts_ns > 0)
            ? (state->last_service_pair_ts_ns + target_period_ns)
            : latest_pair_time_ns;
    const auto min_target_pair_time_ns = latest_pair_time_ns - target_period_ns;
    const auto target_pair_time_ns = std::max(unclamped_target_pair_time_ns, min_target_pair_time_ns);

    std::vector<IndexedRightFrame> indexed_right;
    indexed_right.reserve(context.right_infos.size());
    for (const auto& right_info : context.right_infos) {
        if (!right_info.has_time(service_time_domain)) {
            continue;
        }
        const auto pair_time_ns = right_info.resolve_time_ns(service_time_domain);
        indexed_right.push_back(IndexedRightFrame{
            &right_info,
            pair_time_ns,
            pair_time_ns + resolved_offset_ns,
        });
    }
    if (indexed_right.empty()) {
        return false;
    }

    ServicePairCandidate best_candidate;
    bool have_candidate = false;
    for (const auto& left_info : context.left_infos) {
        if (!left_info.has_time(service_time_domain)) {
            continue;
        }
        const auto left_pair_time_ns = left_info.resolve_time_ns(service_time_domain);
        const auto lower = std::lower_bound(
            indexed_right.begin(),
            indexed_right.end(),
            left_pair_time_ns,
            [](const IndexedRightFrame& candidate, std::int64_t target) {
                return candidate.adjusted_pair_time_ns < target;
            });
        const std::size_t lower_index = static_cast<std::size_t>(std::distance(indexed_right.begin(), lower));
        const std::size_t start_index = (lower_index > 2) ? (lower_index - 2) : 0;
        const std::size_t end_index = std::min(indexed_right.size(), lower_index + 3);
        for (std::size_t index = start_index; index < end_index; ++index) {
            const auto& right_entry = indexed_right[index];
            const auto& right_info = *right_entry.info;
            const auto adjusted_right_pair_time_ns = right_entry.adjusted_pair_time_ns;
            const auto skew_ns = std::llabs(left_pair_time_ns - adjusted_right_pair_time_ns);
            const bool has_new_left = left_info.seq > state->last_left_seq;
            const bool has_new_right = right_info.seq > state->last_right_seq;
            const bool left_reused = !has_new_left;
            const bool right_reused = !has_new_right;
            if (!has_new_left && !has_new_right) {
                continue;
            }
            if (!context.config.allow_frame_reuse && (left_reused || right_reused)) {
                continue;
            }
            const auto allowed_skew_ns =
                (!left_reused && !right_reused) ? (max_delta_ns + fresh_pair_slack_ns) : max_delta_ns;
            if (skew_ns > allowed_skew_ns) {
                continue;
            }

            const auto left_age_ns = std::max<std::int64_t>(0, latest_pair_time_ns - left_pair_time_ns);
            const auto right_age_ns = std::max<std::int64_t>(0, latest_pair_time_ns - adjusted_right_pair_time_ns);
            const bool can_reuse_left =
                left_reused &&
                context.config.allow_frame_reuse &&
                left_age_ns <= max_reuse_age_ns &&
                state->consecutive_left_reuse < max_consecutive_reuse;
            const bool can_reuse_right =
                right_reused &&
                context.config.allow_frame_reuse &&
                right_age_ns <= max_reuse_age_ns &&
                state->consecutive_right_reuse < max_consecutive_reuse;
            if ((left_reused && !can_reuse_left) || (right_reused && !can_reuse_right)) {
                continue;
            }

            ServicePairCandidate candidate;
            candidate.left_info = &left_info;
            candidate.right_info = &right_info;
            candidate.time_domain = service_time_domain;
            candidate.left_pair_time_ns = left_pair_time_ns;
            candidate.right_pair_time_ns = right_entry.pair_time_ns;
            candidate.skew_ns = skew_ns;
            candidate.arrival_skew_ns =
                std::llabs(left_info.arrival_timestamp_ns - right_info.arrival_timestamp_ns);
            candidate.source_skew_ns = compute_source_skew_ns(left_info, right_info, service_time_domain, resolved_offset_ns);
            candidate.sync_overage_ns = std::max<std::int64_t>(0, skew_ns - max_delta_ns);
            candidate.pair_time_ns = std::max(left_pair_time_ns, adjusted_right_pair_time_ns);
            candidate.scheduler_pair_time_ns = candidate.pair_time_ns;
            candidate.cadence_error_ns = std::llabs(candidate.pair_time_ns - target_pair_time_ns);
            candidate.freshness_ns = std::min(left_pair_time_ns, adjusted_right_pair_time_ns);
            candidate.newest_ns = candidate.pair_time_ns;
            candidate.reuse_age_ns = std::max(left_age_ns, right_age_ns);
            candidate.reuse_streak =
                (left_reused ? state->consecutive_left_reuse : 0) +
                (right_reused ? state->consecutive_right_reuse : 0);
            candidate.advance_score = static_cast<int>(has_new_left) + static_cast<int>(has_new_right);
            if (!have_candidate || service_pair_candidate_better(candidate, best_candidate)) {
                best_candidate = candidate;
                have_candidate = true;
            }
        }
    }

    if (!have_candidate || best_candidate.left_info == nullptr || best_candidate.right_info == nullptr) {
        return false;
    }

    fill_pair_from_infos(
        *best_candidate.left_info,
        *best_candidate.right_info,
        resolved_offset_ns,
        resolved_offset_source,
        resolved_offset_confidence,
        pair_out);
    pair_out->pair_time_domain = best_candidate.time_domain;
    pair_out->pair_time_ns = best_candidate.pair_time_ns;
    pair_out->scheduler_pair_time_ns = best_candidate.scheduler_pair_time_ns;
    pair_out->arrival_skew_ns = best_candidate.arrival_skew_ns;
    pair_out->source_skew_ns = best_candidate.source_skew_ns;
    return true;
}

}  // namespace

std::string normalize_sync_time_source(std::string value) {
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    if (value == "pts-offset-manual" ||
        value == "pts-offset-auto" ||
        value == "pts-offset-hybrid" ||
        value == "arrival" ||
        value == "wallclock") {
        return value;
    }
    return "pts-offset-auto";
}

static bool sync_mode_uses_pts_offset(const std::string& value) {
    return value == "pts-offset-manual" || value == "pts-offset-auto" || value == "pts-offset-hybrid";
}

static bool snapshot_has_source_pts(const hogak::input::ReaderSnapshot& snapshot) {
    return snapshot.latest_source_time_valid && snapshot.latest_source_pts_ns > 0;
}

static bool snapshot_has_wallclock(const hogak::input::ReaderSnapshot& snapshot) {
    return snapshot.latest_source_time_comparable &&
        snapshot.latest_comparable_source_timestamp_ns > 0;
}

hogak::input::FrameTimeDomain resolve_service_time_domain(
    const EngineConfig& config,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    double sync_offset_confidence) {
    const std::string sync_time_source = normalize_sync_time_source(config.sync_time_source);
    if (sync_time_source == "wallclock") {
        return (snapshot_has_wallclock(left_snapshot) && snapshot_has_wallclock(right_snapshot))
            ? hogak::input::FrameTimeDomain::kSourceWallclock
            : hogak::input::FrameTimeDomain::kArrival;
    }
    if (sync_time_source == "arrival") {
        return hogak::input::FrameTimeDomain::kArrival;
    }
    if (!sync_mode_uses_pts_offset(sync_time_source) ||
        !snapshot_has_source_pts(left_snapshot) ||
        !snapshot_has_source_pts(right_snapshot)) {
        return hogak::input::FrameTimeDomain::kArrival;
    }
    if (sync_time_source == "pts-offset-manual") {
        return hogak::input::FrameTimeDomain::kSourcePtsOffset;
    }
    if (sync_time_source == "pts-offset-auto") {
        return (sync_offset_confidence >= std::max(0.0, config.sync_auto_offset_confidence_min))
            ? hogak::input::FrameTimeDomain::kSourcePtsOffset
            : hogak::input::FrameTimeDomain::kArrival;
    }
    if (sync_time_source == "pts-offset-hybrid" && std::abs(config.sync_manual_offset_ms) > 1e-6) {
        if (sync_offset_confidence < std::max(0.0, config.sync_auto_offset_confidence_min)) {
            return hogak::input::FrameTimeDomain::kSourcePtsOffset;
        }
    }
    return (sync_offset_confidence >= std::max(0.0, config.sync_auto_offset_confidence_min))
        ? hogak::input::FrameTimeDomain::kSourcePtsOffset
        : hogak::input::FrameTimeDomain::kArrival;
}

std::int64_t snapshot_latest_time_ns(
    const hogak::input::ReaderSnapshot& snapshot,
    hogak::input::FrameTimeDomain time_domain) {
    switch (time_domain) {
        case hogak::input::FrameTimeDomain::kSourceWallclock:
            return snapshot.latest_comparable_source_timestamp_ns;
        case hogak::input::FrameTimeDomain::kSourcePtsOffset:
        case hogak::input::FrameTimeDomain::kSourceComparable:
            return snapshot.latest_source_pts_ns;
        case hogak::input::FrameTimeDomain::kArrival:
        default:
            return snapshot.latest_timestamp_ns;
    }
}

static double cumulative_wait_sync_ratio(const EngineMetrics& metrics) {
    const double total =
        static_cast<double>(
            metrics.stitched_count +
            metrics.realtime_fallback_pair_count +
            metrics.wait_sync_pair_count +
            metrics.wait_next_frame_count +
            metrics.wait_paired_fresh_count +
            metrics.wait_both_streams_count);
    if (total <= 0.0) {
        return 0.0;
    }
    return static_cast<double>(metrics.wait_sync_pair_count) / total;
}

static double resolve_service_target_fps(
    const EngineConfig& config,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot) {
    double configured_output_fps = 0.0;
    if (output_config_enabled(config.production_output) && config.production_output.fps > 0.0) {
        configured_output_fps = std::max(configured_output_fps, config.production_output.fps);
    }
    if (output_config_enabled(config.output) && config.output.fps > 0.0) {
        configured_output_fps = std::max(configured_output_fps, config.output.fps);
    }
    if (configured_output_fps > 0.0) {
        return configured_output_fps;
    }
    return std::max({left_snapshot.fps, right_snapshot.fps, 30.0});
}

std::int64_t selected_pair_left_time_ns(const PairSchedulerSelectedPair& pair) noexcept {
    switch (pair.pair_time_domain) {
        case hogak::input::FrameTimeDomain::kSourceWallclock:
            return pair.left_source_wallclock_ns;
        case hogak::input::FrameTimeDomain::kSourcePtsOffset:
        case hogak::input::FrameTimeDomain::kSourceComparable:
            return pair.left_source_pts_ns;
        case hogak::input::FrameTimeDomain::kArrival:
        default:
            return pair.left_arrival_ts_ns;
    }
}

std::int64_t selected_pair_right_time_ns(const PairSchedulerSelectedPair& pair) noexcept {
    switch (pair.pair_time_domain) {
        case hogak::input::FrameTimeDomain::kSourceWallclock:
            return pair.right_source_wallclock_ns;
        case hogak::input::FrameTimeDomain::kSourcePtsOffset:
        case hogak::input::FrameTimeDomain::kSourceComparable:
            return pair.right_source_pts_ns;
        case hogak::input::FrameTimeDomain::kArrival:
        default:
            return pair.right_arrival_ts_ns;
    }
}

std::int64_t selected_pair_now_ns(
    const PairSchedulerSelectedPair& pair,
    const hogak::input::ReaderSnapshot& left_snapshot,
    const hogak::input::ReaderSnapshot& right_snapshot,
    std::int64_t now_arrival_ns,
    std::int64_t now_source_wallclock_ns) {
    if (pair.pair_time_domain == hogak::input::FrameTimeDomain::kSourceWallclock) {
        return now_source_wallclock_ns;
    }
    if (pair.pair_time_domain == hogak::input::FrameTimeDomain::kSourcePtsOffset) {
        return std::max(
            snapshot_latest_time_ns(left_snapshot, pair.pair_time_domain),
            snapshot_latest_time_ns(right_snapshot, pair.pair_time_domain) + pair.effective_offset_ns);
    }
    return now_arrival_ns;
}

bool select_pair(
    const PairSelectionContext& context,
    PairSchedulerState* state,
    PairSchedulerSelectedPair* pair_out) {
    if (state == nullptr || pair_out == nullptr || !context.left_snapshot.has_frame || !context.right_snapshot.has_frame) {
        return false;
    }

    const auto mode = context.config.sync_pair_mode;
    const auto manual_offset_ns = static_cast<std::int64_t>(context.config.sync_manual_offset_ms * 1'000'000.0);
    const std::string sync_time_source = normalize_sync_time_source(context.config.sync_time_source);
    std::int64_t effective_offset_ns = 0;
    double effective_offset_confidence = 0.0;
    std::string effective_offset_source = "arrival-fallback";

    if (mode == "none") {
        if (context.left_infos.empty() || context.right_infos.empty()) {
            return false;
        }
        const auto& left_info = context.left_infos.back();
        const auto& right_info = context.right_infos.back();
        fill_pair_from_infos(left_info, right_info, effective_offset_ns, effective_offset_source, effective_offset_confidence, pair_out);
        pair_out->pair_time_domain = hogak::input::FrameTimeDomain::kArrival;
        pair_out->pair_time_ns = std::max(pair_out->left_arrival_ts_ns, pair_out->right_arrival_ts_ns);
        pair_out->scheduler_pair_time_ns = pair_out->pair_time_ns;
        pair_out->arrival_skew_ns = std::llabs(pair_out->left_arrival_ts_ns - pair_out->right_arrival_ts_ns);
        pair_out->source_skew_ns = compute_source_skew_ns(left_info, right_info, hogak::input::FrameTimeDomain::kArrival, 0);
        return true;
    }

    if (mode != "service") {
        return false;
    }

    const bool has_pts_time =
        snapshot_has_source_pts(context.left_snapshot) &&
        snapshot_has_source_pts(context.right_snapshot);
    const auto now_arrival_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();

    if (sync_time_source == "pts-offset-manual" && has_pts_time) {
        effective_offset_ns = manual_offset_ns;
        effective_offset_confidence = 1.0;
        effective_offset_source = "manual";
        state->effective_sync_offset_ms = context.config.sync_manual_offset_ms;
        state->sync_offset_confidence = effective_offset_confidence;
        state->sync_offset_source = effective_offset_source;
    } else if ((sync_time_source == "pts-offset-auto" || sync_time_source == "pts-offset-hybrid") && has_pts_time) {
        const double confidence_min = std::max(0.0, context.config.sync_auto_offset_confidence_min);
        const bool have_auto_estimate =
            (state->sync_offset_source == "auto" || state->sync_offset_source == "recalibration");
        const bool periodic_recalibration_due =
            state->last_sync_recalibration_ns <= 0 ||
            (now_arrival_ns - state->last_sync_recalibration_ns) >= static_cast<std::int64_t>(
                std::max(1.0, context.config.sync_recalibration_interval_sec) * 1'000'000'000.0);
        const bool recalibration_cooldown_elapsed =
            state->last_sync_recalibration_ns <= 0 ||
            (now_arrival_ns - state->last_sync_recalibration_ns) >= 5'000'000'000LL;
        const bool sync_quality_degraded =
            context.metrics.pair_source_skew_ms_mean >= std::max(0.0, context.config.sync_recalibration_trigger_skew_ms) ||
            cumulative_wait_sync_ratio(context.metrics) >= std::max(0.0, context.config.sync_recalibration_trigger_wait_ratio);
        const bool should_estimate =
            !have_auto_estimate || periodic_recalibration_due || (sync_quality_degraded && recalibration_cooldown_elapsed);
        if (should_estimate) {
            const auto estimate = estimate_pts_offset_from_buffers(
                context.left_infos,
                context.right_infos,
                context.left_snapshot,
                context.right_snapshot,
                context.config.sync_auto_offset_window_sec,
                context.config.sync_auto_offset_max_search_ms,
                have_auto_estimate ? state->effective_sync_offset_ms : 0.0,
                have_auto_estimate);
            state->sync_estimate_pairs = estimate.valid ? estimate.best_score.matched_pairs : 0;
            state->sync_estimate_avg_gap_ms = estimate.valid ? estimate.best_score.avg_gap_ms : 0.0;
            state->sync_estimate_score = estimate.valid ? estimate.selection_score : 0.0;
            const bool estimate_meets_baseline =
                estimate.valid &&
                estimate.confidence >= confidence_min &&
                estimate.best_score.matched_pairs >= 8 &&
                estimate.best_score.avg_gap_ms <= 15.0;
            const bool requires_extra_confirmation =
                have_auto_estimate &&
                std::abs(estimate.offset_ms - state->effective_sync_offset_ms) >= 20.0;
            const bool strong_estimate =
                estimate_meets_baseline &&
                (!requires_extra_confirmation || estimate.confidence >= 0.95);
            if (strong_estimate) {
                if (have_auto_estimate) {
                    const double blended_offset_ms =
                        (0.9 * state->effective_sync_offset_ms) + (0.1 * estimate.offset_ms);
                    const double limited_delta_ms = std::clamp(
                        blended_offset_ms - state->effective_sync_offset_ms,
                        -10.0,
                        10.0);
                    state->effective_sync_offset_ms += limited_delta_ms;
                    state->sync_offset_source = "recalibration";
                    state->sync_recalibration_count += 1;
                } else {
                    state->effective_sync_offset_ms = estimate.offset_ms;
                    state->sync_offset_source = "auto";
                }
                state->sync_offset_confidence = estimate.confidence;
                state->last_sync_recalibration_ns = now_arrival_ns;
            } else if (!have_auto_estimate) {
                state->effective_sync_offset_ms = 0.0;
                state->sync_offset_confidence = estimate.valid ? estimate.confidence : 0.0;
                state->sync_offset_source = "auto";
                state->last_sync_recalibration_ns = now_arrival_ns;
            }
        }
        if (sync_time_source == "pts-offset-auto") {
            effective_offset_ns = static_cast<std::int64_t>(std::llround(state->effective_sync_offset_ms * 1'000'000.0));
            effective_offset_confidence = state->sync_offset_confidence;
            effective_offset_source = state->sync_offset_source;
        } else if (state->sync_offset_confidence >= confidence_min) {
            effective_offset_ns = static_cast<std::int64_t>(std::llround(state->effective_sync_offset_ms * 1'000'000.0));
            effective_offset_confidence = state->sync_offset_confidence;
            effective_offset_source = state->sync_offset_source;
        } else if (sync_time_source == "pts-offset-hybrid" && std::abs(context.config.sync_manual_offset_ms) > 1e-6) {
            effective_offset_ns = manual_offset_ns;
            effective_offset_confidence = 1.0;
            effective_offset_source = "manual";
            state->effective_sync_offset_ms = context.config.sync_manual_offset_ms;
            state->sync_offset_confidence = effective_offset_confidence;
            state->sync_offset_source = effective_offset_source;
        }
    } else if (sync_time_source == "wallclock") {
        effective_offset_source = "wallclock";
        effective_offset_confidence = 1.0;
        state->effective_sync_offset_ms = 0.0;
        state->sync_offset_confidence = 1.0;
        state->sync_offset_source = "wallclock";
        state->sync_estimate_pairs = 0;
        state->sync_estimate_avg_gap_ms = 0.0;
        state->sync_estimate_score = 0.0;
    } else {
        state->effective_sync_offset_ms = 0.0;
        state->sync_offset_confidence = 0.0;
        state->sync_offset_source = "arrival-fallback";
        state->sync_estimate_pairs = 0;
        state->sync_estimate_avg_gap_ms = 0.0;
        state->sync_estimate_score = 0.0;
    }

    return build_service_pair(
        context,
        state,
        effective_offset_ns,
        effective_offset_confidence,
        effective_offset_source,
        pair_out);
}

}  // namespace hogak::engine
