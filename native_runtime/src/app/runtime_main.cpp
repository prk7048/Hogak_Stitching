#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#endif

#include "control/control_server.h"
#include "engine/engine_config.h"
#include "engine/stitch_engine.h"
#include "output/gpu_direct_support.h"

namespace {

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
        << "stitch_runtime\n"
        << "  --help             Show help\n"
        << "  --emit-hello       Emit hello event on startup\n"
        << "  --once             Emit startup events and exit\n"
        << "  --heartbeat-ms N   Emit metrics every N ms while running\n"
        << "  --left-url URL     Left RTSP URL\n"
        << "  --right-url URL    Right RTSP URL\n"
        << "  --ffmpeg-bin PATH  ffmpeg.exe path\n"
        << "  --input-runtime M  ffmpeg-cpu or ffmpeg-cuda\n"
        << "  --input-pipe-format M  bgr24 or nv12\n"
        << "  --homography-file P  Path to fixed 3x3 homography file\n"
        << "  --width N          Input width (default 1920)\n"
        << "  --height N         Input height (default 1080)\n"
        << "  --transport M      RTSP transport (default tcp)\n"
        << "  --input-buffer-frames N  Max buffered frames per RTSP reader\n"
        << "  --video-codec C    h264 or hevc (default h264)\n"
        << "  --probe-output-runtime M none, ffmpeg, or gpu-direct\n"
        << "  --probe-output-target U  Encoded probe output target (udp/rtsp/rtmp/file)\n"
        << "  --probe-output-codec C   Probe output codec (default h264_nvenc)\n"
        << "  --probe-output-bitrate B Probe output bitrate (default 12M)\n"
        << "  --probe-output-preset P  Probe output preset (default p4)\n"
        << "  --probe-output-muxer M   Optional explicit probe muxer\n"
        << "  --probe-output-width N   Force encoded probe output width\n"
        << "  --probe-output-height N  Force encoded probe output height\n"
        << "  --probe-output-fps N     Force encoded probe output fps\n"
        << "  --probe-output-debug-overlay  Burn debug overlay into local probe output\n"
        << "  --transmit-output-runtime M none, ffmpeg, or gpu-direct\n"
        << "  --transmit-output-target U  Production encoded output target\n"
        << "  --transmit-output-codec C   Production output codec\n"
        << "  --transmit-output-bitrate B Production output bitrate\n"
        << "  --transmit-output-preset P  Production output preset\n"
        << "  --transmit-output-muxer M   Production output muxer\n"
        << "  --transmit-output-width N   Force production encoded output width\n"
        << "  --transmit-output-height N  Force production encoded output height\n"
        << "  --transmit-output-fps N     Force production encoded output fps\n"
        << "  --transmit-output-debug-overlay  Burn debug overlay into transmit output\n"
        << "  --sync-pair-mode M none/latest/oldest/service\n"
        << "  --allow-frame-reuse  Allow reuse of one-side stale pair for smoother output\n"
        << "  --pair-reuse-max-age-ms N  Max stale age allowed for one-side reuse\n"
        << "  --pair-reuse-max-consecutive N  Max consecutive one-side reuses\n"
        << "  --sync-match-max-delta-ms N  Pairing skew threshold\n"
        << "  --sync-manual-offset-ms N    Manual right-stream offset\n"
        << "  --stitch-output-scale N      Runtime stitch/output scale\n"
        << "  --stitch-every-n N           Stitch every N selected pairs\n"
        << "  --gpu-mode M      off/auto/on\n"
        << "  --gpu-device N    CUDA device index\n"
        << "  --print-gpu-direct-status  Print gpu-direct dependency status and exit\n"
        << "  --headless-benchmark  Enable benchmark mode metadata\n";
}

bool has_pending_stdin_data() {
#ifdef _WIN32
    const HANDLE stdin_handle = GetStdHandle(STD_INPUT_HANDLE);
    if (stdin_handle == nullptr || stdin_handle == INVALID_HANDLE_VALUE) {
        return false;
    }
    DWORD available = 0;
    if (PeekNamedPipe(stdin_handle, nullptr, 0, nullptr, &available, nullptr)) {
        return available > 0;
    }
    return false;
#else
    return std::cin.rdbuf()->in_avail() > 0;
#endif
}

}  // namespace

int main(int argc, char** argv) {
    if (has_flag(argc, argv, "--help")) {
        print_help();
        return 0;
    }
    if (has_flag(argc, argv, "--print-gpu-direct-status")) {
        const std::string ffmpeg_dev_root = hogak::output::gpu_direct_ffmpeg_dev_root();
        std::cout
            << "{"
            << "\"provider\":\"" << hogak::output::gpu_direct_provider() << "\","
            << "\"dependency_ready\":" << (hogak::output::gpu_direct_dependency_ready() ? "true" : "false") << ","
            << "\"status\":\"" << hogak::output::gpu_direct_dependency_status() << "\","
            << "\"ffmpeg_dev_root\":\"" << ffmpeg_dev_root << "\""
            << "}\n";
        return 0;
    }

    const bool emit_hello = has_flag(argc, argv, "--emit-hello");
    const bool once = has_flag(argc, argv, "--once");
    const int heartbeat_ms = read_int_arg(argc, argv, "--heartbeat-ms", 1000);

    hogak::engine::EngineConfig config{};
    config.gpu_mode = "on";
    config.input_runtime = read_string_arg(argc, argv, "--input-runtime", "ffmpeg-cuda");
    config.ffmpeg_bin = read_string_arg(argc, argv, "--ffmpeg-bin", "");
    config.homography_file = read_string_arg(argc, argv, "--homography-file", "");
    config.left.name = "left";
    config.right.name = "right";
    config.left.url = read_string_arg(argc, argv, "--left-url", "");
    config.right.url = read_string_arg(argc, argv, "--right-url", "");
    config.left.transport = read_string_arg(argc, argv, "--transport", "tcp");
    config.right.transport = config.left.transport;
    config.left.video_codec = read_string_arg(argc, argv, "--video-codec", "h264");
    config.right.video_codec = config.left.video_codec;
    config.left.input_pipe_format = read_string_arg(argc, argv, "--input-pipe-format", "nv12");
    config.right.input_pipe_format = config.left.input_pipe_format;
    config.left.width = read_int_arg(argc, argv, "--width", 1920);
    config.left.height = read_int_arg(argc, argv, "--height", 1080);
    config.right.width = config.left.width;
    config.right.height = config.left.height;
    config.left.max_buffered_frames = read_int_arg(argc, argv, "--input-buffer-frames", 8);
    config.right.max_buffered_frames = config.left.max_buffered_frames;
    config.left.enable_freeze_detection = !has_flag(argc, argv, "--disable-freeze-detection");
    config.right.enable_freeze_detection = config.left.enable_freeze_detection;
    config.left.timeout_sec = read_double_arg(argc, argv, "--timeout-sec", 10.0);
    config.right.timeout_sec = config.left.timeout_sec;
    config.left.reconnect_cooldown_sec = read_double_arg(argc, argv, "--reconnect-cooldown-sec", 1.0);
    config.right.reconnect_cooldown_sec = config.left.reconnect_cooldown_sec;
    config.output.runtime = read_string_arg(argc, argv, "--probe-output-runtime", "none");
    config.output.profile = "inspection";
    config.output.target = read_string_arg(argc, argv, "--probe-output-target", "");
    config.output.codec = read_string_arg(argc, argv, "--probe-output-codec", "h264_nvenc");
    config.output.bitrate = read_string_arg(argc, argv, "--probe-output-bitrate", "12M");
    config.output.preset = read_string_arg(argc, argv, "--probe-output-preset", "p4");
    config.output.muxer = read_string_arg(argc, argv, "--probe-output-muxer", "");
    config.output.width = read_int_arg(argc, argv, "--probe-output-width", 0);
    config.output.height = read_int_arg(argc, argv, "--probe-output-height", 0);
    config.output.fps = read_double_arg(argc, argv, "--probe-output-fps", 30.0);
    config.output.debug_overlay = has_flag(argc, argv, "--probe-output-debug-overlay");
    config.production_output.runtime = read_string_arg(argc, argv, "--transmit-output-runtime", "none");
    config.production_output.profile = "production-compatible";
    config.production_output.target = read_string_arg(argc, argv, "--transmit-output-target", "");
    config.production_output.codec = read_string_arg(argc, argv, "--transmit-output-codec", "h264_nvenc");
    config.production_output.bitrate = read_string_arg(argc, argv, "--transmit-output-bitrate", "12M");
    config.production_output.preset = read_string_arg(argc, argv, "--transmit-output-preset", "p4");
    config.production_output.muxer = read_string_arg(argc, argv, "--transmit-output-muxer", "");
    config.production_output.width = read_int_arg(argc, argv, "--transmit-output-width", 0);
    config.production_output.height = read_int_arg(argc, argv, "--transmit-output-height", 0);
    config.production_output.fps = read_double_arg(argc, argv, "--transmit-output-fps", 30.0);
    config.production_output.debug_overlay = has_flag(argc, argv, "--transmit-output-debug-overlay");
    config.sync_pair_mode = read_string_arg(argc, argv, "--sync-pair-mode", "none");
    config.allow_frame_reuse = has_flag(argc, argv, "--allow-frame-reuse");
    config.pair_reuse_max_age_ms = read_double_arg(argc, argv, "--pair-reuse-max-age-ms", 90.0);
    config.pair_reuse_max_consecutive = read_int_arg(argc, argv, "--pair-reuse-max-consecutive", 2);
    config.sync_match_max_delta_ms = read_double_arg(argc, argv, "--sync-match-max-delta-ms", 35.0);
    config.sync_manual_offset_ms = read_double_arg(argc, argv, "--sync-manual-offset-ms", 0.0);
    config.stitch_output_scale = read_double_arg(argc, argv, "--stitch-output-scale", 1.0);
    config.stitch_every_n = read_int_arg(argc, argv, "--stitch-every-n", 1);
    config.gpu_mode = read_string_arg(argc, argv, "--gpu-mode", "on");
    config.gpu_device = read_int_arg(argc, argv, "--gpu-device", 0);
    config.headless_benchmark = has_flag(argc, argv, "--headless-benchmark");

    hogak::engine::StitchEngine engine;
    if (!engine.start(config)) {
        std::cerr << "{\"seq\":0,\"type\":\"fatal\",\"payload\":{\"message\":\"engine start failed\"}}\n";
        return 2;
    }

    hogak::control::ControlServer control(std::cin, std::cout);
    if (emit_hello) {
        control.emit_hello();
    }
    control.emit_metrics(1, engine);

    if (once) {
        engine.stop();
        return 0;
    }

    std::int64_t seq = 2;
    using clock = std::chrono::steady_clock;
    auto next_emit = clock::now() + std::chrono::milliseconds(heartbeat_ms);

    while (engine.running()) {
        engine.tick();

        if (has_pending_stdin_data()) {
            if (!control.process_one_command(engine)) {
                break;
            }
        }

        const auto now = clock::now();
        if (now >= next_emit) {
            control.emit_metrics(seq++, engine);
            next_emit = now + std::chrono::milliseconds(heartbeat_ms);
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }

    return 0;
}
