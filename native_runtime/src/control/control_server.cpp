#include "control/control_server.h"

#include <chrono>
#include <cstdint>
#include <fstream>
#include <initializer_list>
#include <limits>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include "control/json_line_protocol.h"
#include "control/json_parser.h"
#include "engine/engine.h"

namespace hogak::control {

namespace {

using JsonValue = hogak::control::json::Value;

double now_sec() {
    using clock = std::chrono::steady_clock;
    const auto now = clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::duration<double>>(now).count();
}

bool require_exact_keys(
    const JsonValue::Object& object,
    std::initializer_list<const char*> allowed_keys,
    const std::string& field_name,
    std::string* error_out) {
    std::unordered_set<std::string> allowed;
    for (const char* key : allowed_keys) {
        allowed.insert(std::string(key));
    }
    std::vector<std::string> unknown;
    for (const auto& [key, _] : object) {
        if (allowed.find(key) == allowed.end()) {
            unknown.push_back(key);
        }
    }
    if (!unknown.empty()) {
        if (error_out != nullptr) {
            std::ostringstream out;
            out << "unsupported " << field_name << " fields: ";
            for (std::size_t index = 0; index < unknown.size(); ++index) {
                if (index > 0) {
                    out << ", ";
                }
                out << unknown[index];
            }
            *error_out = out.str();
        }
        return false;
    }
    return true;
}

const JsonValue* require_field(const JsonValue::Object& object, const std::string& key) {
    const auto it = object.find(key);
    return it == object.end() ? nullptr : &it->second;
}

bool require_object_field(
    const JsonValue::Object& object,
    const std::string& key,
    JsonValue::Object* value_out,
    const std::string& field_name,
    std::string* error_out) {
    const JsonValue* value = require_field(object, key);
    if (value == nullptr || !value->is_object()) {
        if (error_out != nullptr) {
            *error_out = field_name + " must be a JSON object";
        }
        return false;
    }
    if (value_out != nullptr) {
        *value_out = value->as_object();
    }
    return true;
}

bool require_string_field(
    const JsonValue::Object& object,
    const std::string& key,
    std::string* value_out,
    const std::string& field_name,
    bool allow_empty,
    std::string* error_out) {
    const JsonValue* value = require_field(object, key);
    if (value == nullptr || !value->is_string()) {
        if (error_out != nullptr) {
            *error_out = field_name + " must be a string";
        }
        return false;
    }
    const std::string text = value->as_string();
    if (!allow_empty && text.empty()) {
        if (error_out != nullptr) {
            *error_out = field_name + " must not be empty";
        }
        return false;
    }
    if (value_out != nullptr) {
        *value_out = text;
    }
    return true;
}

bool require_bool_field(
    const JsonValue::Object& object,
    const std::string& key,
    bool* value_out,
    const std::string& field_name,
    std::string* error_out) {
    const JsonValue* value = require_field(object, key);
    if (value == nullptr || !value->is_bool()) {
        if (error_out != nullptr) {
            *error_out = field_name + " must be a boolean";
        }
        return false;
    }
    if (value_out != nullptr) {
        *value_out = value->as_bool();
    }
    return true;
}

bool require_number_field(
    const JsonValue::Object& object,
    const std::string& key,
    double* value_out,
    const std::string& field_name,
    std::string* error_out) {
    const JsonValue* value = require_field(object, key);
    if (value == nullptr || !value->is_number()) {
        if (error_out != nullptr) {
            *error_out = field_name + " must be a number";
        }
        return false;
    }
    if (value_out != nullptr) {
        *value_out = value->as_number();
    }
    return true;
}

bool require_int_field(
    const JsonValue::Object& object,
    const std::string& key,
    std::int32_t* value_out,
    const std::string& field_name,
    std::string* error_out) {
    double number = 0.0;
    if (!require_number_field(object, key, &number, field_name, error_out)) {
        return false;
    }
    if (number < static_cast<double>(std::numeric_limits<std::int32_t>::min()) ||
        number > static_cast<double>(std::numeric_limits<std::int32_t>::max()) ||
        static_cast<double>(static_cast<std::int32_t>(number)) != number) {
        if (error_out != nullptr) {
            *error_out = field_name + " must be an integer";
        }
        return false;
    }
    if (value_out != nullptr) {
        *value_out = static_cast<std::int32_t>(number);
    }
    return true;
}

bool require_int64_field(
    const JsonValue::Object& object,
    const std::string& key,
    std::int64_t* value_out,
    const std::string& field_name,
    std::string* error_out) {
    double number = 0.0;
    if (!require_number_field(object, key, &number, field_name, error_out)) {
        return false;
    }
    if (number < static_cast<double>(std::numeric_limits<std::int64_t>::min()) ||
        number > static_cast<double>(std::numeric_limits<std::int64_t>::max()) ||
        static_cast<double>(static_cast<std::int64_t>(number)) != number) {
        if (error_out != nullptr) {
            *error_out = field_name + " must be an integer";
        }
        return false;
    }
    if (value_out != nullptr) {
        *value_out = static_cast<std::int64_t>(number);
    }
    return true;
}

bool load_json_file(const std::string& path, JsonValue* value_out, std::string* error_out) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        if (error_out != nullptr) {
            *error_out = "failed to open JSON file: " + path;
        }
        return false;
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    std::string parse_error;
    if (!json::parse(buffer.str(), value_out, &parse_error)) {
        if (error_out != nullptr) {
            *error_out = "failed to parse JSON file " + path + ": " + parse_error;
        }
        return false;
    }
    return true;
}

bool load_reload_config(
    const JsonValue::Object& payload,
    hogak::engine::EngineConfig* config,
    std::string* error_out) {
    if (config == nullptr) {
        if (error_out != nullptr) {
            *error_out = "internal configuration destination unavailable";
        }
        return false;
    }

    if (!require_exact_keys(payload, {"inputs", "geometry", "timing", "outputs", "runtime"}, "schema v2", error_out)) {
        return false;
    }

    JsonValue::Object inputs;
    JsonValue::Object geometry;
    JsonValue::Object timing;
    JsonValue::Object outputs;
    JsonValue::Object runtime;
    if (!require_object_field(payload, "inputs", &inputs, "inputs", error_out) ||
        !require_object_field(payload, "geometry", &geometry, "geometry", error_out) ||
        !require_object_field(payload, "timing", &timing, "timing", error_out) ||
        !require_object_field(payload, "outputs", &outputs, "outputs", error_out) ||
        !require_object_field(payload, "runtime", &runtime, "runtime", error_out)) {
        return false;
    }

    if (!require_exact_keys(inputs, {"left", "right"}, "inputs", error_out) ||
        !require_exact_keys(geometry, {"artifact_path"}, "geometry", error_out) ||
        !require_exact_keys(
            timing,
            {
                "pair_mode",
                "allow_frame_reuse",
                "reuse_max_age_ms",
                "reuse_max_consecutive",
                "match_max_delta_ms",
                "time_source",
                "manual_offset_ms",
                "auto_offset_window_sec",
                "auto_offset_max_search_ms",
                "recalibration_interval_sec",
                "recalibration_trigger_skew_ms",
                "recalibration_trigger_wait_ratio",
                "auto_offset_confidence_min",
            },
            "timing",
            error_out) ||
        !require_exact_keys(outputs, {"probe", "transmit"}, "outputs", error_out) ||
        !require_exact_keys(
            runtime,
            {
                "input_runtime",
                "ffmpeg_bin",
                "gpu_mode",
                "gpu_device",
                "stitch_output_scale",
                "stitch_every_n",
                "benchmark_log_interval_sec",
                "headless_benchmark",
            },
            "runtime",
            error_out)) {
        return false;
    }

    JsonValue::Object left;
    JsonValue::Object right;
    JsonValue::Object probe;
    JsonValue::Object transmit;
    if (!require_object_field(inputs, "left", &left, "inputs.left", error_out) ||
        !require_object_field(inputs, "right", &right, "inputs.right", error_out) ||
        !require_object_field(outputs, "probe", &probe, "outputs.probe", error_out) ||
        !require_object_field(outputs, "transmit", &transmit, "outputs.transmit", error_out)) {
        return false;
    }

    if (!require_exact_keys(left, {"url", "transport", "timeout_sec", "reconnect_cooldown_sec", "buffer_frames"}, "inputs.left", error_out) ||
        !require_exact_keys(right, {"url", "transport", "timeout_sec", "reconnect_cooldown_sec", "buffer_frames"}, "inputs.right", error_out) ||
        !require_exact_keys(probe, {"runtime", "target", "codec", "bitrate", "preset", "muxer", "width", "height", "fps", "debug_overlay"}, "outputs.probe", error_out) ||
        !require_exact_keys(transmit, {"runtime", "target", "codec", "bitrate", "preset", "muxer", "width", "height", "fps", "debug_overlay"}, "outputs.transmit", error_out)) {
        return false;
    }

    std::string left_url;
    std::string right_url;
    std::string transport;
    double timeout_sec = 0.0;
    double reconnect_cooldown_sec = 0.0;
    std::int32_t buffer_frames = 0;
    if (!require_string_field(left, "url", &left_url, "inputs.left.url", false, error_out) ||
        !require_string_field(right, "url", &right_url, "inputs.right.url", false, error_out) ||
        !require_string_field(left, "transport", &transport, "inputs.left.transport", false, error_out) ||
        !require_string_field(right, "transport", &transport, "inputs.right.transport", false, error_out) ||
        !require_number_field(left, "timeout_sec", &timeout_sec, "inputs.left.timeout_sec", error_out) ||
        !require_number_field(right, "timeout_sec", &timeout_sec, "inputs.right.timeout_sec", error_out) ||
        !require_number_field(left, "reconnect_cooldown_sec", &reconnect_cooldown_sec, "inputs.left.reconnect_cooldown_sec", error_out) ||
        !require_number_field(right, "reconnect_cooldown_sec", &reconnect_cooldown_sec, "inputs.right.reconnect_cooldown_sec", error_out) ||
        !require_int_field(left, "buffer_frames", &buffer_frames, "inputs.left.buffer_frames", error_out) ||
        !require_int_field(right, "buffer_frames", &buffer_frames, "inputs.right.buffer_frames", error_out)) {
        return false;
    }

    // Left/right input shared settings must be exact matches.
    const JsonValue* left_transport = require_field(left, "transport");
    const JsonValue* right_transport = require_field(right, "transport");
    if (left_transport == nullptr || right_transport == nullptr || !left_transport->is_string() || !right_transport->is_string() ||
        left_transport->as_string() != right_transport->as_string()) {
        if (error_out != nullptr) {
            *error_out = "inputs.left.transport must match inputs.right.transport";
        }
        return false;
    }
    const JsonValue* left_timeout = require_field(left, "timeout_sec");
    const JsonValue* right_timeout = require_field(right, "timeout_sec");
    if (left_timeout == nullptr || right_timeout == nullptr || !left_timeout->is_number() || !right_timeout->is_number() ||
        left_timeout->as_number() != right_timeout->as_number()) {
        if (error_out != nullptr) {
            *error_out = "inputs.left.timeout_sec must match inputs.right.timeout_sec";
        }
        return false;
    }
    const JsonValue* left_reconnect = require_field(left, "reconnect_cooldown_sec");
    const JsonValue* right_reconnect = require_field(right, "reconnect_cooldown_sec");
    if (left_reconnect == nullptr || right_reconnect == nullptr || !left_reconnect->is_number() || !right_reconnect->is_number() ||
        left_reconnect->as_number() != right_reconnect->as_number()) {
        if (error_out != nullptr) {
            *error_out = "inputs.left.reconnect_cooldown_sec must match inputs.right.reconnect_cooldown_sec";
        }
        return false;
    }
    const JsonValue* left_buffer = require_field(left, "buffer_frames");
    const JsonValue* right_buffer = require_field(right, "buffer_frames");
    if (left_buffer == nullptr || right_buffer == nullptr || !left_buffer->is_number() || !right_buffer->is_number() ||
        left_buffer->as_number() != right_buffer->as_number()) {
        if (error_out != nullptr) {
            *error_out = "inputs.left.buffer_frames must match inputs.right.buffer_frames";
        }
        return false;
    }

    std::string artifact_path;
    if (!require_string_field(geometry, "artifact_path", &artifact_path, "geometry.artifact_path", false, error_out)) {
        return false;
    }

    std::string pair_mode;
    std::string time_source;
    if (!require_string_field(timing, "pair_mode", &pair_mode, "timing.pair_mode", false, error_out) ||
        !require_bool_field(timing, "allow_frame_reuse", &config->allow_frame_reuse, "timing.allow_frame_reuse", error_out) ||
        !require_number_field(timing, "reuse_max_age_ms", &config->pair_reuse_max_age_ms, "timing.reuse_max_age_ms", error_out) ||
        !require_int_field(timing, "reuse_max_consecutive", &config->pair_reuse_max_consecutive, "timing.reuse_max_consecutive", error_out) ||
        !require_number_field(timing, "match_max_delta_ms", &config->sync_match_max_delta_ms, "timing.match_max_delta_ms", error_out) ||
        !require_string_field(timing, "time_source", &time_source, "timing.time_source", false, error_out) ||
        !require_number_field(timing, "manual_offset_ms", &config->sync_manual_offset_ms, "timing.manual_offset_ms", error_out) ||
        !require_number_field(timing, "auto_offset_window_sec", &config->sync_auto_offset_window_sec, "timing.auto_offset_window_sec", error_out) ||
        !require_number_field(timing, "auto_offset_max_search_ms", &config->sync_auto_offset_max_search_ms, "timing.auto_offset_max_search_ms", error_out) ||
        !require_number_field(timing, "recalibration_interval_sec", &config->sync_recalibration_interval_sec, "timing.recalibration_interval_sec", error_out) ||
        !require_number_field(timing, "recalibration_trigger_skew_ms", &config->sync_recalibration_trigger_skew_ms, "timing.recalibration_trigger_skew_ms", error_out) ||
        !require_number_field(timing, "recalibration_trigger_wait_ratio", &config->sync_recalibration_trigger_wait_ratio, "timing.recalibration_trigger_wait_ratio", error_out) ||
        !require_number_field(timing, "auto_offset_confidence_min", &config->sync_auto_offset_confidence_min, "timing.auto_offset_confidence_min", error_out)) {
        return false;
    }
    config->sync_pair_mode = pair_mode;
    config->sync_time_source = time_source;

    if (!require_string_field(runtime, "input_runtime", &config->input_runtime, "runtime.input_runtime", false, error_out) ||
        !require_string_field(runtime, "ffmpeg_bin", &config->ffmpeg_bin, "runtime.ffmpeg_bin", true, error_out) ||
        !require_string_field(runtime, "gpu_mode", &config->gpu_mode, "runtime.gpu_mode", false, error_out) ||
        !require_int_field(runtime, "gpu_device", &config->gpu_device, "runtime.gpu_device", error_out) ||
        !require_number_field(runtime, "stitch_output_scale", &config->stitch_output_scale, "runtime.stitch_output_scale", error_out) ||
        !require_int_field(runtime, "stitch_every_n", &config->stitch_every_n, "runtime.stitch_every_n", error_out) ||
        !require_number_field(runtime, "benchmark_log_interval_sec", &config->benchmark_log_interval_sec, "runtime.benchmark_log_interval_sec", error_out) ||
        !require_bool_field(runtime, "headless_benchmark", &config->headless_benchmark, "runtime.headless_benchmark", error_out)) {
        return false;
    }

    if (!require_string_field(probe, "runtime", &config->output.runtime, "outputs.probe.runtime", false, error_out) ||
        !require_string_field(transmit, "runtime", &config->production_output.runtime, "outputs.transmit.runtime", false, error_out)) {
        return false;
    }

    if (!require_string_field(probe, "target", &config->output.target, "outputs.probe.target", config->output.runtime == "none", error_out) ||
        !require_string_field(probe, "codec", &config->output.codec, "outputs.probe.codec", false, error_out) ||
        !require_string_field(probe, "bitrate", &config->output.bitrate, "outputs.probe.bitrate", false, error_out) ||
        !require_string_field(probe, "preset", &config->output.preset, "outputs.probe.preset", false, error_out) ||
        !require_string_field(probe, "muxer", &config->output.muxer, "outputs.probe.muxer", true, error_out) ||
        !require_int_field(probe, "width", &config->output.width, "outputs.probe.width", error_out) ||
        !require_int_field(probe, "height", &config->output.height, "outputs.probe.height", error_out) ||
        !require_number_field(probe, "fps", &config->output.fps, "outputs.probe.fps", error_out) ||
        !require_bool_field(probe, "debug_overlay", &config->output.debug_overlay, "outputs.probe.debug_overlay", error_out) ||
        !require_string_field(transmit, "target", &config->production_output.target, "outputs.transmit.target", config->production_output.runtime == "none", error_out) ||
        !require_string_field(transmit, "codec", &config->production_output.codec, "outputs.transmit.codec", false, error_out) ||
        !require_string_field(transmit, "bitrate", &config->production_output.bitrate, "outputs.transmit.bitrate", false, error_out) ||
        !require_string_field(transmit, "preset", &config->production_output.preset, "outputs.transmit.preset", false, error_out) ||
        !require_string_field(transmit, "muxer", &config->production_output.muxer, "outputs.transmit.muxer", true, error_out) ||
        !require_int_field(transmit, "width", &config->production_output.width, "outputs.transmit.width", error_out) ||
        !require_int_field(transmit, "height", &config->production_output.height, "outputs.transmit.height", error_out) ||
        !require_number_field(transmit, "fps", &config->production_output.fps, "outputs.transmit.fps", error_out) ||
        !require_bool_field(transmit, "debug_overlay", &config->production_output.debug_overlay, "outputs.transmit.debug_overlay", error_out)) {
        return false;
    }

    config->left.name = "left";
    config->right.name = "right";
    config->left.url = left_url;
    config->right.url = right_url;
    config->left.transport = transport;
    config->right.transport = transport;
    config->left.timeout_sec = timeout_sec;
    config->right.timeout_sec = timeout_sec;
    config->left.reconnect_cooldown_sec = reconnect_cooldown_sec;
    config->right.reconnect_cooldown_sec = reconnect_cooldown_sec;
    config->left.max_buffered_frames = buffer_frames;
    config->right.max_buffered_frames = buffer_frames;
    config->geometry.artifact_file = artifact_path;
    return true;
}

std::string require_command_type(const JsonValue::Object& envelope, std::string* error_out) {
    const JsonValue* type_value = require_field(envelope, "type");
    if (type_value == nullptr || !type_value->is_string()) {
        if (error_out != nullptr) {
            *error_out = "command type must be a string";
        }
        return {};
    }
    return type_value->as_string();
}

std::int64_t require_command_seq(const JsonValue::Object& envelope, std::string* error_out) {
    std::int64_t seq = 0;
    if (!require_int64_field(envelope, "seq", &seq, "seq", error_out)) {
        return 0;
    }
    return seq;
}

bool require_schema_version_v2(const JsonValue::Object& envelope, std::string* error_out) {
    std::int64_t schema_version = 0;
    if (!require_int64_field(envelope, "schema_version", &schema_version, "schema_version", error_out)) {
        return false;
    }
    if (schema_version != 2) {
        if (error_out != nullptr) {
            *error_out = "unsupported schema_version; expected 2";
        }
        return false;
    }
    return true;
}

bool require_payload_object(const JsonValue::Object& envelope, JsonValue::Object* payload_out, std::string* error_out) {
    const JsonValue* payload = require_field(envelope, "payload");
    if (payload == nullptr || !payload->is_object()) {
        if (error_out != nullptr) {
            *error_out = "payload must be a JSON object";
        }
        return false;
    }
    if (payload_out != nullptr) {
        *payload_out = payload->as_object();
    }
    return true;
}

void write_error(std::ostream& output, std::int64_t seq, const std::string& code, const std::string& message, const std::string& details = "") {
    output << command_error_json(seq, now_sec(), code, message, details) << '\n';
    output.flush();
}

void write_status(std::ostream& output, std::int64_t seq, const std::string& status, const std::string& message) {
    output << command_status_json(seq, now_sec(), status, message) << '\n';
    output.flush();
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
    if (line.empty()) {
        return true;
    }

    JsonValue root;
    std::string parse_error;
    if (!json::parse(line, &root, &parse_error)) {
        write_error(output_, 0, "invalid_json", "failed to parse control command", parse_error);
        return true;
    }
    if (!root.is_object()) {
        write_error(output_, 0, "invalid_envelope", "control command must be a JSON object");
        return true;
    }

    const JsonValue::Object& envelope = root.as_object();
    if (!require_exact_keys(envelope, {"schema_version", "seq", "type", "payload"}, "command envelope", &parse_error)) {
        const std::int64_t seq = envelope.find("seq") != envelope.end() && envelope.find("seq")->second.is_number()
            ? static_cast<std::int64_t>(envelope.find("seq")->second.as_number())
            : 0;
        write_error(output_, seq, "unsupported_envelope", "unsupported command envelope", parse_error);
        return true;
    }

    std::string type_error;
    const std::string command_type = require_command_type(envelope, &type_error);
    const std::int64_t seq = require_command_seq(envelope, &type_error);
    if (!type_error.empty()) {
        write_error(output_, seq, "invalid_envelope", type_error);
        return true;
    }
    if (!require_schema_version_v2(envelope, &type_error)) {
        write_error(output_, seq, "unsupported_schema_version", type_error);
        return true;
    }

    JsonValue::Object payload;
    if (!require_payload_object(envelope, &payload, &type_error)) {
        write_error(output_, seq, "invalid_payload", type_error);
        return true;
    }

    if (command_type == "shutdown" || command_type == "stop") {
        engine.stop();
        write_status(output_, seq, "stopped", "runtime stopped");
        return false;
    }

    if (command_type == "request_snapshot") {
        const auto kind_it = payload.find("kind");
        if (kind_it != payload.end() && (!kind_it->second.is_string() || kind_it->second.as_string() != "metrics")) {
            write_error(output_, seq, "invalid_payload", "request_snapshot.kind must be \"metrics\"");
            return true;
        }
        emit_metrics(seq, engine);
        return true;
    }

    if (command_type == "reload_config") {
        auto config = engine.current_config();
        if (!load_reload_config(payload, &config, &type_error)) {
            write_error(output_, seq, "invalid_reload_config", type_error);
            return true;
        }
        if (engine.reload_config(config)) {
            write_status(output_, seq, "reloaded", "config reloaded");
        } else {
            write_error(output_, seq, "reload_failed", "config reload failed");
        }
        return true;
    }

    if (command_type == "start") {
        write_status(output_, seq, "started", "runtime already running");
        return true;
    }

    write_error(output_, seq, "unknown_command", "command recognized but not implemented", command_type);
    return true;
}

}  // namespace hogak::control
