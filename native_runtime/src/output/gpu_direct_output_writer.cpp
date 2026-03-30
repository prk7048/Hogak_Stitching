#include "output/gpu_direct_output_writer.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <memory>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/error.h>
#include <libavutil/hwcontext.h>
#include <libavutil/imgutils.h>
#include <libavutil/opt.h>
#include <libavutil/pixfmt.h>
#include <libswscale/swscale.h>
}

#include <opencv2/cudaimgproc.hpp>
#include <opencv2/imgproc.hpp>

#include "output/gpu_direct_build_config.h"
#include "output/gpu_direct_support.h"

#if HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED
#include <cuda_runtime.h>
#endif

namespace hogak::output {

namespace {

std::string trim_copy(const std::string& text) {
    std::size_t begin = 0;
    std::size_t end = text.size();
    while (begin < end && std::isspace(static_cast<unsigned char>(text[begin])) != 0) {
        ++begin;
    }
    while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1])) != 0) {
        --end;
    }
    return text.substr(begin, end - begin);
}

std::string sanitize_runtime_token(std::string text) {
    for (char& ch : text) {
        const bool allowed =
            std::isalnum(static_cast<unsigned char>(ch)) != 0 ||
            ch == '-' || ch == '_' || ch == '.';
        if (!allowed) {
            ch = '_';
        }
    }
    constexpr std::size_t kMaxTokenLength = 96;
    if (text.size() > kMaxTokenLength) {
        text.resize(kMaxTokenLength);
    }
    return trim_copy(text);
}

std::int64_t parse_bitrate_bits(const std::string& text) {
    const auto trimmed = trim_copy(text);
    if (trimmed.empty()) {
        return 0;
    }

    double multiplier = 1.0;
    std::string number_text = trimmed;
    const char suffix = static_cast<char>(std::tolower(static_cast<unsigned char>(trimmed.back())));
    if (suffix == 'k' || suffix == 'm' || suffix == 'g') {
        number_text.pop_back();
        if (suffix == 'k') {
            multiplier = 1'000.0;
        } else if (suffix == 'm') {
            multiplier = 1'000'000.0;
        } else if (suffix == 'g') {
            multiplier = 1'000'000'000.0;
        }
    }

    char* end = nullptr;
    const double value = std::strtod(number_text.c_str(), &end);
    if (end == number_text.c_str() || (end != nullptr && *end != '\0') || !std::isfinite(value) || value <= 0.0) {
        return 0;
    }
    return std::max<std::int64_t>(1, static_cast<std::int64_t>(std::llround(value * multiplier)));
}

bool starts_with(const std::string& text, const char* prefix) {
    return text.rfind(prefix, 0) == 0;
}

bool url_has_query_key(const std::string& url, const std::string& key) {
    const auto query_pos = url.find('?');
    if (query_pos == std::string::npos || key.empty()) {
        return false;
    }

    std::size_t item_begin = query_pos + 1;
    while (item_begin < url.size()) {
        const auto item_end = url.find('&', item_begin);
        const auto token = url.substr(item_begin, item_end == std::string::npos ? std::string::npos : item_end - item_begin);
        const auto equals_pos = token.find('=');
        const auto token_key = token.substr(0, equals_pos);
        if (token_key == key) {
            return true;
        }
        if (item_end == std::string::npos) {
            break;
        }
        item_begin = item_end + 1;
    }
    return false;
}

std::string append_url_query_key(std::string url, const std::string& key, const std::string& value) {
    if (key.empty() || value.empty() || url_has_query_key(url, key)) {
        return url;
    }
    url += (url.find('?') == std::string::npos) ? '?' : '&';
    url += key;
    url += '=';
    url += value;
    return url;
}

std::int64_t build_udp_transport_bitrate_bits(const std::string& bitrate) {
    const auto bitrate_bits = parse_bitrate_bits(bitrate);
    if (bitrate_bits <= 0) {
        return 0;
    }
    const auto overhead_bits = std::max<std::int64_t>(500'000, bitrate_bits / 10);
    return bitrate_bits + overhead_bits;
}

std::int64_t build_udp_burst_bits(std::int64_t transport_bitrate_bits, double fps) {
    if (transport_bitrate_bits <= 0) {
        return 0;
    }
    const double safe_fps = std::max(1.0, fps);
    const auto frame_bits = std::max<std::int64_t>(
        1,
        static_cast<std::int64_t>(std::llround(static_cast<double>(transport_bitrate_bits) / safe_fps)));
    return std::max<std::int64_t>(262'144, frame_bits * 4);
}

std::string build_udp_output_target(std::string target, const std::string& bitrate, double fps) {
    if (!starts_with(target, "udp://")) {
        return target;
    }

    std::string result = std::move(target);
    const auto transport_bitrate_bits = build_udp_transport_bitrate_bits(bitrate);
    const auto burst_bits = build_udp_burst_bits(transport_bitrate_bits, fps);
    if (transport_bitrate_bits > 0) {
        result = append_url_query_key(result, "bitrate", std::to_string(transport_bitrate_bits));
    }
    if (burst_bits > 0) {
        result = append_url_query_key(result, "burst_bits", std::to_string(burst_bits));
        const auto buffer_bytes = std::max<std::int64_t>(1 * 1024 * 1024, (burst_bits / 8) * 4);
        result = append_url_query_key(result, "buffer_size", std::to_string(buffer_bytes));
    } else {
        result = append_url_query_key(result, "buffer_size", std::to_string(1 * 1024 * 1024));
    }
    return result;
}

std::vector<std::string> split_text(const std::string& text, char delimiter) {
    std::vector<std::string> parts;
    std::string current;
    for (const char ch : text) {
        if (ch == delimiter) {
            parts.push_back(current);
            current.clear();
            continue;
        }
        current.push_back(ch);
    }
    parts.push_back(current);
    return parts;
}

std::string join_text(const std::vector<std::string>& parts, char delimiter) {
    std::ostringstream builder;
    for (std::size_t index = 0; index < parts.size(); ++index) {
        if (index > 0) {
            builder << delimiter;
        }
        builder << parts[index];
    }
    return builder.str();
}

std::string normalize_tee_leg_options(std::string options) {
    if (options.empty()) {
        return options;
    }
    if (options.find("f=mpegts") != std::string::npos) {
        if (options.find("mpegts_flags=") == std::string::npos) {
            options += ":mpegts_flags=resend_headers+pat_pmt_at_frames";
        } else if (options.find("pat_pmt_at_frames") == std::string::npos) {
            const std::string needle = "mpegts_flags=resend_headers";
            const auto pos = options.find(needle);
            if (pos != std::string::npos) {
                options.replace(pos, needle.size(), "mpegts_flags=resend_headers+pat_pmt_at_frames");
            }
        }
    }
    return options;
}

std::string build_tee_output_target(const hogak::engine::OutputConfig& config, double fps) {
    std::vector<std::string> legs = split_text(config.target, '|');
    for (auto& leg : legs) {
        std::string options;
        std::string leg_target = leg;
        if (!leg.empty() && leg.front() == '[') {
            const auto closing = leg.find(']');
            if (closing != std::string::npos) {
                options = leg.substr(1, closing - 1);
                leg_target = leg.substr(closing + 1);
            }
        }

        leg_target = build_udp_output_target(std::move(leg_target), config.bitrate, fps);
        options = normalize_tee_leg_options(std::move(options));
        if (!options.empty()) {
            leg = "[" + options + "]" + leg_target;
        } else {
            leg = leg_target;
        }
    }
    return join_text(legs, '|');
}

std::string build_output_target(const hogak::engine::OutputConfig& config, double fps, const std::string& muxer) {
    if (muxer == "tee" || config.target.find('|') != std::string::npos) {
        return build_tee_output_target(config, fps);
    }
    return build_udp_output_target(config.target, config.bitrate, fps);
}

std::string infer_muxer(const std::string& target) {
    if (target.find('|') != std::string::npos) {
        return "tee";
    }
    if (starts_with(target, "rtsp://")) {
        return "rtsp";
    }
    if (starts_with(target, "rtmp://")) {
        return "flv";
    }
    if (starts_with(target, "srt://")) {
        return "mpegts";
    }
    if (starts_with(target, "udp://")) {
        return "mpegts";
    }
    return "";
}

std::string resolve_output_codec(
    const std::string& requested_codec,
    int width,
    int height) {
    if ((requested_codec == "h264_nvenc") && (width > 4096 || height > 4096)) {
        return "hevc_nvenc";
    }
    return requested_codec;
}

std::string ffmpeg_error_text(int errnum) {
    char error_buffer[AV_ERROR_MAX_STRING_SIZE] = {};
    av_strerror(errnum, error_buffer, sizeof(error_buffer));
    return std::string(error_buffer);
}

bool codec_supports_cuda_hw_frames(const AVCodec* codec) {
    if (codec == nullptr) {
        return false;
    }
    for (int index = 0;; ++index) {
        const AVCodecHWConfig* config = avcodec_get_hw_config(codec, index);
        if (config == nullptr) {
            break;
        }
        if (config->device_type == AV_HWDEVICE_TYPE_CUDA &&
            config->pix_fmt == AV_PIX_FMT_CUDA &&
            (config->methods & AV_CODEC_HW_CONFIG_METHOD_HW_FRAMES_CTX) != 0) {
            return true;
        }
    }
    return false;
}

bool codec_supports_pixel_format(const AVCodec* codec, AVPixelFormat pixel_format) {
    if (codec == nullptr || codec->pix_fmts == nullptr) {
        return false;
    }
    for (const AVPixelFormat* current = codec->pix_fmts; *current != AV_PIX_FMT_NONE; ++current) {
        if (*current == pixel_format) {
            return true;
        }
    }
    return false;
}

bool try_copy_gpu_bgra_to_hw_frame(
    const cv::cuda::GpuMat& bgra_source,
    AVFrame* hw_frame,
    std::string* error_text) {
    if (bgra_source.empty()) {
        if (error_text != nullptr) {
            *error_text = "empty GPU BGRA source";
        }
        return false;
    }
    if (bgra_source.type() != CV_8UC4) {
        if (error_text != nullptr) {
            *error_text = "GPU BGRA source type mismatch";
        }
        return false;
    }
    if (hw_frame == nullptr || hw_frame->data[0] == nullptr || hw_frame->linesize[0] <= 0) {
        if (error_text != nullptr) {
            *error_text = "CUDA hw frame plane is not writable";
        }
        return false;
    }

#if HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED
    const cudaError_t copy_result = cudaMemcpy2D(
        hw_frame->data[0],
        static_cast<std::size_t>(hw_frame->linesize[0]),
        bgra_source.data,
        bgra_source.step,
        static_cast<std::size_t>(bgra_source.cols) * 4U,
        static_cast<std::size_t>(bgra_source.rows),
        cudaMemcpyDeviceToDevice);
    if (copy_result != cudaSuccess) {
        if (error_text != nullptr) {
            *error_text = cudaGetErrorString(copy_result);
        }
        return false;
    }
    const cudaError_t sync_result = cudaDeviceSynchronize();
    if (sync_result != cudaSuccess) {
        if (error_text != nullptr) {
            *error_text = cudaGetErrorString(sync_result);
        }
        return false;
    }
#else
    try {
        cv::cuda::GpuMat hw_frame_view(
            bgra_source.rows,
            bgra_source.cols,
            CV_8UC4,
            hw_frame->data[0],
            static_cast<std::size_t>(hw_frame->linesize[0]));
        bgra_source.copyTo(hw_frame_view);
        cv::cuda::Stream::Null().waitForCompletion();
    } catch (const cv::Exception& e) {
        if (error_text != nullptr) {
            *error_text = e.what();
        }
        return false;
    }
#endif
    return true;
}

void free_av_context(AVFormatContext* format_context) {
    if (format_context == nullptr) {
        return;
    }
    if ((format_context->oformat->flags & AVFMT_NOFILE) == 0 && format_context->pb != nullptr) {
        avio_closep(&format_context->pb);
    }
    avformat_free_context(format_context);
}

}  // namespace

struct GpuDirectOutputWriter::Impl {
    AVFormatContext* format_context = nullptr;
    AVCodecContext* codec_context = nullptr;
    AVStream* stream = nullptr;
    SwsContext* sws_context = nullptr;
    AVBufferRef* hw_device_context = nullptr;
    AVBufferRef* hw_frames_context = nullptr;
    AVFrame* frame = nullptr;
    bool cuda_hw_frames_active = false;
    bool bgra_bridge_active = false;
    bool bgra_direct_fill_active = false;
    bool bgra_direct_fill_disabled = false;
    int fps_num = 30;
    std::int64_t next_pts = 0;

    ~Impl() {
        if (frame != nullptr) {
            av_frame_free(&frame);
        }
        if (hw_frames_context != nullptr) {
            av_buffer_unref(&hw_frames_context);
        }
        if (hw_device_context != nullptr) {
            av_buffer_unref(&hw_device_context);
        }
        if (sws_context != nullptr) {
            sws_freeContext(sws_context);
        }
        if (codec_context != nullptr) {
            avcodec_free_context(&codec_context);
        }
        if (format_context != nullptr) {
            free_av_context(format_context);
        }
    }
};

GpuDirectOutputWriter::GpuDirectOutputWriter() = default;

GpuDirectOutputWriter::~GpuDirectOutputWriter() {
    stop();
}

bool GpuDirectOutputWriter::start(
    const hogak::engine::OutputConfig& config,
    const std::string& /*ffmpeg_bin*/,
    int width,
    int height,
    double fps,
    bool /*input_prepared*/) {
    stop();
    if (config.runtime != "gpu-direct" || config.target.empty() || width <= 0 || height <= 0) {
        return false;
    }
    if (!gpu_direct_dependency_ready()) {
        std::lock_guard<std::mutex> lock(mutex_);
        config_ = config;
        width_ = width;
        height_ = height;
        fps_ = fps;
        last_error_ = "gpu-direct dependency not ready: " + gpu_direct_dependency_status();
        command_line_ = "gpu-direct://dependency-missing";
        runtime_mode_ = "native-nvenc-unavailable";
        return false;
    }

    const bool tee_target = config.target.find('|') != std::string::npos;
    const std::string resolved_muxer =
        tee_target ? std::string("tee") : (config.muxer.empty() ? infer_muxer(config.target) : config.muxer);

    {
        std::lock_guard<std::mutex> lock(mutex_);
        config_ = config;
        width_ = width;
        height_ = height;
        fps_ = std::max(1.0, fps);
        effective_codec_ = resolve_output_codec(config.codec, width, height);
        muxer_ = resolved_muxer;
        output_target_ = build_output_target(config, fps_, muxer_);
        latest_frame_.release();
        latest_gpu_frame_.release();
        latest_frame_on_gpu_ = false;
        frame_pending_ = false;
        frames_written_ = 0;
        frames_dropped_ = 0;
        last_error_.clear();
        runtime_mode_ = "native-nvenc-bridge";
        command_line_ =
            "gpu-direct://provider=" + std::string(gpu_direct_provider()) +
            " codec=" + effective_codec_ +
            " muxer=" + (muxer_.empty() ? std::string("auto") : muxer_) +
            " target=" + output_target_;
        impl_.reset();
    }

    active_.store(true);
    thread_ = std::thread(&GpuDirectOutputWriter::run, this);
    return true;
}

void GpuDirectOutputWriter::submit(const OutputFrame& frame, std::int64_t /*timestamp_ns*/) {
    if (!active_.load() || frame.empty()) {
        return;
    }

    cv::Mat cpu_frame;
    cv::cuda::GpuMat gpu_frame;
    bool frame_on_gpu = false;
    if (frame.gpu_frame != nullptr && !frame.gpu_frame->empty()) {
        try {
            frame.gpu_frame->copyTo(gpu_frame);
            frame_on_gpu = true;
        } catch (const cv::Exception& e) {
            std::lock_guard<std::mutex> lock(mutex_);
            last_error_ = std::string("gpu-direct gpu frame copy failed: ") + e.what();
            return;
        }
    } else if (frame.cpu_frame != nullptr && !frame.cpu_frame->empty()) {
        if (frame.cpu_frame->isContinuous()) {
            frame.cpu_frame->copyTo(cpu_frame);
        } else {
            cpu_frame = frame.cpu_frame->clone();
        }
    }
    if (!frame_on_gpu && cpu_frame.empty()) {
        return;
    }
    if (frame_on_gpu && gpu_frame.empty()) {
        return;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (frame_pending_) {
        frames_dropped_ += 1;
    }
    if (frame_on_gpu) {
        latest_frame_.release();
        latest_gpu_frame_ = std::move(gpu_frame);
        latest_frame_on_gpu_ = true;
    } else {
        latest_gpu_frame_.release();
        latest_frame_ = std::move(cpu_frame);
        latest_frame_on_gpu_ = false;
    }
    frame_pending_ = true;
    condition_.notify_one();
}

void GpuDirectOutputWriter::stop() {
    active_.store(false);
    condition_.notify_all();
    if (thread_.joinable()) {
        thread_.join();
    }
}

bool GpuDirectOutputWriter::active() const noexcept {
    return active_.load();
}

std::int64_t GpuDirectOutputWriter::frames_written() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return frames_written_;
}

std::int64_t GpuDirectOutputWriter::frames_dropped() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return frames_dropped_;
}

std::string GpuDirectOutputWriter::last_error() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return last_error_;
}

std::string GpuDirectOutputWriter::effective_codec() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return effective_codec_;
}

std::string GpuDirectOutputWriter::command_line() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return command_line_;
}

std::string GpuDirectOutputWriter::runtime_mode() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return runtime_mode_;
}

std::string GpuDirectOutputWriter::muxer() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return muxer_;
}

void GpuDirectOutputWriter::run() {
    auto local_impl = std::make_unique<Impl>();
    std::string target;
    std::string codec_name;
    std::string muxer_name;
    std::string bitrate;
    int width = 0;
    int height = 0;
    double fps = 30.0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        target = output_target_;
        codec_name = effective_codec_;
        muxer_name = muxer_;
        bitrate = config_.bitrate;
        width = width_;
        height = height_;
        fps = fps_;
    }

    const AVCodec* codec = avcodec_find_encoder_by_name(codec_name.c_str());
    if (codec == nullptr) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct encoder not found: " + codec_name;
        active_.store(false);
        return;
    }

    AVFormatContext* format_context = nullptr;
    const int alloc_result = avformat_alloc_output_context2(
        &format_context,
        nullptr,
        muxer_name.empty() ? nullptr : muxer_name.c_str(),
        target.c_str());
    if (alloc_result < 0 || format_context == nullptr) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct avformat_alloc_output_context2 failed: " + ffmpeg_error_text(alloc_result);
        active_.store(false);
        return;
    }
    local_impl->format_context = format_context;
    local_impl->format_context->flags |= AVFMT_FLAG_FLUSH_PACKETS;

    AVStream* stream = avformat_new_stream(local_impl->format_context, codec);
    if (stream == nullptr) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct avformat_new_stream failed";
        active_.store(false);
        return;
    }
    local_impl->stream = stream;

    AVCodecContext* codec_context = avcodec_alloc_context3(codec);
    if (codec_context == nullptr) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct avcodec_alloc_context3 failed";
        active_.store(false);
        return;
    }
    local_impl->codec_context = codec_context;
    local_impl->fps_num = std::max(1, static_cast<int>(std::round(fps)));

    const auto bitrate_bits = parse_bitrate_bits(bitrate);
    const bool codec_is_nvenc = codec_name.find("_nvenc") != std::string::npos;
    const bool supports_cuda_frames = codec_supports_cuda_hw_frames(codec);
    const bool supports_bgra = codec_supports_pixel_format(codec, AV_PIX_FMT_BGRA);

    auto configure_base_codec_context = [&](AVCodecContext* context) {
        context->codec_type = AVMEDIA_TYPE_VIDEO;
        context->codec_id = codec->id;
        context->width = width;
        context->height = height;
        context->time_base = AVRational{1, local_impl->fps_num};
        context->framerate = AVRational{local_impl->fps_num, 1};
        context->gop_size = local_impl->fps_num;
        context->max_b_frames = 0;
        context->pix_fmt = AV_PIX_FMT_YUV420P;
        context->thread_count = 1;

        if (bitrate_bits > 0) {
            context->bit_rate = static_cast<int64_t>(bitrate_bits);
            context->rc_max_rate = static_cast<int64_t>(bitrate_bits);
            context->rc_buffer_size =
                static_cast<int>(std::max<std::int64_t>(bitrate_bits / 4, bitrate_bits / local_impl->fps_num));
        }

        if ((local_impl->format_context->oformat->flags & AVFMT_GLOBALHEADER) != 0) {
            context->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
        }
    };

    auto apply_nvenc_options = [&](AVCodecContext* context) {
        if (!codec_is_nvenc) {
            return;
        }
        av_opt_set(context->priv_data, "preset", config_.preset.c_str(), 0);
        av_opt_set(context->priv_data, "tune", "ll", 0);
        av_opt_set(context->priv_data, "rc", "cbr", 0);
        av_opt_set(context->priv_data, "zerolatency", "1", 0);
        av_opt_set(context->priv_data, "forced-idr", "1", 0);
    };

    auto release_hw_state = [&]() {
        if (codec_context->hw_frames_ctx != nullptr) {
            av_buffer_unref(&codec_context->hw_frames_ctx);
        }
        if (local_impl->hw_frames_context != nullptr) {
            av_buffer_unref(&local_impl->hw_frames_context);
        }
        if (local_impl->hw_device_context != nullptr) {
            av_buffer_unref(&local_impl->hw_device_context);
        }
        local_impl->cuda_hw_frames_active = false;
        local_impl->bgra_bridge_active = false;
        codec_context->pix_fmt = AV_PIX_FMT_YUV420P;
        codec_context->sw_pix_fmt = AV_PIX_FMT_NONE;
    };

    auto configure_cuda_hw_frames = [&](AVPixelFormat sw_format) -> int {
        AVBufferRef* hw_device_context = nullptr;
        AVBufferRef* hw_frames_context = nullptr;
        auto create_hw_device_context = [&](bool use_primary_context) -> int {
            AVDictionary* hw_device_options = nullptr;
            if (use_primary_context) {
                av_dict_set(&hw_device_options, "primary_ctx", "1", 0);
            }
            const int create_result = av_hwdevice_ctx_create(
                &hw_device_context,
                AV_HWDEVICE_TYPE_CUDA,
                nullptr,
                hw_device_options,
                0);
            av_dict_free(&hw_device_options);
            return create_result;
        };

        int hw_result = create_hw_device_context(true);
        if (hw_result < 0) {
            hw_result = create_hw_device_context(false);
        }
        if (hw_result >= 0) {
            hw_frames_context = av_hwframe_ctx_alloc(hw_device_context);
            if (hw_frames_context == nullptr) {
                hw_result = AVERROR(ENOMEM);
            }
        }
        if (hw_result >= 0) {
            auto* frames_context = reinterpret_cast<AVHWFramesContext*>(hw_frames_context->data);
            frames_context->format = AV_PIX_FMT_CUDA;
            frames_context->sw_format = sw_format;
            frames_context->width = width;
            frames_context->height = height;
            frames_context->initial_pool_size = 4;
            hw_result = av_hwframe_ctx_init(hw_frames_context);
        }
        if (hw_result >= 0) {
            codec_context->pix_fmt = AV_PIX_FMT_CUDA;
            codec_context->sw_pix_fmt = sw_format;
            codec_context->hw_frames_ctx = av_buffer_ref(hw_frames_context);
            local_impl->hw_device_context = hw_device_context;
            local_impl->hw_frames_context = hw_frames_context;
            local_impl->cuda_hw_frames_active = true;
            local_impl->bgra_bridge_active = (sw_format == AV_PIX_FMT_BGRA);
            hw_device_context = nullptr;
            hw_frames_context = nullptr;
        }
        if (hw_frames_context != nullptr) {
            av_buffer_unref(&hw_frames_context);
        }
        if (hw_device_context != nullptr) {
            av_buffer_unref(&hw_device_context);
        }
        return hw_result;
    };

    configure_base_codec_context(codec_context);

    int result = 0;
    bool codec_opened = false;
    std::string open_error_text;
    if (codec_is_nvenc && supports_cuda_frames && supports_bgra) {
        result = configure_cuda_hw_frames(AV_PIX_FMT_BGRA);
        if (result >= 0) {
            apply_nvenc_options(codec_context);
            result = avcodec_open2(codec_context, codec, nullptr);
            if (result >= 0) {
                codec_opened = true;
            } else {
                open_error_text = "gpu-direct bgra bridge avcodec_open2 failed: " + ffmpeg_error_text(result);
                release_hw_state();
            }
        } else {
            open_error_text = "gpu-direct bgra hwframe init failed: " + ffmpeg_error_text(result);
            release_hw_state();
        }
    }

    if (!codec_opened) {
        if (codec_is_nvenc && supports_cuda_frames) {
            result = configure_cuda_hw_frames(AV_PIX_FMT_NV12);
            if (result >= 0) {
                apply_nvenc_options(codec_context);
                result = avcodec_open2(codec_context, codec, nullptr);
                if (result >= 0) {
                    codec_opened = true;
                } else {
                    open_error_text = "gpu-direct avcodec_open2 failed: " + ffmpeg_error_text(result);
                    release_hw_state();
                }
            } else {
                open_error_text = "gpu-direct hwframe init failed: " + ffmpeg_error_text(result);
                release_hw_state();
            }
        } else {
            apply_nvenc_options(codec_context);
            result = avcodec_open2(codec_context, codec, nullptr);
            if (result >= 0) {
                codec_opened = true;
            } else {
                open_error_text = "gpu-direct avcodec_open2 failed: " + ffmpeg_error_text(result);
            }
        }
    }

    if (!codec_opened) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = open_error_text.empty() ? ("gpu-direct avcodec_open2 failed: " + ffmpeg_error_text(result)) : open_error_text;
        active_.store(false);
        return;
    }

    result = avcodec_parameters_from_context(stream->codecpar, codec_context);
    if (result < 0) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct avcodec_parameters_from_context failed: " + ffmpeg_error_text(result);
        active_.store(false);
        return;
    }
    stream->time_base = codec_context->time_base;

    if ((local_impl->format_context->oformat->flags & AVFMT_NOFILE) == 0) {
        result = avio_open(&local_impl->format_context->pb, target.c_str(), AVIO_FLAG_WRITE);
        if (result < 0) {
            std::lock_guard<std::mutex> lock(mutex_);
            last_error_ = "gpu-direct avio_open failed: " + ffmpeg_error_text(result);
            active_.store(false);
            return;
        }
    }

    AVDictionary* muxer_options = nullptr;
    if (muxer_name == "mpegts") {
        av_dict_set(&muxer_options, "mpegts_flags", "resend_headers+pat_pmt_at_frames", 0);
        av_dict_set(&muxer_options, "muxdelay", "0", 0);
        av_dict_set(&muxer_options, "muxpreload", "0", 0);
    } else if (muxer_name == "tee") {
        av_dict_set(&muxer_options, "use_fifo", "0", 0);
    }
    result = avformat_write_header(local_impl->format_context, &muxer_options);
    av_dict_free(&muxer_options);
    if (result < 0) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct avformat_write_header failed: " + ffmpeg_error_text(result);
        active_.store(false);
        return;
    }

    local_impl->frame = av_frame_alloc();
    if (local_impl->frame == nullptr) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct av_frame_alloc failed";
        active_.store(false);
        return;
    }
    local_impl->frame->format = local_impl->bgra_bridge_active
        ? AV_PIX_FMT_BGRA
        : (local_impl->cuda_hw_frames_active ? AV_PIX_FMT_NV12 : codec_context->pix_fmt);
    local_impl->frame->width = width;
    local_impl->frame->height = height;
    result = av_frame_get_buffer(local_impl->frame, 32);
    if (result < 0) {
        std::lock_guard<std::mutex> lock(mutex_);
        last_error_ = "gpu-direct av_frame_get_buffer failed: " + ffmpeg_error_text(result);
        active_.store(false);
        return;
    }

    if (!local_impl->bgra_bridge_active) {
        local_impl->sws_context = sws_getContext(
            width,
            height,
            AV_PIX_FMT_BGR24,
            width,
            height,
            static_cast<AVPixelFormat>(local_impl->frame->format),
            SWS_FAST_BILINEAR,
            nullptr,
            nullptr,
            nullptr);
        if (local_impl->sws_context == nullptr) {
            std::lock_guard<std::mutex> lock(mutex_);
            last_error_ = "gpu-direct sws_getContext failed";
            active_.store(false);
            return;
        }
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (local_impl->bgra_bridge_active) {
            command_line_ += " mode=cuda-hwframes-bgra-bridge";
        } else if (local_impl->cuda_hw_frames_active) {
            command_line_ += " mode=cuda-hwframes";
        } else {
            command_line_ += " mode=cpu-bridge";
        }
        impl_ = std::move(local_impl);
    }

    cv::Mat current_frame;
    cv::cuda::GpuMat current_gpu_frame;
    cv::cuda::GpuMat prepared_gpu_bgra;
    cv::Mat bridge_frame;
    cv::Mat prepared_frame;
    const cv::Mat* prepared_frame_view = nullptr;
    bool prepared_gpu_frame_ready = false;
    bool has_current_frame = false;
    bool current_frame_on_gpu = false;
    bool frame_content_dirty = false;
    const auto frame_period = std::chrono::duration_cast<std::chrono::steady_clock::duration>(
        std::chrono::duration<double>(1.0 / std::max(1.0, fps)));
    auto next_write_time = std::chrono::steady_clock::now();

    auto write_packet = [&](AVPacket* packet) -> bool {
        av_packet_rescale_ts(packet, impl_->codec_context->time_base, impl_->stream->time_base);
        packet->stream_index = impl_->stream->index;
        const int write_result = av_interleaved_write_frame(impl_->format_context, packet);
        if (write_result < 0) {
            std::lock_guard<std::mutex> lock(mutex_);
            last_error_ = "gpu-direct av_interleaved_write_frame failed: " + ffmpeg_error_text(write_result);
            active_.store(false);
            return false;
        }
        return true;
    };

    auto set_runtime_mode = [&](const char* mode) {
        if (mode == nullptr || *mode == '\0') {
            return;
        }
        std::lock_guard<std::mutex> lock(mutex_);
        if (std::string(mode) == "cuda-hwframes-bgra-direct-fill") {
            runtime_mode_ = "native-nvenc-direct";
        } else {
            runtime_mode_ = "native-nvenc-bridge";
        }
        const auto mode_pos = command_line_.find(" mode=");
        if (mode_pos == std::string::npos) {
            command_line_ += " mode=";
            command_line_ += mode;
            return;
        }
        command_line_.erase(mode_pos);
        command_line_ += " mode=";
        command_line_ += mode;
    };

    while (active_.load()) {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (!has_current_frame) {
                condition_.wait(lock, [this]() { return !active_.load() || frame_pending_; });
            } else {
                condition_.wait_until(lock, next_write_time, [this]() { return !active_.load() || frame_pending_; });
            }
            if (!active_.load()) {
                break;
            }
            if (frame_pending_) {
                if (latest_frame_on_gpu_) {
                    current_frame.release();
                    std::swap(current_gpu_frame, latest_gpu_frame_);
                    current_frame_on_gpu = !current_gpu_frame.empty();
                } else {
                    current_gpu_frame.release();
                    std::swap(current_frame, latest_frame_);
                    current_frame_on_gpu = false;
                }
                frame_pending_ = false;
                has_current_frame = current_frame_on_gpu ? !current_gpu_frame.empty() : !current_frame.empty();
                frame_content_dirty = has_current_frame;
            }
        }

        if (!has_current_frame) {
            continue;
        }

        const auto now = std::chrono::steady_clock::now();
        if (now < next_write_time) {
            continue;
        }

        const bool prepared_content_available =
            prepared_gpu_frame_ready || (prepared_frame_view != nullptr && !prepared_frame_view->empty());
        if (frame_content_dirty || !prepared_content_available) {
            bridge_frame.release();
            prepared_frame.release();
            prepared_frame_view = nullptr;
            prepared_gpu_frame_ready = false;

            if (impl_->bgra_bridge_active) {
                if (current_frame_on_gpu) {
                    try {
                        cv::cuda::cvtColor(current_gpu_frame, prepared_gpu_bgra, cv::COLOR_BGR2BGRA);
                    } catch (const cv::Exception& e) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        last_error_ = std::string("gpu-direct bgra bridge failed: ") + e.what();
                        active_.store(false);
                        break;
                    }
                    prepared_gpu_frame_ready = true;
                } else {
                    if (current_frame.empty()) {
                        continue;
                    }
                    if (current_frame.cols != width || current_frame.rows != height) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        last_error_ = "gpu-direct bgra bridge expects frame matching output size";
                        active_.store(false);
                        break;
                    }
                    if (current_frame.type() == CV_8UC4) {
                        if (!current_frame.isContinuous()) {
                            prepared_frame = current_frame.clone();
                            prepared_frame_view = &prepared_frame;
                        } else {
                            prepared_frame_view = &current_frame;
                        }
                    } else if (current_frame.type() == CV_8UC3) {
                        cv::cvtColor(current_frame, prepared_frame, cv::COLOR_BGR2BGRA);
                        prepared_frame_view = &prepared_frame;
                    } else {
                        std::lock_guard<std::mutex> lock(mutex_);
                        last_error_ = "gpu-direct bgra bridge expects BGR/BGRA frame";
                        active_.store(false);
                        break;
                    }
                }

                if (!prepared_gpu_frame_ready && (prepared_frame_view == nullptr || prepared_frame_view->empty())) {
                    continue;
                }
                if (!prepared_gpu_frame_ready &&
                    (prepared_frame_view->cols != width || prepared_frame_view->rows != height ||
                     prepared_frame_view->type() != CV_8UC4)) {
                    std::lock_guard<std::mutex> lock(mutex_);
                    last_error_ = "gpu-direct bgra bridge expects BGRA frame matching output size";
                    active_.store(false);
                    break;
                }
            } else {
                const cv::Mat* source_frame = &current_frame;
                if (current_frame_on_gpu) {
                    try {
                        current_gpu_frame.download(bridge_frame);
                    } catch (const cv::Exception& e) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        last_error_ = std::string("gpu-direct cpu bridge download failed: ") + e.what();
                        active_.store(false);
                        break;
                    }
                    source_frame = &bridge_frame;
                }

                if (source_frame->empty()) {
                    continue;
                }
                if (source_frame->cols != width || source_frame->rows != height || source_frame->type() != CV_8UC3) {
                    std::lock_guard<std::mutex> lock(mutex_);
                    last_error_ = "gpu-direct writer expects contiguous BGR frame matching output size";
                    active_.store(false);
                    break;
                }

                if (!source_frame->isContinuous()) {
                    prepared_frame = source_frame->clone();
                    prepared_frame_view = &prepared_frame;
                } else {
                    prepared_frame_view = source_frame;
                }
            }
        }
        if (!prepared_gpu_frame_ready && (prepared_frame_view == nullptr || prepared_frame_view->empty())) {
            continue;
        }

        AVFrame* frame_to_send = impl_->frame;
        AVFrame* hw_frame = nullptr;
        auto ensure_software_frame_ready = [&]() -> bool {
            const int writable_result = av_frame_make_writable(impl_->frame);
            if (writable_result < 0) {
                std::lock_guard<std::mutex> lock(mutex_);
                last_error_ = "gpu-direct av_frame_make_writable failed: " + ffmpeg_error_text(writable_result);
                active_.store(false);
                return false;
            }

            if (!frame_content_dirty) {
                return true;
            }

            if (impl_->bgra_bridge_active) {
                cv::Mat frame_bgra_view(
                    height,
                    width,
                    CV_8UC4,
                    impl_->frame->data[0],
                    static_cast<std::size_t>(impl_->frame->linesize[0]));
                if (prepared_gpu_frame_ready) {
                    try {
                        prepared_gpu_bgra.download(frame_bgra_view);
                    } catch (const cv::Exception& e) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        last_error_ = std::string("gpu-direct bgra download failed: ") + e.what();
                        active_.store(false);
                        return false;
                    }
                } else {
                    prepared_frame_view->copyTo(frame_bgra_view);
                }
            } else {
                uint8_t* src_slices[4] = {const_cast<uint8_t*>(prepared_frame_view->ptr<uint8_t>()), nullptr, nullptr, nullptr};
                int src_strides[4] = {static_cast<int>(prepared_frame_view->step[0]), 0, 0, 0};
                sws_scale(
                    impl_->sws_context,
                    src_slices,
                    src_strides,
                    0,
                    height,
                    impl_->frame->data,
                    impl_->frame->linesize);
            }
            frame_content_dirty = false;
            return true;
        };

        if (impl_->cuda_hw_frames_active) {
            hw_frame = av_frame_alloc();
            if (hw_frame == nullptr) {
                std::lock_guard<std::mutex> lock(mutex_);
                last_error_ = "gpu-direct av_frame_alloc failed for hw frame";
                active_.store(false);
                break;
            }
            hw_frame->format = AV_PIX_FMT_CUDA;
            hw_frame->width = width;
            hw_frame->height = height;
            result = av_hwframe_get_buffer(impl_->hw_frames_context, hw_frame, 0);
            if (result < 0) {
                av_frame_free(&hw_frame);
                std::lock_guard<std::mutex> lock(mutex_);
                last_error_ = "gpu-direct av_hwframe_get_buffer failed: " + ffmpeg_error_text(result);
                active_.store(false);
                break;
            }

            bool hw_frame_filled_directly = false;
            if (impl_->bgra_bridge_active && prepared_gpu_frame_ready && !impl_->bgra_direct_fill_disabled) {
                std::string direct_fill_error;
                if (try_copy_gpu_bgra_to_hw_frame(prepared_gpu_bgra, hw_frame, &direct_fill_error)) {
                    hw_frame_filled_directly = true;
                    frame_content_dirty = false;
                    if (!impl_->bgra_direct_fill_active) {
                        impl_->bgra_direct_fill_active = true;
                        set_runtime_mode("cuda-hwframes-bgra-direct-fill");
                    }
                } else {
                    const bool first_disable = !impl_->bgra_direct_fill_disabled;
                    impl_->bgra_direct_fill_disabled = true;
                    impl_->bgra_direct_fill_active = false;
                    set_runtime_mode("cuda-hwframes-bgra-bridge");
                    if (first_disable && !direct_fill_error.empty()) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        command_line_ += " direct-fill-error=" + sanitize_runtime_token(direct_fill_error);
                    }
                }
            }

            if (!hw_frame_filled_directly) {
                if (!ensure_software_frame_ready()) {
                    av_frame_free(&hw_frame);
                    break;
                }
                result = av_hwframe_transfer_data(hw_frame, impl_->frame, 0);
                if (result < 0) {
                    av_frame_free(&hw_frame);
                    std::lock_guard<std::mutex> lock(mutex_);
                    last_error_ = "gpu-direct av_hwframe_transfer_data failed: " + ffmpeg_error_text(result);
                    active_.store(false);
                    break;
                }
            }
            frame_to_send = hw_frame;
        } else if (!ensure_software_frame_ready()) {
            break;
        }

        frame_to_send->pts = impl_->next_pts++;
        result = avcodec_send_frame(impl_->codec_context, frame_to_send);
        if (hw_frame != nullptr) {
            av_frame_free(&hw_frame);
        }
        if (result < 0) {
            std::lock_guard<std::mutex> lock(mutex_);
            last_error_ = "gpu-direct avcodec_send_frame failed: " + ffmpeg_error_text(result);
            active_.store(false);
            break;
        }

        while (result >= 0) {
            AVPacket packet;
            av_init_packet(&packet);
            packet.data = nullptr;
            packet.size = 0;
            result = avcodec_receive_packet(impl_->codec_context, &packet);
            if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) {
                av_packet_unref(&packet);
                break;
            }
            if (result < 0) {
                av_packet_unref(&packet);
                std::lock_guard<std::mutex> lock(mutex_);
                last_error_ = "gpu-direct avcodec_receive_packet failed: " + ffmpeg_error_text(result);
                active_.store(false);
                break;
            }
            if (!write_packet(&packet)) {
                av_packet_unref(&packet);
                break;
            }
            {
                std::lock_guard<std::mutex> lock(mutex_);
                frames_written_ += 1;
            }
            av_packet_unref(&packet);
        }

        next_write_time = now + frame_period;
    }

    if (impl_ != nullptr && impl_->codec_context != nullptr && impl_->format_context != nullptr) {
        avcodec_send_frame(impl_->codec_context, nullptr);
        while (true) {
            AVPacket packet;
            av_init_packet(&packet);
            packet.data = nullptr;
            packet.size = 0;
            const int receive_result = avcodec_receive_packet(impl_->codec_context, &packet);
            if (receive_result == AVERROR(EAGAIN) || receive_result == AVERROR_EOF) {
                av_packet_unref(&packet);
                break;
            }
            if (receive_result < 0) {
                av_packet_unref(&packet);
                break;
            }
            if (!write_packet(&packet)) {
                av_packet_unref(&packet);
                break;
            }
            {
                std::lock_guard<std::mutex> lock(mutex_);
                frames_written_ += 1;
            }
            av_packet_unref(&packet);
        }
        av_write_trailer(impl_->format_context);
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        impl_.reset();
    }
}

}  // namespace hogak::output
