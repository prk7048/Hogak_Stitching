#pragma once

#ifdef _WIN32

#include <cstdint>
#include <cstdio>
#include <string>

namespace hogak::platform {

class WinProcessPipe {
public:
    WinProcessPipe() = default;
    ~WinProcessPipe();

    WinProcessPipe(const WinProcessPipe&) = delete;
    WinProcessPipe& operator=(const WinProcessPipe&) = delete;

    bool start(const std::string& command_line, std::string& error_message);
    bool read_exact(std::uint8_t* destination, std::size_t bytes_to_read, std::size_t& bytes_read);
    void stop();
    bool running() const noexcept;

private:
    void* process_handle_ = nullptr;
    void* stdout_read_handle_ = nullptr;
};

}  // namespace hogak::platform

#endif
