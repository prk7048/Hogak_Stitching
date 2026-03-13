#include "platform/win_process_pipe.h"

#ifdef _WIN32

#include <windows.h>

#include <algorithm>
#include <cstring>
#include <vector>

namespace hogak::platform {

namespace {

HANDLE as_handle(void* value) noexcept {
    return static_cast<HANDLE>(value);
}

// Keep at least one full 1080p BGR24 frame in the pipe so reader-side frame reads
// are less likely to be split across many small ReadFile calls.
constexpr DWORD kPipeBufferBytes = 8u << 20;
constexpr std::size_t kPipeReadChunkBytes = 4u << 20;

}  // namespace

WinProcessPipe::~WinProcessPipe() {
    stop();
}

bool WinProcessPipe::start(const std::string& command_line, std::string& error_message) {
    stop();
    read_buffer_.assign(kPipeReadChunkBytes, 0);
    read_buffer_begin_ = 0;
    read_buffer_end_ = 0;

    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.bInheritHandle = TRUE;
    security.lpSecurityDescriptor = nullptr;

    HANDLE stdout_read = nullptr;
    HANDLE stdout_write = nullptr;
    if (!CreatePipe(&stdout_read, &stdout_write, &security, kPipeBufferBytes)) {
        error_message = "CreatePipe failed";
        return false;
    }
    if (!SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0)) {
        CloseHandle(stdout_read);
        CloseHandle(stdout_write);
        error_message = "SetHandleInformation failed";
        return false;
    }

    HANDLE stdin_handle = CreateFileA(
        "NUL",
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        &security,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (stdin_handle == INVALID_HANDLE_VALUE) {
        CloseHandle(stdout_read);
        CloseHandle(stdout_write);
        error_message = "failed to open NUL for stdin";
        return false;
    }

    HANDLE stderr_handle = CreateFileA(
        "NUL",
        GENERIC_WRITE,
        FILE_SHARE_WRITE,
        &security,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (stderr_handle == INVALID_HANDLE_VALUE) {
        CloseHandle(stdout_read);
        CloseHandle(stdout_write);
        error_message = "failed to open NUL for stderr";
        return false;
    }

    STARTUPINFOA startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = stdin_handle;
    startup.hStdOutput = stdout_write;
    startup.hStdError = stderr_handle;

    PROCESS_INFORMATION process{};
    std::vector<char> mutable_command(command_line.begin(), command_line.end());
    mutable_command.push_back('\0');

    const BOOL ok = CreateProcessA(
        nullptr,
        mutable_command.data(),
        nullptr,
        nullptr,
        TRUE,
        CREATE_NO_WINDOW,
        nullptr,
        nullptr,
        &startup,
        &process);

    CloseHandle(stdout_write);
    CloseHandle(stdin_handle);
    CloseHandle(stderr_handle);

    if (!ok) {
        CloseHandle(stdout_read);
        error_message = "CreateProcess failed";
        return false;
    }

    CloseHandle(process.hThread);
    process_handle_ = process.hProcess;
    stdout_read_handle_ = stdout_read;
    return true;
}

bool WinProcessPipe::read_exact(std::uint8_t* destination, std::size_t bytes_to_read, std::size_t& bytes_read) {
    bytes_read = 0;
    if (stdout_read_handle_ == nullptr || destination == nullptr || bytes_to_read == 0) {
        return false;
    }

    while (bytes_read < bytes_to_read) {
        if (read_buffer_begin_ >= read_buffer_end_) {
            if (read_buffer_.empty()) {
                read_buffer_.assign(kPipeReadChunkBytes, 0);
            }
            DWORD chunk = 0;
            if (!ReadFile(
                    as_handle(stdout_read_handle_),
                    read_buffer_.data(),
                    static_cast<DWORD>(read_buffer_.size()),
                    &chunk,
                    nullptr)) {
                return false;
            }
            if (chunk == 0) {
                return false;
            }
            read_buffer_begin_ = 0;
            read_buffer_end_ = static_cast<std::size_t>(chunk);
        }
        const std::size_t available = read_buffer_end_ - read_buffer_begin_;
        const std::size_t remaining = bytes_to_read - bytes_read;
        const std::size_t copy_bytes = (available < remaining) ? available : remaining;
        std::memcpy(destination + bytes_read, read_buffer_.data() + read_buffer_begin_, copy_bytes);
        read_buffer_begin_ += copy_bytes;
        bytes_read += copy_bytes;
    }
    return true;
}

void WinProcessPipe::stop() {
    if (stdout_read_handle_ != nullptr) {
        CloseHandle(as_handle(stdout_read_handle_));
        stdout_read_handle_ = nullptr;
    }
    if (process_handle_ != nullptr) {
        TerminateProcess(as_handle(process_handle_), 0);
        WaitForSingleObject(as_handle(process_handle_), 1000);
        CloseHandle(as_handle(process_handle_));
        process_handle_ = nullptr;
    }
    read_buffer_begin_ = 0;
    read_buffer_end_ = 0;
}

bool WinProcessPipe::running() const noexcept {
    if (process_handle_ == nullptr) {
        return false;
    }
    DWORD code = STILL_ACTIVE;
    if (!GetExitCodeProcess(as_handle(process_handle_), &code)) {
        return false;
    }
    return code == STILL_ACTIVE;
}

}  // namespace hogak::platform

#endif
