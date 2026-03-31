#include "input/ffmpeg_rtsp_reader.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <memory>
#include <sstream>
#include <thread>
#include <utility>
#include <vector>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavcodec/defs.h>
#include <libavformat/avformat.h>
#include <libavutil/dict.h>
#include <libavutil/error.h>
#include <libavutil/hwcontext.h>
#include <libavutil/opt.h>
#include <libswscale/swscale.h>
}

#include <opencv2/imgproc.hpp>

namespace hogak::input {

namespace {

constexpr int kFreezeProbeWidth = 64;
constexpr int kFreezeProbeHeight = 36;
constexpr int kFreezeProbeEveryN = 8;
constexpr double kFreezeMotionThreshold = 0.01;
constexpr double kFreezeRestartSec = 5.0;
constexpr double kLateFrameIntervalMs = 45.0;
constexpr std::size_t kReaderMetricWindow = 90;
constexpr std::size_t kPacketWallclockHintWindow = 256;
constexpr std::int64_t kUdpReceiveFifoBytes = 1 * 1024 * 1024;

bool input_pipe_format_is_nv12(const std::string& format) {
    return format == "nv12";
}

int input_frame_rows(int height, const std::string& format) {
    if (input_pipe_format_is_nv12(format)) {
        return height + (height / 2);
    }
    return height;
}

int input_frame_type(const std::string& format) {
    if (input_pipe_format_is_nv12(format)) {
        return CV_8UC1;
    }
    return CV_8UC3;
}

cv::Mat allocate_input_frame(int width, int height, const std::string& format) {
    return cv::Mat(input_frame_rows(height, format), width, input_frame_type(format));
}

cv::Mat nv12_luma_plane(const cv::Mat& nv12_frame, int width, int height) {
    if (nv12_frame.empty() || nv12_frame.type() != CV_8UC1 || nv12_frame.cols != width || nv12_frame.rows < height) {
        return {};
    }
    return cv::Mat(height, width, CV_8UC1, const_cast<std::uint8_t*>(nv12_frame.ptr<std::uint8_t>(0)), nv12_frame.step);
}

double fps_from_count(std::size_t count, std::int64_t span_ns) {
    if (count < 2 || span_ns <= 0) {
        return 0.0;
    }
    return static_cast<double>(count - 1) * 1'000'000'000.0 / static_cast<double>(span_ns);
}

cv::Mat make_freeze_probe_gray(const cv::Mat& current_frame, const std::string& format, int width, int height) {
    if (current_frame.empty()) {
        return {};
    }
    if (input_pipe_format_is_nv12(format)) {
        cv::Mat gray = nv12_luma_plane(current_frame, width, height);
        if (gray.empty()) {
            return {};
        }
        cv::Mat resized;
        cv::resize(gray, resized, cv::Size(kFreezeProbeWidth, kFreezeProbeHeight), 0.0, 0.0, cv::INTER_AREA);
        return resized;
    }
    cv::Mat gray;
    cv::cvtColor(current_frame, gray, cv::COLOR_BGR2GRAY);
    cv::resize(gray, gray, cv::Size(kFreezeProbeWidth, kFreezeProbeHeight), 0.0, 0.0, cv::INTER_AREA);
    return gray;
}

double frame_motion_score(const cv::Mat& previous_probe_gray, const cv::Mat& current_probe_gray) {
    if (previous_probe_gray.empty() || current_probe_gray.empty()) {
        return 0.0;
    }
    if (current_probe_gray.size() != previous_probe_gray.size()) {
        return 0.0;
    }
    cv::Mat diff;
    cv::absdiff(previous_probe_gray, current_probe_gray, diff);
    return cv::mean(diff)[0];
}

double probe_luma_mean(const cv::Mat& probe_gray) {
    if (probe_gray.empty()) {
        return 0.0;
    }
    return cv::mean(probe_gray)[0];
}

bool is_effectively_identical_probe(const cv::Mat& previous_probe_gray, const cv::Mat& current_probe_gray) {
    if (previous_probe_gray.empty() || current_probe_gray.empty()) {
        return false;
    }
    if (current_probe_gray.size() != previous_probe_gray.size()) {
        return false;
    }
    return cv::countNonZero(current_probe_gray != previous_probe_gray) == 0;
}

bool can_reuse_frame_storage(const cv::Mat& frame, int width, int height, const std::string& format) {
    return !frame.empty() &&
        frame.rows == input_frame_rows(height, format) &&
        frame.cols == width &&
        frame.type() == input_frame_type(format) &&
        frame.isContinuous() &&
        frame.u != nullptr &&
        frame.u->refcount == 1;
}

std::string resolve_ffmpeg_bin(const std::string& explicit_path) {
    if (!explicit_path.empty()) {
        return explicit_path;
    }

    char* env_value = nullptr;
    std::size_t env_size = 0;
    if (_dupenv_s(&env_value, &env_size, "FFMPEG_BIN") == 0 && env_value != nullptr && env_size > 0) {
        std::string resolved(env_value);
        free(env_value);
        return resolved;
    }

    const auto local = std::filesystem::path(".") / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe";
    if (std::filesystem::exists(local)) {
        return local.string();
    }

    return "ffmpeg";
}

std::string ffmpeg_error_text(int error_code) {
    char error_buffer[AV_ERROR_MAX_STRING_SIZE] = {};
    av_strerror(error_code, error_buffer, sizeof(error_buffer));
    return std::string(error_buffer);
}

AVPixelFormat input_pipe_av_pix_fmt(const std::string& format) {
    return input_pipe_format_is_nv12(format) ? AV_PIX_FMT_NV12 : AV_PIX_FMT_BGR24;
}

std::int64_t rescale_timestamp_to_ns(std::int64_t timestamp, AVRational time_base) {
    if (timestamp == AV_NOPTS_VALUE || timestamp <= 0 || time_base.num <= 0 || time_base.den <= 0) {
        return 0;
    }
    return av_rescale_q(timestamp, time_base, AVRational{1, 1'000'000'000});
}

std::int64_t wallclock_now_ns() {
    using clock = std::chrono::system_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(clock::now().time_since_epoch()).count();
}

AVPixelFormat resolve_cuda_hw_pix_fmt(const AVCodec* codec) {
    if (codec == nullptr) {
        return AV_PIX_FMT_NONE;
    }
    for (int index = 0;; ++index) {
        const AVCodecHWConfig* config = avcodec_get_hw_config(codec, index);
        if (config == nullptr) {
            break;
        }
        if (config->device_type == AV_HWDEVICE_TYPE_CUDA &&
            config->pix_fmt == AV_PIX_FMT_CUDA &&
            ((config->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX) != 0 ||
             (config->methods & AV_CODEC_HW_CONFIG_METHOD_HW_FRAMES_CTX) != 0)) {
            return config->pix_fmt;
        }
    }
    return AV_PIX_FMT_NONE;
}

struct DecoderHwState {
    AVPixelFormat hw_pixel_format = AV_PIX_FMT_NONE;
};

AVPixelFormat select_decoder_output_format(AVCodecContext* codec_context, const AVPixelFormat* pixel_formats) {
    if (codec_context != nullptr && codec_context->opaque != nullptr) {
        const auto* hw_state = static_cast<const DecoderHwState*>(codec_context->opaque);
        if (hw_state->hw_pixel_format != AV_PIX_FMT_NONE) {
            for (const AVPixelFormat* current = pixel_formats; current != nullptr && *current != AV_PIX_FMT_NONE; ++current) {
                if (*current == hw_state->hw_pixel_format) {
                    return *current;
                }
            }
        }
    }
    return (pixel_formats != nullptr) ? pixel_formats[0] : AV_PIX_FMT_NONE;
}

struct PacketWallclockHint {
    std::int64_t pts_raw = AV_NOPTS_VALUE;
    std::int64_t dts_raw = AV_NOPTS_VALUE;
    std::int64_t wallclock_ns = 0;
};

std::int64_t packet_prft_wallclock_ns(const AVPacket& packet) {
    std::size_t side_data_size = 0;
    const auto* side_data = av_packet_get_side_data(&packet, AV_PKT_DATA_PRFT, &side_data_size);
    if (side_data == nullptr || side_data_size < sizeof(AVProducerReferenceTime)) {
        return 0;
    }
    const auto* prft = reinterpret_cast<const AVProducerReferenceTime*>(side_data);
    if (prft->wallclock <= 0) {
        return 0;
    }
    return prft->wallclock * 1000;
}

void push_packet_wallclock_hint(std::deque<PacketWallclockHint>* hints_out, const AVPacket& packet) {
    if (hints_out == nullptr) {
        return;
    }
    const auto wallclock_ns = packet_prft_wallclock_ns(packet);
    if (wallclock_ns <= 0) {
        return;
    }
    hints_out->push_back(PacketWallclockHint{packet.pts, packet.dts, wallclock_ns});
    while (hints_out->size() > kPacketWallclockHintWindow) {
        hints_out->pop_front();
    }
}

bool resolve_frame_wallclock_hint(
    const std::deque<PacketWallclockHint>& hints,
    std::int64_t source_pts_raw,
    std::int64_t frame_pts_raw,
    std::int64_t* source_dts_raw_out,
    std::int64_t* source_wallclock_ns_out) {
    if (source_dts_raw_out != nullptr) {
        *source_dts_raw_out = AV_NOPTS_VALUE;
    }
    if (source_wallclock_ns_out != nullptr) {
        *source_wallclock_ns_out = 0;
    }
    for (auto it = hints.rbegin(); it != hints.rend(); ++it) {
        const bool pts_match =
            (source_pts_raw != AV_NOPTS_VALUE && it->pts_raw == source_pts_raw) ||
            (frame_pts_raw != AV_NOPTS_VALUE && it->pts_raw == frame_pts_raw);
        const bool dts_match =
            (source_pts_raw != AV_NOPTS_VALUE && it->dts_raw == source_pts_raw) ||
            (frame_pts_raw != AV_NOPTS_VALUE && it->dts_raw == frame_pts_raw);
        if (!pts_match && !dts_match) {
            continue;
        }
        if (source_dts_raw_out != nullptr) {
            *source_dts_raw_out = it->dts_raw;
        }
        if (source_wallclock_ns_out != nullptr) {
            *source_wallclock_ns_out = it->wallclock_ns;
        }
        return true;
    }
    return false;
}

bool ensure_reusable_input_frame(cv::Mat* frame, int width, int height, const std::string& format) {
    if (frame == nullptr) {
        return false;
    }
    if (frame->empty() ||
        frame->rows != input_frame_rows(height, format) ||
        frame->cols != width ||
        frame->type() != input_frame_type(format) ||
        !frame->isContinuous()) {
        *frame = allocate_input_frame(width, height, format);
    }
    return !frame->empty() && frame->isContinuous();
}

bool convert_decoded_frame_to_input_format(
    const AVFrame* source_frame,
    const hogak::engine::StreamConfig& config,
    SwsContext** sws_context,
    cv::Mat* output_frame,
    std::string* error_out) {
    if (error_out != nullptr) {
        error_out->clear();
    }
    if (source_frame == nullptr || output_frame == nullptr) {
        if (error_out != nullptr) {
            *error_out = "missing decode output frame";
        }
        return false;
    }
    if (!ensure_reusable_input_frame(output_frame, config.width, config.height, config.input_pipe_format)) {
        if (error_out != nullptr) {
            *error_out = "failed to allocate reader frame buffer";
        }
        return false;
    }

    const auto destination_format = input_pipe_av_pix_fmt(config.input_pipe_format);
    *sws_context = sws_getCachedContext(
        *sws_context,
        source_frame->width,
        source_frame->height,
        static_cast<AVPixelFormat>(source_frame->format),
        config.width,
        config.height,
        destination_format,
        SWS_FAST_BILINEAR,
        nullptr,
        nullptr,
        nullptr);
    if (*sws_context == nullptr) {
        if (error_out != nullptr) {
            *error_out = "sws_getCachedContext failed";
        }
        return false;
    }

    uint8_t* destination_data[4] = {nullptr, nullptr, nullptr, nullptr};
    int destination_linesize[4] = {0, 0, 0, 0};
    if (input_pipe_format_is_nv12(config.input_pipe_format)) {
        destination_data[0] = output_frame->data;
        destination_linesize[0] = static_cast<int>(output_frame->step[0]);
        destination_data[1] = output_frame->data + (static_cast<std::ptrdiff_t>(output_frame->step[0]) * config.height);
        destination_linesize[1] = static_cast<int>(output_frame->step[0]);
    } else {
        destination_data[0] = output_frame->data;
        destination_linesize[0] = static_cast<int>(output_frame->step[0]);
    }

    const int scaled_height = sws_scale(
        *sws_context,
        source_frame->data,
        source_frame->linesize,
        0,
        source_frame->height,
        destination_data,
        destination_linesize);
    if (scaled_height <= 0) {
        if (error_out != nullptr) {
            *error_out = "sws_scale failed";
        }
        return false;
    }
    return true;
}

struct ReaderSession {
    AVFormatContext* format_context = nullptr;
    AVCodecContext* codec_context = nullptr;
    AVStream* video_stream = nullptr;
    AVBufferRef* hw_device_context = nullptr;
    AVFrame* decoded_frame = nullptr;
    AVFrame* transfer_frame = nullptr;
    AVPacket* packet = nullptr;
    SwsContext* sws_context = nullptr;
    DecoderHwState hw_state{};
    int video_stream_index = -1;
    bool cuda_decode_active = false;

    ~ReaderSession() {
        if (sws_context != nullptr) {
            sws_freeContext(sws_context);
        }
        if (packet != nullptr) {
            av_packet_free(&packet);
        }
        if (transfer_frame != nullptr) {
            av_frame_free(&transfer_frame);
        }
        if (decoded_frame != nullptr) {
            av_frame_free(&decoded_frame);
        }
        if (hw_device_context != nullptr) {
            av_buffer_unref(&hw_device_context);
        }
        if (codec_context != nullptr) {
            avcodec_free_context(&codec_context);
        }
        if (format_context != nullptr) {
            avformat_close_input(&format_context);
        }
    }
};

bool open_decoder_context(
    ReaderSession* session,
    const AVCodecParameters* codec_parameters,
    const AVRational time_base,
    const std::string& input_runtime,
    std::string* error_out) {
    if (session == nullptr || codec_parameters == nullptr) {
        if (error_out != nullptr) {
            *error_out = "invalid decoder initialization request";
        }
        return false;
    }

    auto open_with_mode = [&](bool try_cuda, std::string* open_error_out) -> bool {
        const AVCodec* decoder = avcodec_find_decoder(codec_parameters->codec_id);
        if (decoder == nullptr) {
            if (open_error_out != nullptr) {
                *open_error_out = "decoder not found for codec_id=" + std::to_string(codec_parameters->codec_id);
            }
            return false;
        }

        const auto free_codec_context = [](AVCodecContext* context) {
            if (context != nullptr) {
                avcodec_free_context(&context);
            }
        };
        std::unique_ptr<AVCodecContext, decltype(free_codec_context)> codec_context(
            avcodec_alloc_context3(decoder),
            free_codec_context);
        if (!codec_context) {
            if (open_error_out != nullptr) {
                *open_error_out = "avcodec_alloc_context3 failed";
            }
            return false;
        }
        if (avcodec_parameters_to_context(codec_context.get(), codec_parameters) < 0) {
            if (open_error_out != nullptr) {
                *open_error_out = "avcodec_parameters_to_context failed";
            }
            return false;
        }
        codec_context->pkt_timebase = time_base;
        codec_context->thread_count = 0;
        codec_context->flags |= AV_CODEC_FLAG_LOW_DELAY;

        AVBufferRef* hw_device_context = nullptr;
        DecoderHwState hw_state{};
        if (try_cuda) {
            hw_state.hw_pixel_format = resolve_cuda_hw_pix_fmt(decoder);
            if (hw_state.hw_pixel_format == AV_PIX_FMT_NONE) {
                if (open_error_out != nullptr) {
                    *open_error_out = "cuda hwaccel pixel format unavailable";
                }
                return false;
            }
            AVBufferRef* raw_hw_device_context = nullptr;
            const int create_result =
                av_hwdevice_ctx_create(&raw_hw_device_context, AV_HWDEVICE_TYPE_CUDA, nullptr, nullptr, 0);
            if (create_result < 0 || raw_hw_device_context == nullptr) {
                if (open_error_out != nullptr) {
                    *open_error_out = "av_hwdevice_ctx_create(cuda) failed: " + ffmpeg_error_text(create_result);
                }
                return false;
            }
            hw_device_context = raw_hw_device_context;
            codec_context->opaque = &hw_state;
            codec_context->get_format = &select_decoder_output_format;
            codec_context->hw_device_ctx = av_buffer_ref(hw_device_context);
            if (codec_context->hw_device_ctx == nullptr) {
                if (open_error_out != nullptr) {
                    *open_error_out = "av_buffer_ref(hw_device_ctx) failed";
                }
                av_buffer_unref(&hw_device_context);
                return false;
            }
        }

        const int open_result = avcodec_open2(codec_context.get(), decoder, nullptr);
        if (open_result < 0) {
            if (open_error_out != nullptr) {
                *open_error_out = "avcodec_open2 failed: " + ffmpeg_error_text(open_result);
            }
            av_buffer_unref(&hw_device_context);
            return false;
        }

        session->codec_context = codec_context.release();
        session->hw_device_context = hw_device_context;
        session->hw_state = hw_state;
        session->cuda_decode_active = try_cuda;
        return true;
    };

    std::string open_error;
    if (input_runtime == "ffmpeg-cuda" && open_with_mode(true, &open_error)) {
        return true;
    }
    if (open_with_mode(false, &open_error)) {
        return true;
    }
    if (error_out != nullptr) {
        *error_out = open_error;
    }
    return false;
}

bool open_reader_session(
    const hogak::engine::StreamConfig& config,
    const std::string& input_runtime,
    ReaderSession* session,
    std::string* error_out) {
    if (error_out != nullptr) {
        error_out->clear();
    }
    if (session == nullptr) {
        if (error_out != nullptr) {
            *error_out = "reader session is null";
        }
        return false;
    }

    avformat_network_init();

    AVFormatContext* format_context = nullptr;
    AVDictionary* options = nullptr;
    const auto timeout_us = static_cast<std::int64_t>(std::max(1.0, config.timeout_sec) * 1'000'000.0);
    std::string transport = config.transport;
    std::transform(
        transport.begin(),
        transport.end(),
        transport.begin(),
        [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    av_dict_set(&options, "rtsp_transport", config.transport.c_str(), 0);
    av_dict_set(&options, "fflags", "nobuffer", 0);
    av_dict_set(&options, "flags", "low_delay", 0);
    av_dict_set_int(&options, "rw_timeout", timeout_us, 0);
    av_dict_set_int(&options, "stimeout", timeout_us, 0);
    av_dict_set_int(&options, "timeout", timeout_us, 0);
    if (transport == "udp") {
        av_dict_set_int(&options, "fifo_size", kUdpReceiveFifoBytes, 0);
        av_dict_set(&options, "overrun_nonfatal", "1", 0);
    }

    const int open_result = avformat_open_input(&format_context, config.url.c_str(), nullptr, &options);
    av_dict_free(&options);
    if (open_result < 0 || format_context == nullptr) {
        if (error_out != nullptr) {
            *error_out = "avformat_open_input failed: " + ffmpeg_error_text(open_result);
        }
        if (format_context != nullptr) {
            avformat_close_input(&format_context);
        }
        return false;
    }

    const int stream_info_result = avformat_find_stream_info(format_context, nullptr);
    if (stream_info_result < 0) {
        if (error_out != nullptr) {
            *error_out = "avformat_find_stream_info failed: " + ffmpeg_error_text(stream_info_result);
        }
        avformat_close_input(&format_context);
        return false;
    }

    const int video_stream_index = av_find_best_stream(format_context, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    if (video_stream_index < 0) {
        if (error_out != nullptr) {
            *error_out = "av_find_best_stream(video) failed: " + ffmpeg_error_text(video_stream_index);
        }
        avformat_close_input(&format_context);
        return false;
    }
    AVStream* video_stream = format_context->streams[video_stream_index];
    if (video_stream == nullptr || video_stream->codecpar == nullptr) {
        if (error_out != nullptr) {
            *error_out = "video stream codec parameters unavailable";
        }
        avformat_close_input(&format_context);
        return false;
    }

    session->format_context = format_context;
    session->video_stream_index = video_stream_index;
    session->video_stream = video_stream;
    if (!open_decoder_context(session, video_stream->codecpar, video_stream->time_base, input_runtime, error_out)) {
        return false;
    }

    session->decoded_frame = av_frame_alloc();
    session->transfer_frame = av_frame_alloc();
    session->packet = av_packet_alloc();
    if (session->decoded_frame == nullptr || session->transfer_frame == nullptr || session->packet == nullptr) {
        if (error_out != nullptr) {
            *error_out = "failed to allocate ffmpeg decode buffers";
        }
        return false;
    }
    return true;
}

}  // namespace

FfmpegRtspReader::~FfmpegRtspReader() {
    stop();
}

bool FfmpegRtspReader::start(
    const hogak::engine::StreamConfig& config,
    const std::string& ffmpeg_bin,
    const std::string& input_runtime) {
    stop();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        config_ = config;
        ffmpeg_bin_ = resolve_ffmpeg_bin(ffmpeg_bin);
        input_runtime_ = input_runtime;
        snapshot_ = ReaderSnapshot{};
        frames_.clear();
        frames_.resize(static_cast<std::size_t>(std::max(1, config.max_buffered_frames)));
        frame_start_index_ = 0;
        frame_count_ = 0;
    }
    running_.store(true);
    thread_ = std::thread(&FfmpegRtspReader::run, this);
    return true;
}

void FfmpegRtspReader::stop() {
    running_.store(false);
    if (thread_.joinable()) {
        thread_.join();
    }
}

ReaderSnapshot FfmpegRtspReader::snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return snapshot_;
}

std::vector<BufferedFrameInfo> FfmpegRtspReader::buffered_frame_infos() const {
    std::vector<BufferedFrameInfo> infos;
    buffered_frame_infos(&infos);
    return infos;
}

void FfmpegRtspReader::buffered_frame_infos(std::vector<BufferedFrameInfo>* infos_out) const {
    if (infos_out == nullptr) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    infos_out->clear();
    infos_out->reserve(buffered_frame_count_locked());
    for (std::size_t index = 0; index < buffered_frame_count_locked(); ++index) {
        const auto* frame = buffered_frame_at_locked(index);
        if (frame == nullptr) {
            continue;
        }
        BufferedFrameInfo info;
        info.frame = frame->frame;
        info.seq = frame->seq;
        info.timestamp_ns = frame->arrival_timestamp_ns;
        info.arrival_timestamp_ns = frame->arrival_timestamp_ns;
        info.source_pts_ns = frame->source_pts_ns;
        info.source_dts_ns = frame->source_dts_ns;
        info.source_wallclock_ns = frame->source_wallclock_ns;
        info.source_time_valid = frame->source_time_valid;
        info.source_time_comparable = frame->source_time_comparable;
        info.source_time_kind = frame->source_time_kind;
        info.motion_score = frame->motion_score;
        info.luma_mean = frame->luma_mean;
        infos_out->push_back(std::move(info));
    }
}

bool FfmpegRtspReader::running() const noexcept {
    return running_.load();
}

bool FfmpegRtspReader::copy_latest_frame(
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out,
    BufferedFrameInfo* info_out) const {
    if (frame_out == nullptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto* latest = latest_buffered_frame_locked();
    if (!snapshot_.has_frame || latest == nullptr) {
        return false;
    }
    return copy_buffered_frame(*latest, frame_out, seq_out, ts_out, info_out);
}

bool FfmpegRtspReader::copy_oldest_frame(
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out,
    BufferedFrameInfo* info_out) const {
    if (frame_out == nullptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto* oldest = oldest_buffered_frame_locked();
    if (!snapshot_.has_frame || oldest == nullptr) {
        return false;
    }
    return copy_buffered_frame(*oldest, frame_out, seq_out, ts_out, info_out);
}

bool FfmpegRtspReader::copy_frame_by_seq(
    std::int64_t seq,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out,
    BufferedFrameInfo* info_out) const {
    if (frame_out == nullptr) {
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!snapshot_.has_frame || buffered_frame_count_locked() == 0) {
        return false;
    }

    for (std::size_t index = buffered_frame_count_locked(); index > 0; --index) {
        const auto* buffered = buffered_frame_at_locked(index - 1);
        if (buffered != nullptr && buffered->seq == seq) {
            return copy_buffered_frame(*buffered, frame_out, seq_out, ts_out, info_out);
        }
    }
    return false;
}

bool FfmpegRtspReader::copy_closest_frame(
    std::int64_t target_ts_ns,
    bool prefer_past,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out,
    FrameTimeDomain time_domain,
    BufferedFrameInfo* info_out) const {
    if (frame_out == nullptr) {
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!snapshot_.has_frame || buffered_frame_count_locked() == 0) {
        return false;
    }

    const BufferedFrame* best = nullptr;
    const BufferedFrame* best_past = nullptr;
    std::int64_t best_delta = 0;
    std::int64_t best_past_delta = 0;

    for (std::size_t index = 0; index < buffered_frame_count_locked(); ++index) {
        const auto* buffered_ptr = buffered_frame_at_locked(index);
        if (buffered_ptr == nullptr) {
            continue;
        }
        BufferedFrameInfo info;
        info.seq = buffered_ptr->seq;
        info.timestamp_ns = buffered_ptr->arrival_timestamp_ns;
        info.arrival_timestamp_ns = buffered_ptr->arrival_timestamp_ns;
        info.source_pts_ns = buffered_ptr->source_pts_ns;
        info.source_dts_ns = buffered_ptr->source_dts_ns;
        info.source_wallclock_ns = buffered_ptr->source_wallclock_ns;
        info.source_time_valid = buffered_ptr->source_time_valid;
        info.source_time_comparable = buffered_ptr->source_time_comparable;
        info.source_time_kind = buffered_ptr->source_time_kind;
        info.motion_score = buffered_ptr->motion_score;
        info.luma_mean = buffered_ptr->luma_mean;
        if (!info.has_time(time_domain)) {
            continue;
        }
        const auto time_ns = info.resolve_time_ns(time_domain);
        const auto delta = std::llabs(time_ns - target_ts_ns);
        if (best == nullptr || delta < best_delta || (delta == best_delta && buffered_ptr->seq > best->seq)) {
            best = buffered_ptr;
            best_delta = delta;
        }
        if (time_ns <= target_ts_ns) {
            const auto past_delta = target_ts_ns - time_ns;
            if (best_past == nullptr || past_delta < best_past_delta || (past_delta == best_past_delta && buffered_ptr->seq > best_past->seq)) {
                best_past = buffered_ptr;
                best_past_delta = past_delta;
            }
        }
    }

    if (prefer_past && best_past != nullptr) {
        return copy_buffered_frame(*best_past, frame_out, seq_out, ts_out, info_out);
    }
    if (best == nullptr) {
        return false;
    }
    return copy_buffered_frame(*best, frame_out, seq_out, ts_out, info_out);
}

void FfmpegRtspReader::run() {
    std::deque<std::int64_t> receive_times_ns;
    std::deque<double> frame_intervals_ms;
    std::deque<double> read_durations_ms;
    std::deque<PacketWallclockHint> packet_wallclock_hints;
    cv::Mat read_frame = allocate_input_frame(config_.width, config_.height, config_.input_pipe_format);
    cv::Mat recycled_frame;
    cv::Mat previous_probe_gray;
    std::int64_t freeze_started_ns = 0;
    std::int64_t freeze_probe_index = 0;
    std::int64_t last_receive_ts_ns = 0;
    double last_motion_mean = 0.0;
    double last_luma_mean = 0.0;
    double last_frame_interval_ms = 0.0;
    double max_frame_interval_ms = 0.0;
    double frame_intervals_sum_ms = 0.0;
    double read_durations_sum_ms = 0.0;
    std::int64_t late_frame_intervals = 0;

    while (running_.load()) {
        ReaderSession session;
        std::string session_error;
        if (!open_reader_session(config_, input_runtime_, &session, &session_error)) {
            {
                std::lock_guard<std::mutex> lock(mutex_);
                snapshot_.last_error = session_error;
                snapshot_.launch_failures += 1;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(
                static_cast<int>(std::max(0.2, config_.reconnect_cooldown_sec) * 1000.0)));
            continue;
        }

        packet_wallclock_hints.clear();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            snapshot_.last_error.clear();
            snapshot_.content_frozen = false;
            snapshot_.frozen_duration_sec = 0.0;
        }

        while (running_.load()) {
            const auto read_started_ns = now_ns();
            const int read_result = av_read_frame(session.format_context, session.packet);
            if (read_result < 0) {
                std::lock_guard<std::mutex> lock(mutex_);
                snapshot_.read_failures += 1;
                if (snapshot_.last_error.empty()) {
                    snapshot_.last_error = "av_read_frame failed: " + ffmpeg_error_text(read_result);
                }
                break;
            }

            if (session.packet->stream_index != session.video_stream_index) {
                av_packet_unref(session.packet);
                continue;
            }

            push_packet_wallclock_hint(&packet_wallclock_hints, *session.packet);

            const int send_result = avcodec_send_packet(session.codec_context, session.packet);
            av_packet_unref(session.packet);
            if (send_result < 0) {
                std::lock_guard<std::mutex> lock(mutex_);
                snapshot_.read_failures += 1;
                snapshot_.last_error = "avcodec_send_packet failed: " + ffmpeg_error_text(send_result);
                break;
            }

            while (running_.load()) {
                const int receive_result = avcodec_receive_frame(session.codec_context, session.decoded_frame);
                if (receive_result == AVERROR(EAGAIN)) {
                    break;
                }
                if (receive_result == AVERROR_EOF) {
                    break;
                }
                if (receive_result < 0) {
                    std::lock_guard<std::mutex> lock(mutex_);
                    snapshot_.read_failures += 1;
                    snapshot_.last_error = "avcodec_receive_frame failed: " + ffmpeg_error_text(receive_result);
                    break;
                }

                const AVFrame* source_frame = session.decoded_frame;
                if (session.decoded_frame->format == AV_PIX_FMT_CUDA) {
                    av_frame_unref(session.transfer_frame);
                    const int transfer_result = av_hwframe_transfer_data(session.transfer_frame, session.decoded_frame, 0);
                    if (transfer_result < 0) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        snapshot_.read_failures += 1;
                        snapshot_.last_error = "av_hwframe_transfer_data failed: " + ffmpeg_error_text(transfer_result);
                        av_frame_unref(session.decoded_frame);
                        break;
                    }
                    const int copy_props_result = av_frame_copy_props(session.transfer_frame, session.decoded_frame);
                    if (copy_props_result < 0) {
                        std::lock_guard<std::mutex> lock(mutex_);
                        snapshot_.read_failures += 1;
                        snapshot_.last_error = "av_frame_copy_props failed: " + ffmpeg_error_text(copy_props_result);
                        av_frame_unref(session.decoded_frame);
                        av_frame_unref(session.transfer_frame);
                        break;
                    }
                    source_frame = session.transfer_frame;
                }

                std::string convert_error;
                if (!convert_decoded_frame_to_input_format(
                        source_frame,
                        config_,
                        &session.sws_context,
                        &read_frame,
                        &convert_error)) {
                    std::lock_guard<std::mutex> lock(mutex_);
                    snapshot_.read_failures += 1;
                    snapshot_.last_error = convert_error.empty() ? "frame conversion failed" : convert_error;
                    av_frame_unref(session.decoded_frame);
                    av_frame_unref(session.transfer_frame);
                    break;
                }

                const auto read_finished_ns = now_ns();
                const double read_duration_ms =
                    static_cast<double>(std::max<std::int64_t>(0, read_finished_ns - read_started_ns)) / 1'000'000.0;
                read_durations_ms.push_back(read_duration_ms);
                read_durations_sum_ms += read_duration_ms;
                if (read_durations_ms.size() > kReaderMetricWindow) {
                    read_durations_sum_ms -= read_durations_ms.front();
                    read_durations_ms.pop_front();
                }

                const auto arrival_timestamp_ns = read_finished_ns;
                receive_times_ns.push_back(arrival_timestamp_ns);
                if (receive_times_ns.size() > kReaderMetricWindow) {
                    receive_times_ns.pop_front();
                }
                if (last_receive_ts_ns > 0) {
                    last_frame_interval_ms =
                        static_cast<double>(std::max<std::int64_t>(0, arrival_timestamp_ns - last_receive_ts_ns)) / 1'000'000.0;
                    max_frame_interval_ms = std::max(max_frame_interval_ms, last_frame_interval_ms);
                    if (last_frame_interval_ms >= kLateFrameIntervalMs) {
                        late_frame_intervals += 1;
                    }
                    frame_intervals_ms.push_back(last_frame_interval_ms);
                    frame_intervals_sum_ms += last_frame_interval_ms;
                    if (frame_intervals_ms.size() > kReaderMetricWindow) {
                        frame_intervals_sum_ms -= frame_intervals_ms.front();
                        frame_intervals_ms.pop_front();
                    }
                }
                last_receive_ts_ns = arrival_timestamp_ns;

                const auto frame_pts_raw =
                    (source_frame->best_effort_timestamp != AV_NOPTS_VALUE)
                        ? source_frame->best_effort_timestamp
                        : source_frame->pts;
                std::int64_t source_dts_raw = AV_NOPTS_VALUE;
                std::int64_t source_wallclock_ns = 0;
                resolve_frame_wallclock_hint(
                    packet_wallclock_hints,
                    frame_pts_raw,
                    source_frame->pts,
                    &source_dts_raw,
                    &source_wallclock_ns);
                const bool source_pts_valid = frame_pts_raw != AV_NOPTS_VALUE;
                const std::int64_t source_pts_ns = rescale_timestamp_to_ns(frame_pts_raw, session.video_stream->time_base);
                const std::int64_t source_dts_ns = rescale_timestamp_to_ns(source_dts_raw, session.video_stream->time_base);
                const bool source_time_valid = source_pts_valid || source_wallclock_ns > 0;
                const bool source_time_comparable = source_wallclock_ns > 0;
                const auto source_time_kind =
                    (source_wallclock_ns > 0)
                        ? SourceTimeKind::kWallclock
                        : (source_pts_valid ? SourceTimeKind::kStreamPts : SourceTimeKind::kNone);

                double frozen_duration_sec = 0.0;
                if (config_.enable_freeze_detection) {
                    freeze_probe_index += 1;
                    const bool sample_freeze_probe =
                        previous_probe_gray.empty() || (freeze_probe_index % kFreezeProbeEveryN) == 0;
                    if (sample_freeze_probe) {
                        cv::Mat current_probe_gray = make_freeze_probe_gray(
                            read_frame,
                            config_.input_pipe_format,
                            config_.width,
                            config_.height);
                        const bool identical_frame = is_effectively_identical_probe(previous_probe_gray, current_probe_gray);
                        last_motion_mean = frame_motion_score(previous_probe_gray, current_probe_gray);
                        last_luma_mean = probe_luma_mean(current_probe_gray);
                        previous_probe_gray = current_probe_gray;
                        if (!previous_probe_gray.empty()) {
                            if (identical_frame && last_motion_mean <= kFreezeMotionThreshold) {
                                if (freeze_started_ns <= 0) {
                                    freeze_started_ns = arrival_timestamp_ns;
                                }
                            } else {
                                freeze_started_ns = 0;
                            }
                        }
                    }
                    frozen_duration_sec =
                        (freeze_started_ns > 0)
                            ? static_cast<double>(arrival_timestamp_ns - freeze_started_ns) / 1'000'000'000.0
                            : 0.0;
                } else {
                    freeze_started_ns = 0;
                    freeze_probe_index = 0;
                    last_motion_mean = 0.0;
                    last_luma_mean = 0.0;
                    previous_probe_gray.release();
                }

                std::lock_guard<std::mutex> lock(mutex_);
                const auto max_buffered_frames =
                    static_cast<std::size_t>(std::max(1, config_.max_buffered_frames));
                if (frames_.size() != max_buffered_frames) {
                    frames_.clear();
                    frames_.resize(max_buffered_frames);
                    frame_start_index_ = 0;
                    frame_count_ = 0;
                }

                std::size_t insert_index = 0;
                if (frame_count_ < frames_.size()) {
                    insert_index = (frame_start_index_ + frame_count_) % frames_.size();
                    frame_count_ += 1;
                } else {
                    insert_index = frame_start_index_;
                    recycled_frame = std::move(frames_[insert_index].frame);
                    frame_start_index_ = (frame_start_index_ + 1) % frames_.size();
                    snapshot_.stale_drops += 1;
                }
                BufferedFrame& buffered = frames_[insert_index];
                buffered.frame = std::move(read_frame);
                buffered.seq = snapshot_.latest_seq + 1;
                buffered.timestamp_ns = arrival_timestamp_ns;
                buffered.arrival_timestamp_ns = arrival_timestamp_ns;
                buffered.source_pts_ns = source_pts_ns;
                buffered.source_dts_ns = source_dts_ns;
                buffered.source_wallclock_ns = source_wallclock_ns;
                buffered.source_time_valid = source_time_valid;
                buffered.source_time_comparable = source_time_comparable;
                buffered.source_time_kind = source_time_kind;
                buffered.motion_score = last_motion_mean;
                buffered.luma_mean = last_luma_mean;
                if (can_reuse_frame_storage(recycled_frame, config_.width, config_.height, config_.input_pipe_format)) {
                    read_frame = std::move(recycled_frame);
                } else {
                    read_frame = allocate_input_frame(config_.width, config_.height, config_.input_pipe_format);
                }

                const auto* latest = latest_buffered_frame_locked();
                const auto* oldest = oldest_buffered_frame_locked();
                snapshot_.has_frame = true;
                snapshot_.latest_seq = (latest != nullptr) ? latest->seq : 0;
                snapshot_.latest_timestamp_ns = (latest != nullptr) ? latest->arrival_timestamp_ns : 0;
                snapshot_.oldest_seq = (oldest != nullptr) ? oldest->seq : 0;
                snapshot_.oldest_timestamp_ns = (oldest != nullptr) ? oldest->arrival_timestamp_ns : 0;
                snapshot_.latest_arrival_timestamp_ns = snapshot_.latest_timestamp_ns;
                snapshot_.oldest_arrival_timestamp_ns = snapshot_.oldest_timestamp_ns;
                snapshot_.buffer_seq_span =
                    (latest != nullptr && oldest != nullptr && latest->seq >= oldest->seq)
                        ? (latest->seq - oldest->seq)
                        : 0;
                snapshot_.buffer_span_ms =
                    (latest != nullptr && oldest != nullptr && latest->arrival_timestamp_ns >= oldest->arrival_timestamp_ns)
                        ? static_cast<double>(latest->arrival_timestamp_ns - oldest->arrival_timestamp_ns) / 1'000'000.0
                        : 0.0;
                snapshot_.latest_source_pts_ns = (latest != nullptr) ? latest->source_pts_ns : 0;
                snapshot_.oldest_source_pts_ns = (oldest != nullptr) ? oldest->source_pts_ns : 0;
                snapshot_.latest_source_dts_ns = (latest != nullptr) ? latest->source_dts_ns : 0;
                snapshot_.oldest_source_dts_ns = (oldest != nullptr) ? oldest->source_dts_ns : 0;
                snapshot_.latest_source_wallclock_ns = (latest != nullptr) ? latest->source_wallclock_ns : 0;
                snapshot_.oldest_source_wallclock_ns = (oldest != nullptr) ? oldest->source_wallclock_ns : 0;
                snapshot_.latest_source_time_valid = (latest != nullptr) ? latest->source_time_valid : false;
                snapshot_.latest_source_time_comparable = (latest != nullptr) ? latest->source_time_comparable : false;
                snapshot_.latest_source_time_kind = (latest != nullptr) ? latest->source_time_kind : SourceTimeKind::kNone;
                snapshot_.source_valid_frames = 0;
                snapshot_.source_comparable_frames = 0;
                snapshot_.latest_comparable_source_timestamp_ns = 0;
                snapshot_.oldest_comparable_source_timestamp_ns = 0;
                bool first_comparable = true;
                for (std::size_t index = 0; index < buffered_frame_count_locked(); ++index) {
                    const auto* frame = buffered_frame_at_locked(index);
                    if (frame == nullptr) {
                        continue;
                    }
                    if (frame->source_time_valid) {
                        snapshot_.source_valid_frames += 1;
                    }
                    if (frame->source_time_comparable) {
                        snapshot_.source_comparable_frames += 1;
                        const auto comparable_time_ns = frame->source_wallclock_ns;
                        if (first_comparable) {
                            snapshot_.oldest_comparable_source_timestamp_ns = comparable_time_ns;
                            snapshot_.latest_comparable_source_timestamp_ns = comparable_time_ns;
                            first_comparable = false;
                        } else {
                            snapshot_.latest_comparable_source_timestamp_ns = comparable_time_ns;
                        }
                    }
                }
                if (!first_comparable &&
                    snapshot_.latest_comparable_source_timestamp_ns >= snapshot_.oldest_comparable_source_timestamp_ns) {
                    snapshot_.source_buffer_span_ms =
                        static_cast<double>(
                            snapshot_.latest_comparable_source_timestamp_ns -
                            snapshot_.oldest_comparable_source_timestamp_ns) / 1'000'000.0;
                } else {
                    snapshot_.source_buffer_span_ms = 0.0;
                }
                snapshot_.buffered_frames = static_cast<std::int64_t>(buffered_frame_count_locked());
                snapshot_.frames_total += 1;
                snapshot_.motion_mean = last_motion_mean;
                snapshot_.frozen_duration_sec = frozen_duration_sec;
                snapshot_.content_frozen = config_.enable_freeze_detection && frozen_duration_sec >= kFreezeRestartSec;
                snapshot_.last_frame_interval_ms = last_frame_interval_ms;
                snapshot_.max_frame_interval_ms = max_frame_interval_ms;
                snapshot_.late_frame_intervals = late_frame_intervals;
                snapshot_.avg_frame_interval_ms =
                    frame_intervals_ms.empty() ? 0.0 : (frame_intervals_sum_ms / static_cast<double>(frame_intervals_ms.size()));
                snapshot_.avg_read_ms =
                    read_durations_ms.empty() ? 0.0 : (read_durations_sum_ms / static_cast<double>(read_durations_ms.size()));
                snapshot_.max_read_ms = 0.0;
                for (const double value : read_durations_ms) {
                    snapshot_.max_read_ms = std::max(snapshot_.max_read_ms, value);
                }
                if (receive_times_ns.size() >= 2) {
                    snapshot_.fps = fps_from_count(
                        receive_times_ns.size(),
                        receive_times_ns.back() - receive_times_ns.front());
                }

                av_frame_unref(session.decoded_frame);
                av_frame_unref(session.transfer_frame);
            }

            if (!running_.load()) {
                break;
            }
        }

        if (!running_.load()) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(
            static_cast<int>(std::max(0.2, config_.reconnect_cooldown_sec) * 1000.0)));
    }
}

std::int64_t FfmpegRtspReader::now_ns() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        clock::now().time_since_epoch()).count();
}

std::size_t FfmpegRtspReader::buffered_frame_count_locked() const noexcept {
    return frame_count_;
}

const FfmpegRtspReader::BufferedFrame* FfmpegRtspReader::buffered_frame_at_locked(std::size_t logical_index) const noexcept {
    if (logical_index >= frame_count_ || frames_.empty()) {
        return nullptr;
    }
    const std::size_t physical_index = (frame_start_index_ + logical_index) % frames_.size();
    return &frames_[physical_index];
}

const FfmpegRtspReader::BufferedFrame* FfmpegRtspReader::oldest_buffered_frame_locked() const noexcept {
    return buffered_frame_at_locked(0);
}

const FfmpegRtspReader::BufferedFrame* FfmpegRtspReader::latest_buffered_frame_locked() const noexcept {
    if (frame_count_ == 0) {
        return nullptr;
    }
    return buffered_frame_at_locked(frame_count_ - 1);
}

bool FfmpegRtspReader::copy_buffered_frame(
    const BufferedFrame& buffered,
    cv::Mat* frame_out,
    std::int64_t* seq_out,
    std::int64_t* ts_out,
    BufferedFrameInfo* info_out) const {
    *frame_out = buffered.frame;
    if (seq_out != nullptr) {
        *seq_out = buffered.seq;
    }
    if (ts_out != nullptr) {
        *ts_out = buffered.arrival_timestamp_ns;
    }
    if (info_out != nullptr) {
        info_out->frame = buffered.frame;
        info_out->seq = buffered.seq;
        info_out->timestamp_ns = buffered.arrival_timestamp_ns;
        info_out->arrival_timestamp_ns = buffered.arrival_timestamp_ns;
        info_out->source_pts_ns = buffered.source_pts_ns;
        info_out->source_dts_ns = buffered.source_dts_ns;
        info_out->source_wallclock_ns = buffered.source_wallclock_ns;
        info_out->source_time_valid = buffered.source_time_valid;
        info_out->source_time_comparable = buffered.source_time_comparable;
        info_out->source_time_kind = buffered.source_time_kind;
        info_out->motion_score = buffered.motion_score;
        info_out->luma_mean = buffered.luma_mean;
    }
    return true;
}

}  // namespace hogak::input
