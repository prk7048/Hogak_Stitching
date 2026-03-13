#pragma once

#include <string>

namespace hogak::output {

bool gpu_direct_dependency_ready() noexcept;
const char* gpu_direct_provider() noexcept;
std::string gpu_direct_dependency_status();
std::string gpu_direct_ffmpeg_dev_root();
std::string gpu_direct_startup_status();

}  // namespace hogak::output
