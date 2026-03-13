#include "output/gpu_direct_support.h"

#include <sstream>

#include "output/gpu_direct_build_config.h"

namespace hogak::output {

bool gpu_direct_dependency_ready() noexcept {
    return HOGAK_GPU_DIRECT_AVCODEC_ENABLED != 0;
}

const char* gpu_direct_provider() noexcept {
    return HOGAK_GPU_DIRECT_PROVIDER;
}

std::string gpu_direct_dependency_status() {
    return HOGAK_GPU_DIRECT_DEPENDENCY_STATUS;
}

std::string gpu_direct_ffmpeg_dev_root() {
    return HOGAK_GPU_DIRECT_FFMPEG_DEV_ROOT;
}

std::string gpu_direct_startup_status() {
    std::ostringstream out;
    out << "provider=" << gpu_direct_provider()
        << " dependency_ready=" << (gpu_direct_dependency_ready() ? "true" : "false")
        << " status=" << gpu_direct_dependency_status();
    const std::string ffmpeg_dev_root = gpu_direct_ffmpeg_dev_root();
    if (!ffmpeg_dev_root.empty()) {
        out << " ffmpeg_dev_root=" << ffmpeg_dev_root;
    }
    return out.str();
}

}  // namespace hogak::output
