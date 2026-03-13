#include "output/output_writer_factory.h"

#include "output/ffmpeg_output_writer.h"
#include "output/gpu_direct_output_writer.h"

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

}  // namespace hogak::output
