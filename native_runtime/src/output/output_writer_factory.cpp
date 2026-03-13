#include "output/output_writer_factory.h"

#include "output/ffmpeg_output_writer.h"
#include "output/gpu_direct_output_writer.h"
#include "output/gpu_direct_support.h"

namespace hogak::output {

std::unique_ptr<OutputWriter> create_output_writer(const std::string& runtime) {
    if (runtime == "ffmpeg") {
        return std::make_unique<FfmpegOutputWriter>();
    }
    if (runtime == "gpu-direct") {
        return std::make_unique<GpuDirectOutputWriter>();
    }
    return nullptr;
}

OutputRuntimeCapabilities get_output_runtime_capabilities(const std::string& runtime) {
    if (runtime == "ffmpeg") {
        return OutputRuntimeCapabilities{
            true,
            true,
            true,
        };
    }
    if (runtime == "gpu-direct") {
        return OutputRuntimeCapabilities{
            false,
            true,
            false,
        };
    }
    return OutputRuntimeCapabilities{};
}

bool output_runtime_available(const std::string& runtime) {
    if (runtime == "none" || runtime == "ffmpeg") {
        return true;
    }
    if (runtime == "gpu-direct") {
        return gpu_direct_dependency_ready();
    }
    return false;
}

std::string output_runtime_availability_reason(const std::string& runtime) {
    if (runtime == "none" || runtime == "ffmpeg") {
        return "";
    }
    if (runtime == "gpu-direct") {
        return gpu_direct_startup_status();
    }
    return "unsupported output runtime";
}

}  // namespace hogak::output
