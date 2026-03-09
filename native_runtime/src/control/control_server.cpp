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

    output_ << "{\"seq\":0,\"type\":\"warning\",\"timestamp_sec\":" << now_sec()
            << ",\"payload\":{\"message\":\"command accepted by skeleton but not implemented\"}}\n";
    output_.flush();
    return true;
}

}  // namespace hogak::control
