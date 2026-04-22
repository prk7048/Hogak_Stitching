#include "engine/input_pipeline.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudawarping.hpp>
#include <opencv2/imgproc.hpp>

#include "output/gpu_direct_build_config.h"

#if HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED
#include <cuda_runtime.h>
#include <nppdefs.h>
#include <nppi_color_conversion.h>
#include <opencv2/core/cuda_stream_accessor.hpp>
#endif

namespace hogak::engine {

namespace {

double frame_mean_luma(const cv::Mat& image) {
    if (image.empty()) {
        return 0.0;
    }
    cv::Mat gray;
    if (image.channels() == 1) {
        gray = image;
    } else {
        cv::cvtColor(image, gray, cv::COLOR_BGR2GRAY);
    }
    return cv::mean(gray)[0];
}

cv::Size scaled_frame_size(const cv::Size& source_size, double scale) {
    if (source_size.width <= 0 || source_size.height <= 0) {
        return source_size;
    }
    if (std::abs(scale - 1.0) < 1e-6) {
        return source_size;
    }
    return cv::Size(
        std::max(2, static_cast<int>(std::round(source_size.width * scale))),
        std::max(2, static_cast<int>(std::round(source_size.height * scale))));
}

#if HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED
cv::Exception make_cuda_runtime_exception(const char* operation, cudaError_t status) {
    return cv::Exception(
        cv::Error::GpuApiCallError,
        std::string(operation) + " failed: " + cudaGetErrorString(status),
        __FUNCTION__,
        __FILE__,
        __LINE__);
}

cv::Exception make_npp_exception(const char* operation, NppStatus status) {
    return cv::Exception(
        cv::Error::GpuApiCallError,
        std::string(operation) + " failed with NPP status " + std::to_string(static_cast<int>(status)),
        __FUNCTION__,
        __FILE__,
        __LINE__);
}

NppStreamContext make_npp_stream_context() {
    int cuda_device = 0;
    const cudaError_t device_status = cudaGetDevice(&cuda_device);
    if (device_status != cudaSuccess) {
        throw make_cuda_runtime_exception("cudaGetDevice", device_status);
    }

    cudaDeviceProp device_props{};
    const cudaError_t props_status = cudaGetDeviceProperties(&device_props, cuda_device);
    if (props_status != cudaSuccess) {
        throw make_cuda_runtime_exception("cudaGetDeviceProperties", props_status);
    }

    unsigned int stream_flags = 0;
    const cudaStream_t stream_handle = cv::cuda::StreamAccessor::getStream(cv::cuda::Stream::Null());
    const cudaError_t stream_flags_status = cudaStreamGetFlags(stream_handle, &stream_flags);
    if (stream_flags_status != cudaSuccess) {
        throw make_cuda_runtime_exception("cudaStreamGetFlags", stream_flags_status);
    }

    NppStreamContext stream_context{};
    stream_context.hStream = stream_handle;
    stream_context.nCudaDeviceId = cuda_device;
    stream_context.nMultiProcessorCount = device_props.multiProcessorCount;
    stream_context.nMaxThreadsPerMultiProcessor = device_props.maxThreadsPerMultiProcessor;
    stream_context.nMaxThreadsPerBlock = device_props.maxThreadsPerBlock;
    stream_context.nSharedMemPerBlock = device_props.sharedMemPerBlock;
    stream_context.nCudaDevAttrComputeCapabilityMajor = device_props.major;
    stream_context.nCudaDevAttrComputeCapabilityMinor = device_props.minor;
    stream_context.nStreamFlags = stream_flags;
    stream_context.nReserved0 = 0;
    return stream_context;
}

void convert_nv12_to_bgr_gpu(
    const cv::cuda::GpuMat& nv12_y_gpu,
    const cv::cuda::GpuMat& nv12_uv_gpu,
    cv::cuda::GpuMat* decoded_bgr_gpu) {
    if (decoded_bgr_gpu == nullptr) {
        throw cv::Exception(cv::Error::StsBadArg, "cuda nv12 output gpu mat is null", __FUNCTION__, __FILE__, __LINE__);
    }
    if (nv12_y_gpu.empty() || nv12_uv_gpu.empty()) {
        throw cv::Exception(cv::Error::StsBadArg, "cuda nv12 planes are empty", __FUNCTION__, __FILE__, __LINE__);
    }
    if (nv12_y_gpu.type() != CV_8UC1 || nv12_uv_gpu.type() != CV_8UC2) {
        throw cv::Exception(cv::Error::StsBadArg, "cuda nv12 plane types are invalid", __FUNCTION__, __FILE__, __LINE__);
    }
    if (nv12_y_gpu.cols != (nv12_uv_gpu.cols * 2) || nv12_y_gpu.rows != (nv12_uv_gpu.rows * 2)) {
        throw cv::Exception(cv::Error::StsBadArg, "cuda nv12 plane dimensions are invalid", __FUNCTION__, __FILE__, __LINE__);
    }
    if (nv12_y_gpu.step != nv12_uv_gpu.step) {
        throw cv::Exception(
            cv::Error::StsBadArg,
            "cuda nv12 gpu plane step mismatch prevents a shared NPP source step",
            __FUNCTION__,
            __FILE__,
            __LINE__);
    }

    decoded_bgr_gpu->create(nv12_y_gpu.rows, nv12_y_gpu.cols, CV_8UC3);
    const Npp8u* source_planes[2] = {
        nv12_y_gpu.ptr<Npp8u>(),
        nv12_uv_gpu.ptr<Npp8u>(),
    };
    const NppiSize roi{
        nv12_y_gpu.cols,
        nv12_y_gpu.rows,
    };
    const NppStatus status = nppiNV12ToBGR_8u_P2C3R_Ctx(
        source_planes,
        static_cast<int>(nv12_y_gpu.step),
        decoded_bgr_gpu->ptr<Npp8u>(),
        static_cast<int>(decoded_bgr_gpu->step),
        roi,
        make_npp_stream_context());
    if (status != NPP_SUCCESS) {
        throw make_npp_exception("cuda nv12 nppiNV12ToBGR_8u_P2C3R_Ctx", status);
    }
}
#endif

}  // namespace

bool input_pipe_format_is_nv12(const StreamConfig& config) {
    return config.input_pipe_format == "nv12";
}

bool is_nv12_gpu_conversion_unsupported(const cv::Exception& error) {
    const std::string message = error.what();
    return
        message.find("Unknown/unsupported color conversion code") != std::string::npos ||
        message.find("cuda nv12") != std::string::npos;
}

cv::Size input_frame_size_for_runtime(const cv::Mat& frame, const StreamConfig& config, double scale) {
    if (input_pipe_format_is_nv12(config)) {
        return scaled_frame_size(cv::Size(config.width, config.height), scale);
    }
    if (frame.empty()) {
        return {};
    }
    return scaled_frame_size(frame.size(), scale);
}

double input_frame_mean_luma(const cv::Mat& frame, const StreamConfig& config) {
    if (frame.empty()) {
        return 0.0;
    }
    if (!input_pipe_format_is_nv12(config)) {
        return frame_mean_luma(frame);
    }
    if (frame.type() != CV_8UC1 || frame.cols != config.width || frame.rows < config.height) {
        return 0.0;
    }
    cv::Mat y_plane(config.height, config.width, CV_8UC1, const_cast<std::uint8_t*>(frame.ptr<std::uint8_t>(0)), frame.step);
    return cv::mean(y_plane)[0];
}

cv::Mat decode_input_frame_for_stitch(const cv::Mat& frame, const StreamConfig& config) {
    if (frame.empty()) {
        return {};
    }
    if (!input_pipe_format_is_nv12(config)) {
        return frame;
    }
    if (frame.type() != CV_8UC1 || frame.cols != config.width || frame.rows < (config.height + (config.height / 2))) {
        return {};
    }

    cv::Mat y_plane(config.height, config.width, CV_8UC1, const_cast<std::uint8_t*>(frame.ptr<std::uint8_t>(0)), frame.step);
    cv::Mat uv_plane(
        config.height / 2,
        config.width / 2,
        CV_8UC2,
        const_cast<std::uint8_t*>(frame.ptr<std::uint8_t>(config.height)),
        frame.step);
    cv::Mat decoded_bgr;
    cv::cvtColorTwoPlane(y_plane, uv_plane, decoded_bgr, cv::COLOR_YUV2BGR_NV12);
    return decoded_bgr;
}

bool upload_input_frame_for_gpu_stitch(
    const cv::Mat& raw_input,
    const StreamConfig& config,
    double output_scale,
    const cv::Mat* cpu_fallback_bgr,
    cv::cuda::GpuMat* nv12_y_gpu,
    cv::cuda::GpuMat* nv12_uv_gpu,
    cv::cuda::GpuMat* decoded_bgr_gpu,
    cv::cuda::GpuMat* final_bgr_gpu) {
    if (final_bgr_gpu == nullptr) {
        return false;
    }
    const cv::Size target_size = input_frame_size_for_runtime(raw_input, config, output_scale);
    if (target_size.width <= 0 || target_size.height <= 0) {
        return false;
    }

    if (!input_pipe_format_is_nv12(config)) {
        if (cpu_fallback_bgr == nullptr || cpu_fallback_bgr->empty()) {
            return false;
        }
        const cv::Size source_size = cpu_fallback_bgr->size();
        if (target_size == source_size) {
            final_bgr_gpu->upload(*cpu_fallback_bgr);
        } else {
            cv::cuda::GpuMat uploaded_bgr;
            uploaded_bgr.upload(*cpu_fallback_bgr);
            cv::cuda::resize(uploaded_bgr, *final_bgr_gpu, target_size, 0.0, 0.0, cv::INTER_AREA);
        }
        return true;
    }

    if (raw_input.type() != CV_8UC1 || raw_input.cols != config.width || raw_input.rows < (config.height + (config.height / 2))) {
        return false;
    }
    if (nv12_y_gpu == nullptr || nv12_uv_gpu == nullptr || decoded_bgr_gpu == nullptr) {
        return false;
    }

    cv::Mat y_plane(config.height, config.width, CV_8UC1, const_cast<std::uint8_t*>(raw_input.ptr<std::uint8_t>(0)), raw_input.step);
    cv::Mat uv_plane(
        config.height / 2,
        config.width / 2,
        CV_8UC2,
        const_cast<std::uint8_t*>(raw_input.ptr<std::uint8_t>(config.height)),
        raw_input.step);
    nv12_y_gpu->upload(y_plane);
    nv12_uv_gpu->upload(uv_plane);

#if HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED
    convert_nv12_to_bgr_gpu(*nv12_y_gpu, *nv12_uv_gpu, decoded_bgr_gpu);
#else
    throw cv::Exception(
        cv::Error::GpuApiCallError,
        "cuda nv12 input unsupported: runtime built without CUDA Toolkit NPP support",
        __FUNCTION__,
        __FILE__,
        __LINE__);
#endif

    if (decoded_bgr_gpu->size() == target_size) {
        decoded_bgr_gpu->copyTo(*final_bgr_gpu);
    } else {
        cv::cuda::resize(*decoded_bgr_gpu, *final_bgr_gpu, target_size, 0.0, 0.0, cv::INTER_AREA);
    }
    return true;
}

cv::Mat resize_frame_for_runtime(const cv::Mat& frame, double scale) {
    if (frame.empty()) {
        return frame;
    }
    if (std::abs(scale - 1.0) < 1e-6) {
        return frame;
    }
    const int width = std::max(2, static_cast<int>(std::round(frame.cols * scale)));
    const int height = std::max(2, static_cast<int>(std::round(frame.rows * scale)));
    cv::Mat resized;
    cv::resize(frame, resized, cv::Size(width, height), 0.0, 0.0, cv::INTER_AREA);
    return resized;
}

}  // namespace hogak::engine
