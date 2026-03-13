#pragma once

#include <memory>
#include <string>

namespace hogak::output {

class OutputWriter;

struct OutputRuntimeCapabilities {
    bool supports_cpu_input = false;
    bool supports_gpu_input = false;
    bool requires_cpu_input = false;
};

std::unique_ptr<OutputWriter> create_output_writer(const std::string& runtime);
OutputRuntimeCapabilities get_output_runtime_capabilities(const std::string& runtime);
bool output_runtime_available(const std::string& runtime);
std::string output_runtime_availability_reason(const std::string& runtime);

}  // namespace hogak::output
