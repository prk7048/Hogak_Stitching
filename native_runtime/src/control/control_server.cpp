#include "control/control_server.h"

#include <chrono>
#include <string>

#include "control/json_line_protocol.h"
#include "engine/stitch_engine.h"

namespace hogak::control {

namespace {

double now_sec() {
    using clock = std::chrono::steady_clock;
    const auto now = clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::duration<double>>(now).count();
}

bool extract_json_string(const std::string& line, const char* key, std::string* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const std::string quoted_key = "\"" + std::string(key) + "\"";
    const auto key_pos = line.find(quoted_key);
    if (key_pos == std::string::npos) {
        return false;
    }
    auto colon_pos = line.find(':', key_pos + quoted_key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    auto quote_pos = line.find('"', colon_pos + 1);
    if (quote_pos == std::string::npos) {
        return false;
    }

    std::string result;
    for (std::size_t pos = quote_pos + 1; pos < line.size(); ++pos) {
        const char ch = line[pos];
        if (ch == '\\' && pos + 1 < line.size()) {
            result.push_back(line[pos + 1]);
            pos += 1;
            continue;
        }
        if (ch == '"') {
            *value_out = result;
            return true;
        }
        result.push_back(ch);
    }
    return false;
}

bool extract_json_number(const std::string& line, const char* key, double* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const std::string quoted_key = "\"" + std::string(key) + "\"";
    const auto key_pos = line.find(quoted_key);
    if (key_pos == std::string::npos) {
        return false;
    }
    auto colon_pos = line.find(':', key_pos + quoted_key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    auto start_pos = line.find_first_of("-0123456789.", colon_pos + 1);
    if (start_pos == std::string::npos) {
        return false;
    }
    auto end_pos = line.find_first_not_of("0123456789.-", start_pos);
    const auto token = line.substr(start_pos, end_pos - start_pos);
    try {
        *value_out = std::stod(token);
        return true;
    } catch (...) {
        return false;
    }
}

bool extract_json_bool(const std::string& line, const char* key, bool* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const std::string quoted_key = "\"" + std::string(key) + "\"";
    const auto key_pos = line.find(quoted_key);
    if (key_pos == std::string::npos) {
        return false;
    }
    auto colon_pos = line.find(':', key_pos + quoted_key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    const auto true_pos = line.find("true", colon_pos + 1);
    if (true_pos != std::string::npos && true_pos < line.find_first_of(",}", colon_pos + 1)) {
        *value_out = true;
        return true;
    }
    const auto false_pos = line.find("false", colon_pos + 1);
    if (false_pos != std::string::npos && false_pos < line.find_first_of(",}", colon_pos + 1)) {
        *value_out = false;
        return true;
    }
    return false;
}

std::string status_event_json(double timestamp_sec, const std::string& message) {
    std::ostringstream out;
    out << "{\"seq\":0,\"type\":\"status\",\"timestamp_sec\":" << timestamp_sec
        << ",\"payload\":{\"message\":\"" << json_escape(message) << "\"}}";
    return out.str();
}

}  // namespace

ControlServer::ControlServer(std::istream& input, std::ostream& output)
    : input_(input), output_(output) {}

void ControlServer::emit_hello() {
    output_ << hello_event_json(now_sec()) << '\n';
    output_.flush();
}

void ControlServer::emit_metrics(std::int64_t seq, const hogak::engine::StitchEngine& engine) {
    output_ << metrics_event_json(seq, now_sec(), engine.snapshot_metrics()) << '\n';
    output_.flush();
}

bool ControlServer::process_one_command(hogak::engine::StitchEngine& engine) {
    std::string line;
    if (!std::getline(input_, line)) {
        return false;
    }

    if (command_type_is(line, "shutdown") || command_type_is(line, "stop")) {
        engine.stop();
        output_ << "{\"seq\":0,\"type\":\"stopped\",\"timestamp_sec\":" << now_sec() << ",\"payload\":{}}\n";
        output_.flush();
        return false;
    }

    if (command_type_is(line, "request_snapshot")) {
        emit_metrics(0, engine);
        return true;
    }

    if (command_type_is(line, "reload_config")) {
        auto config = engine.current_config();
        std::string text_value;
        double number_value = 0.0;
        bool bool_value = false;

        if (extract_json_string(line, "left_rtsp", &text_value)) {
            config.left.url = text_value;
        }
        if (extract_json_string(line, "right_rtsp", &text_value)) {
            config.right.url = text_value;
        }
        if (extract_json_string(line, "input_runtime", &text_value)) {
            config.input_runtime = text_value;
        }
        if (extract_json_string(line, "ffmpeg_bin", &text_value)) {
            config.ffmpeg_bin = text_value;
        }
        if (extract_json_string(line, "homography_file", &text_value)) {
            config.homography_file = text_value;
        }
        if (extract_json_string(line, "output_runtime", &text_value)) {
            config.output.runtime = text_value;
        }
        if (extract_json_string(line, "target", &text_value)) {
            config.output.target = text_value;
        }
        if (extract_json_string(line, "output_target", &text_value)) {
            config.output.target = text_value;
        }
        if (extract_json_string(line, "codec", &text_value)) {
            config.output.codec = text_value;
        }
        if (extract_json_string(line, "output_codec", &text_value)) {
            config.output.codec = text_value;
        }
        if (extract_json_string(line, "bitrate", &text_value)) {
            config.output.bitrate = text_value;
        }
        if (extract_json_string(line, "output_bitrate", &text_value)) {
            config.output.bitrate = text_value;
        }
        if (extract_json_string(line, "preset", &text_value)) {
            config.output.preset = text_value;
        }
        if (extract_json_string(line, "output_preset", &text_value)) {
            config.output.preset = text_value;
        }
        if (extract_json_string(line, "muxer", &text_value)) {
            config.output.muxer = text_value;
        }
        if (extract_json_string(line, "output_muxer", &text_value)) {
            config.output.muxer = text_value;
        }
        if (extract_json_string(line, "rtsp_transport", &text_value) || extract_json_string(line, "transport", &text_value)) {
            config.left.transport = text_value;
            config.right.transport = text_value;
        }
        if (extract_json_string(line, "sync_pair_mode", &text_value)) {
            config.sync_pair_mode = text_value;
        }
        if (extract_json_string(line, "gpu_mode", &text_value)) {
            config.gpu_mode = text_value;
        }
        if (extract_json_number(line, "rtsp_timeout_sec", &number_value) || extract_json_number(line, "timeout_sec", &number_value)) {
            config.left.timeout_sec = number_value;
            config.right.timeout_sec = number_value;
        }
        if (extract_json_number(line, "reconnect_cooldown_sec", &number_value)) {
            config.left.reconnect_cooldown_sec = number_value;
            config.right.reconnect_cooldown_sec = number_value;
        }
        if (extract_json_number(line, "sync_match_max_delta_ms", &number_value)) {
            config.sync_match_max_delta_ms = number_value;
        }
        if (extract_json_number(line, "sync_manual_offset_ms", &number_value)) {
            config.sync_manual_offset_ms = number_value;
        }
        if (extract_json_number(line, "process_scale", &number_value)) {
            config.process_scale = number_value;
        }
        if (extract_json_number(line, "stitch_output_scale", &number_value)) {
            config.stitch_output_scale = number_value;
        }
        if (extract_json_number(line, "stitch_every_n", &number_value)) {
            config.stitch_every_n = static_cast<std::int32_t>(number_value);
        }
        if (extract_json_number(line, "gpu_device", &number_value)) {
            config.gpu_device = static_cast<std::int32_t>(number_value);
        }
        if (extract_json_number(line, "benchmark_log_interval_sec", &number_value)) {
            config.benchmark_log_interval_sec = number_value;
        }
        if (extract_json_number(line, "output_width", &number_value)) {
            config.output.width = static_cast<std::int32_t>(number_value);
        }
        if (extract_json_number(line, "output_height", &number_value)) {
            config.output.height = static_cast<std::int32_t>(number_value);
        }
        if (extract_json_bool(line, "headless_benchmark", &bool_value)) {
            config.headless_benchmark = bool_value;
        }

        if (engine.reload_config(config)) {
            output_ << status_event_json(now_sec(), "config reloaded") << '\n';
        } else {
            output_ << "{\"seq\":0,\"type\":\"warning\",\"timestamp_sec\":" << now_sec()
                    << ",\"payload\":{\"message\":\"config reload failed\"}}\n";
        }
        output_.flush();
        return true;
    }

    if (command_type_is(line, "reset_auto_calibration") || command_type_is(line, "reload_homography")) {
        auto config = engine.current_config();
        std::string text_value;
        if (extract_json_string(line, "homography_file", &text_value)) {
            config.homography_file = text_value;
            if (!engine.reload_config(config)) {
                output_ << "{\"seq\":0,\"type\":\"warning\",\"timestamp_sec\":" << now_sec()
                        << ",\"payload\":{\"message\":\"calibration reload failed\"}}\n";
                output_.flush();
                return true;
            }
        } else {
            engine.reset_calibration();
        }
        output_ << status_event_json(now_sec(), "calibration reset") << '\n';
        output_.flush();
        return true;
    }

    output_ << "{\"seq\":0,\"type\":\"warning\",\"timestamp_sec\":" << now_sec()
            << ",\"payload\":{\"message\":\"command recognized but not implemented\"}}\n";
    output_.flush();
    return true;
}

}  // namespace hogak::control
