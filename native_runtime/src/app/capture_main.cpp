#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/imgcodecs.hpp>

#include "engine/engine_config.h"
#include "input/ffmpeg_rtsp_reader.h"

namespace {

using hogak::input::BufferedFrameInfo;
using hogak::input::FfmpegRtspReader;
using hogak::input::FrameTimeDomain;
using hogak::input::ReaderSnapshot;

struct PairRecord {
    std::string left_path;
    std::string right_path;
    BufferedFrameInfo left_info;
    BufferedFrameInfo right_info;
    std::string time_domain;
    double delta_ms = 0.0;
};

int read_int_arg(int argc, char** argv, const char* key, int fallback) {
    for (int i = 1; i < argc - 1; ++i) {
        if (std::strcmp(argv[i], key) == 0) {
            return std::atoi(argv[i + 1]);
        }
    }
    return fallback;
}

double read_double_arg(int argc, char** argv, const char* key, double fallback) {
    for (int i = 1; i < argc - 1; ++i) {
        if (std::strcmp(argv[i], key) == 0) {
            return std::atof(argv[i + 1]);
        }
    }
    return fallback;
}

const char* read_string_arg(int argc, char** argv, const char* key, const char* fallback) {
    for (int i = 1; i < argc - 1; ++i) {
        if (std::strcmp(argv[i], key) == 0) {
            return argv[i + 1];
        }
    }
    return fallback;
}

bool has_flag(int argc, char** argv, const char* key) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], key) == 0) {
            return true;
        }
    }
    return false;
}

void print_help() {
    std::cout
        << "stitch_capture\n"
        << "  --help             Show help\n"
        << "  --left-url URL     Left RTSP URL\n"
        << "  --right-url URL    Right RTSP URL\n"
        << "  --output-dir PATH  Directory for paired PNG frames and capture manifest\n"
        << "  --clip-frames N    Number of frame pairs to capture\n"
        << "  --warmup-frames N  Frames to observe before pairing begins\n"
        << "  --ffmpeg-bin PATH  ffmpeg.exe path\n"
        << "  --input-runtime M  ffmpeg-cpu or ffmpeg-cuda\n"
        << "  --input-pipe-format M  bgr24 or nv12\n"
        << "  --width N          Input width (default 1920)\n"
        << "  --height N         Input height (default 1080)\n"
        << "  --transport M      RTSP transport (default tcp)\n"
        << "  --input-buffer-frames N  Max buffered frames per RTSP reader\n"
        << "  --video-codec C    h264 or hevc (default h264)\n"
        << "  --timeout-sec N    Reader timeout per stream\n"
        << "  --reconnect-cooldown-sec N  Reader reconnect cooldown\n"
        << "  --sync-pair-mode M none/latest/oldest/service\n"
        << "  --sync-match-max-delta-ms N  Max accepted skew for stored pairs\n"
        << "  --sync-time-source M pts-offset-auto/pts-offset-manual/pts-offset-hybrid/arrival/wallclock\n"
        << "  --sync-manual-offset-ms N    Manual right-stream offset for pts pairing\n"
        << "  --disable-freeze-detection   Disable freeze detection in readers\n";
}

std::int64_t now_ns() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        clock::now().time_since_epoch()).count();
}

std::int64_t unix_epoch_sec() {
    using clock = std::chrono::system_clock;
    return std::chrono::duration_cast<std::chrono::seconds>(
        clock::now().time_since_epoch()).count();
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (const char ch : value) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\b':
                out << "\\b";
                break;
            case '\f':
                out << "\\f";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    out << "\\u"
                        << std::hex
                        << std::setw(4)
                        << std::setfill('0')
                        << static_cast<int>(static_cast<unsigned char>(ch))
                        << std::dec
                        << std::setfill(' ');
                } else {
                    out << ch;
                }
                break;
        }
    }
    return out.str();
}

FrameTimeDomain resolve_pair_time_domain(
    const std::string& requested_time_source,
    const ReaderSnapshot& left_snapshot,
    const ReaderSnapshot& right_snapshot) {
    const bool left_has_wallclock = left_snapshot.source_comparable_frames > 0;
    const bool right_has_wallclock = right_snapshot.source_comparable_frames > 0;
    const bool left_has_source_pts = left_snapshot.source_valid_frames > 0;
    const bool right_has_source_pts = right_snapshot.source_valid_frames > 0;

    if (requested_time_source == "arrival") {
        return FrameTimeDomain::kArrival;
    }
    if (requested_time_source == "wallclock") {
        return (left_has_wallclock && right_has_wallclock)
            ? FrameTimeDomain::kSourceWallclock
            : FrameTimeDomain::kArrival;
    }
    if (requested_time_source == "pts-offset-manual") {
        return (left_has_source_pts && right_has_source_pts)
            ? FrameTimeDomain::kSourcePtsOffset
            : FrameTimeDomain::kArrival;
    }
    if (requested_time_source == "pts-offset-auto" || requested_time_source == "pts-offset-hybrid") {
        return (left_has_wallclock && right_has_wallclock)
            ? FrameTimeDomain::kSourceComparable
            : FrameTimeDomain::kArrival;
    }
    return (left_has_wallclock && right_has_wallclock)
        ? FrameTimeDomain::kSourceComparable
        : FrameTimeDomain::kArrival;
}

bool copy_anchor_frame(
    const FfmpegRtspReader& reader,
    const std::string& pair_mode,
    cv::Mat* frame_out,
    BufferedFrameInfo* info_out) {
    if (pair_mode == "oldest") {
        return reader.copy_oldest_frame(frame_out, nullptr, nullptr, info_out);
    }
    return reader.copy_latest_frame(frame_out, nullptr, nullptr, info_out);
}

std::string build_snapshot_error(const char* side, const ReaderSnapshot& snapshot) {
    std::ostringstream out;
    out << side << " reader has_frame=" << (snapshot.has_frame ? "true" : "false")
        << " buffered_frames=" << snapshot.buffered_frames
        << " frames_total=" << snapshot.frames_total;
    if (!snapshot.last_error.empty()) {
        out << " last_error=" << snapshot.last_error;
    }
    return out.str();
}

}  // namespace

int main(int argc, char** argv) {
    if (has_flag(argc, argv, "--help")) {
        print_help();
        return 0;
    }
    if (std::strcmp(read_string_arg(argc, argv, "--left-url", ""), "") == 0 ||
        std::strcmp(read_string_arg(argc, argv, "--right-url", ""), "") == 0) {
        std::cerr << "left/right RTSP URLs are required\n";
        return 2;
    }
    const std::filesystem::path output_dir = read_string_arg(argc, argv, "--output-dir", "");
    if (output_dir.empty()) {
        std::cerr << "output-dir is required\n";
        return 2;
    }

    std::filesystem::create_directories(output_dir);

    hogak::engine::StreamConfig left_config{};
    hogak::engine::StreamConfig right_config{};
    left_config.name = "left";
    right_config.name = "right";
    left_config.url = read_string_arg(argc, argv, "--left-url", "");
    right_config.url = read_string_arg(argc, argv, "--right-url", "");
    left_config.transport = read_string_arg(argc, argv, "--transport", "tcp");
    right_config.transport = left_config.transport;
    left_config.video_codec = read_string_arg(argc, argv, "--video-codec", "h264");
    right_config.video_codec = left_config.video_codec;
    left_config.input_pipe_format = read_string_arg(argc, argv, "--input-pipe-format", "nv12");
    right_config.input_pipe_format = left_config.input_pipe_format;
    left_config.width = read_int_arg(argc, argv, "--width", 1920);
    left_config.height = read_int_arg(argc, argv, "--height", 1080);
    right_config.width = left_config.width;
    right_config.height = left_config.height;
    left_config.max_buffered_frames = read_int_arg(argc, argv, "--input-buffer-frames", 8);
    right_config.max_buffered_frames = left_config.max_buffered_frames;
    left_config.enable_freeze_detection = !has_flag(argc, argv, "--disable-freeze-detection");
    right_config.enable_freeze_detection = left_config.enable_freeze_detection;
    left_config.timeout_sec = read_double_arg(argc, argv, "--timeout-sec", 10.0);
    right_config.timeout_sec = left_config.timeout_sec;
    left_config.reconnect_cooldown_sec = read_double_arg(argc, argv, "--reconnect-cooldown-sec", 1.0);
    right_config.reconnect_cooldown_sec = left_config.reconnect_cooldown_sec;

    const std::string ffmpeg_bin = read_string_arg(argc, argv, "--ffmpeg-bin", "");
    const std::string input_runtime = read_string_arg(argc, argv, "--input-runtime", "ffmpeg-cuda");
    const std::string pair_mode = read_string_arg(argc, argv, "--sync-pair-mode", "service");
    const std::string requested_time_source = read_string_arg(argc, argv, "--sync-time-source", "pts-offset-auto");
    const double max_delta_ms = std::max(0.0, read_double_arg(argc, argv, "--sync-match-max-delta-ms", 35.0));
    const std::int64_t manual_offset_ns = static_cast<std::int64_t>(
        read_double_arg(argc, argv, "--sync-manual-offset-ms", 0.0) * 1'000'000.0);
    const int clip_frames = std::max(1, read_int_arg(argc, argv, "--clip-frames", 5));
    const int warmup_frames = std::max(0, read_int_arg(argc, argv, "--warmup-frames", 2));
    const std::int64_t deadline_ns =
        now_ns() + static_cast<std::int64_t>(std::max(4.0, left_config.timeout_sec * 2.0) * 1'000'000'000.0);

    FfmpegRtspReader left_reader;
    FfmpegRtspReader right_reader;
    if (!left_reader.start(left_config, ffmpeg_bin, input_runtime, true)) {
        std::cerr << "failed to start left native reader\n";
        return 2;
    }
    if (!right_reader.start(right_config, ffmpeg_bin, input_runtime, true)) {
        left_reader.stop();
        std::cerr << "failed to start right native reader\n";
        return 2;
    }

    auto stop_readers = [&]() {
        right_reader.stop();
        left_reader.stop();
    };

    while (now_ns() < deadline_ns) {
        const ReaderSnapshot left_snapshot = left_reader.snapshot();
        const ReaderSnapshot right_snapshot = right_reader.snapshot();
        const bool left_ready = left_snapshot.has_frame && left_snapshot.frames_total >= warmup_frames;
        const bool right_ready = right_snapshot.has_frame && right_snapshot.frames_total >= warmup_frames;
        if (left_ready && right_ready) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    const ReaderSnapshot warmed_left = left_reader.snapshot();
    const ReaderSnapshot warmed_right = right_reader.snapshot();
    if (!warmed_left.has_frame || !warmed_right.has_frame) {
        std::cerr
            << "failed to warm native readers: "
            << build_snapshot_error("left", warmed_left) << "; "
            << build_snapshot_error("right", warmed_right) << "\n";
        stop_readers();
        return 2;
    }

    const FrameTimeDomain resolved_time_domain =
        resolve_pair_time_domain(requested_time_source, warmed_left, warmed_right);
    const std::string resolved_time_domain_name = hogak::input::frame_time_domain_name(resolved_time_domain);

    std::vector<PairRecord> pairs;
    pairs.reserve(static_cast<std::size_t>(clip_frames));
    std::int64_t last_left_seq = -1;
    double delta_sum_ms = 0.0;
    double delta_worst_ms = 0.0;

    for (int index = 0; index < clip_frames; ++index) {
        bool stored = false;
        while (now_ns() < deadline_ns) {
            cv::Mat left_frame;
            cv::Mat right_frame;
            BufferedFrameInfo left_info;
            BufferedFrameInfo right_info;
            if (!copy_anchor_frame(left_reader, pair_mode, &left_frame, &left_info)) {
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                continue;
            }
            if (left_info.seq <= last_left_seq) {
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                continue;
            }

            FrameTimeDomain pair_time_domain = resolved_time_domain;
            if (!left_info.has_time(pair_time_domain)) {
                pair_time_domain = FrameTimeDomain::kArrival;
            }
            std::int64_t target_time_ns = left_info.resolve_time_ns(pair_time_domain);
            if (pair_time_domain == FrameTimeDomain::kSourcePtsOffset) {
                target_time_ns += manual_offset_ns;
            }
            if (!right_reader.copy_closest_frame(
                    target_time_ns,
                    true,
                    &right_frame,
                    nullptr,
                    nullptr,
                    pair_time_domain,
                    &right_info)) {
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                continue;
            }

            std::int64_t right_time_ns = right_info.resolve_time_ns(pair_time_domain);
            if (right_time_ns <= 0) {
                pair_time_domain = FrameTimeDomain::kArrival;
                target_time_ns = left_info.resolve_time_ns(pair_time_domain);
                right_time_ns = right_info.resolve_time_ns(pair_time_domain);
            }
            const double delta_ms = static_cast<double>(std::llabs(right_time_ns - target_time_ns)) / 1'000'000.0;
            if (max_delta_ms > 0.0 && delta_ms > max_delta_ms) {
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                continue;
            }

            std::ostringstream left_name;
            left_name << "left_" << std::setw(3) << std::setfill('0') << index << ".png";
            std::ostringstream right_name;
            right_name << "right_" << std::setw(3) << std::setfill('0') << index << ".png";
            const std::filesystem::path left_path = output_dir / left_name.str();
            const std::filesystem::path right_path = output_dir / right_name.str();
            if (!cv::imwrite(left_path.string(), left_frame) || !cv::imwrite(right_path.string(), right_frame)) {
                std::cerr << "failed to write native capture frames to disk\n";
                stop_readers();
                return 2;
            }

            left_info.frame.release();
            right_info.frame.release();
            pairs.push_back(PairRecord{
                left_name.str(),
                right_name.str(),
                left_info,
                right_info,
                hogak::input::frame_time_domain_name(pair_time_domain),
                delta_ms,
            });
            delta_sum_ms += delta_ms;
            delta_worst_ms = std::max(delta_worst_ms, delta_ms);
            last_left_seq = left_info.seq;
            stored = true;
            break;
        }
        if (!stored) {
            std::cerr << "timed out while collecting native frame pairs\n";
            stop_readers();
            return 2;
        }
    }

    stop_readers();

    const double delta_mean_ms =
        pairs.empty() ? 0.0 : (delta_sum_ms / static_cast<double>(pairs.size()));
    const std::filesystem::path manifest_path = output_dir / "capture_manifest.json";
    std::ofstream manifest(manifest_path, std::ios::binary);
    if (!manifest) {
        std::cerr << "failed to create native capture manifest\n";
        return 2;
    }

    manifest << "{\n";
    manifest << "  \"version\": 1,\n";
    manifest << "  \"format\": \"native_paired_capture\",\n";
    manifest << "  \"created_at_epoch_sec\": " << unix_epoch_sec() << ",\n";
    manifest << "  \"output_dir\": \"" << json_escape(output_dir.string()) << "\",\n";
    manifest << "  \"pairing\": {\n";
    manifest << "    \"pair_mode\": \"" << json_escape(pair_mode) << "\",\n";
    manifest << "    \"requested_time_source\": \"" << json_escape(requested_time_source) << "\",\n";
    manifest << "    \"resolved_time_domain\": \"" << json_escape(resolved_time_domain_name) << "\",\n";
    manifest << "    \"max_delta_ms\": " << std::fixed << std::setprecision(3) << max_delta_ms << ",\n";
    manifest << "    \"mean_delta_ms\": " << std::fixed << std::setprecision(3) << delta_mean_ms << ",\n";
    manifest << "    \"worst_delta_ms\": " << std::fixed << std::setprecision(3) << delta_worst_ms << "\n";
    manifest << "  },\n";
    manifest << "  \"frames\": [\n";
    for (std::size_t index = 0; index < pairs.size(); ++index) {
        const PairRecord& pair = pairs[index];
        manifest << "    {\n";
        manifest << "      \"index\": " << index << ",\n";
        manifest << "      \"left_path\": \"" << json_escape(pair.left_path) << "\",\n";
        manifest << "      \"right_path\": \"" << json_escape(pair.right_path) << "\",\n";
        manifest << "      \"time_domain\": \"" << json_escape(pair.time_domain) << "\",\n";
        manifest << "      \"pair_delta_ms\": " << std::fixed << std::setprecision(3) << pair.delta_ms << ",\n";
        manifest << "      \"left\": {\n";
        manifest << "        \"seq\": " << pair.left_info.seq << ",\n";
        manifest << "        \"arrival_timestamp_ns\": " << pair.left_info.arrival_timestamp_ns << ",\n";
        manifest << "        \"source_pts_ns\": " << pair.left_info.source_pts_ns << ",\n";
        manifest << "        \"source_wallclock_ns\": " << pair.left_info.source_wallclock_ns << "\n";
        manifest << "      },\n";
        manifest << "      \"right\": {\n";
        manifest << "        \"seq\": " << pair.right_info.seq << ",\n";
        manifest << "        \"arrival_timestamp_ns\": " << pair.right_info.arrival_timestamp_ns << ",\n";
        manifest << "        \"source_pts_ns\": " << pair.right_info.source_pts_ns << ",\n";
        manifest << "        \"source_wallclock_ns\": " << pair.right_info.source_wallclock_ns << "\n";
        manifest << "      }\n";
        manifest << "    }";
        if (index + 1 < pairs.size()) {
            manifest << ",";
        }
        manifest << "\n";
    }
    manifest << "  ]\n";
    manifest << "}\n";

    std::cout
        << "{"
        << "\"status\":\"ok\","
        << "\"manifest_path\":\"" << json_escape(manifest_path.string()) << "\","
        << "\"clip_frame_count\":" << pairs.size()
        << "}\n";
    return 0;
}
