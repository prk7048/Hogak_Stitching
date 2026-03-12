#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#endif

#include "control/control_server.h"
#include "engine/engine_config.h"
#include "engine/stitch_engine.h"

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
        << "  --homography-file P  Path to fixed 3x3 homography file\n"
        << "  --width N          Input width (default 1920)\n"
        << "  --height N         Input height (default 1080)\n"
        << "  --transport M      RTSP transport (default tcp)\n"
        << "  --video-codec C    h264 or hevc (default h264)\n"
        << "  --output-runtime M none or ffmpeg\n"
        << "  --output-target U  Encoded output target (udp/rtsp/rtmp/file)\n"
        << "  --output-codec C   Output codec (default h264_nvenc)\n"
        << "  --output-bitrate B Output bitrate (default 12M)\n"
        << "  --output-preset P  Output preset (default p4)\n"
        << "  --output-muxer M   Optional explicit muxer\n"
        << "  --output-width N   Force encoded output width\n"
        << "  --output-height N  Force encoded output height\n"
        << "  --sync-pair-mode M none/latest/oldest\n"
        << "  --sync-match-max-delta-ms N  Pairing skew threshold\n"
        << "  --sync-manual-offset-ms N    Manual right-stream offset\n"
        << "  --stitch-output-scale N      Runtime stitch/output scale\n"
        << "  --stitch-every-n N           Stitch every N selected pairs\n"
        << "  --gpu-mode M      off/auto/on\n"
        << "  --gpu-device N    CUDA device index\n"
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
    config.left.width = read_int_arg(argc, argv, "--width", 1920);
    config.left.height = read_int_arg(argc, argv, "--height", 1080);
    config.right.width = config.left.width;
    config.right.height = config.left.height;
    config.left.timeout_sec = read_double_arg(argc, argv, "--timeout-sec", 10.0);
    config.right.timeout_sec = config.left.timeout_sec;
    config.left.reconnect_cooldown_sec = read_double_arg(argc, argv, "--reconnect-cooldown-sec", 1.0);
    config.right.reconnect_cooldown_sec = config.left.reconnect_cooldown_sec;
    config.output.runtime = read_string_arg(argc, argv, "--output-runtime", "none");
    config.output.target = read_string_arg(argc, argv, "--output-target", "");
    config.output.codec = read_string_arg(argc, argv, "--output-codec", "h264_nvenc");
    config.output.bitrate = read_string_arg(argc, argv, "--output-bitrate", "12M");
    config.output.preset = read_string_arg(argc, argv, "--output-preset", "p4");
    config.output.muxer = read_string_arg(argc, argv, "--output-muxer", "");
    config.output.width = read_int_arg(argc, argv, "--output-width", 0);
    config.output.height = read_int_arg(argc, argv, "--output-height", 0);
    config.sync_pair_mode = read_string_arg(argc, argv, "--sync-pair-mode", "none");
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
