#pragma once

#include <memory>
#include <string>

namespace hogak::output {

class OutputWriter;

std::unique_ptr<OutputWriter> create_output_writer(const std::string& runtime);

}  // namespace hogak::output
