#pragma once

#ifdef _WIN32

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>

namespace hogak::platform {

class WinProcessSink {
public:
    WinProcessSink() = default;
    ~WinProcessSink();

    WinProcessSink(const WinProcessSink&) = delete;
    WinProcessSink& operator=(const WinProcessSink&) = delete;

    bool start(const std::string& command_line, std::string& error_message);
    bool write_all(const std::uint8_t* source, std::size_t bytes_to_write, std::string& error_message);
    void stop();
    bool running() const noexcept;
    std::uint32_t exit_code() const noexcept;
    std::string stderr_tail() const;
    void wait_for_exit(std::uint32_t timeout_ms) noexcept;

private:
    void stderr_pump();

    void* process_handle_ = nullptr;
    void* stdin_write_handle_ = nullptr;
    void* stderr_read_handle_ = nullptr;
    std::thread stderr_thread_{};
    mutable std::atomic<std::uint32_t> exit_code_{0};
    mutable std::mutex stderr_mutex_{};
    std::string stderr_tail_{};
};

}  // namespace hogak::platform

#endif
