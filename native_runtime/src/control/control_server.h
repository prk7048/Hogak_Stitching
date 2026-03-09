#pragma once

#include <cstdint>
#include <istream>
#include <ostream>

namespace hogak::engine {
class StitchEngine;
}

namespace hogak::control {

class ControlServer {
public:
    ControlServer(std::istream& input, std::ostream& output);

    bool process_one_command(hogak::engine::StitchEngine& engine);
    void emit_hello();
    void emit_metrics(std::int64_t seq, const hogak::engine::StitchEngine& engine);

private:
    std::istream& input_;
    std::ostream& output_;
};

}  // namespace hogak::control
