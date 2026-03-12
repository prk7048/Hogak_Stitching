#include "platform/win_process_sink.h"

#ifdef _WIN32

#include <windows.h>

#include <algorithm>
#include <array>
#include <sstream>
#include <vector>

namespace hogak::platform {

namespace {

HANDLE as_handle(void* value) noexcept {
    return static_cast<HANDLE>(value);
}

}  // namespace

WinProcessSink::~WinProcessSink() {
    stop();
}

bool WinProcessSink::start(const std::string& command_line, std::string& error_message) {
    stop();

    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.bInheritHandle = TRUE;
    security.lpSecurityDescriptor = nullptr;

    HANDLE stdin_read = nullptr;
    HANDLE stdin_write = nullptr;
    if (!CreatePipe(&stdin_read, &stdin_write, &security, 0)) {
        error_message = "CreatePipe failed";
        return false;
    }
    if (!SetHandleInformation(stdin_write, HANDLE_FLAG_INHERIT, 0)) {
        CloseHandle(stdin_read);
        CloseHandle(stdin_write);
        error_message = "SetHandleInformation failed";
        return false;
    }

    HANDLE stdout_handle = CreateFileA(
        "NUL",
        GENERIC_WRITE,
        FILE_SHARE_WRITE,
        &security,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (stdout_handle == INVALID_HANDLE_VALUE) {
        CloseHandle(stdin_read);
        CloseHandle(stdin_write);
        error_message = "failed to open NUL for stdout";
        return false;
    }

    HANDLE stderr_read = nullptr;
    HANDLE stderr_write = nullptr;
    if (!CreatePipe(&stderr_read, &stderr_write, &security, 0)) {
        CloseHandle(stdin_read);
        CloseHandle(stdin_write);
        CloseHandle(stdout_handle);
        error_message = "failed to create stderr pipe";
        return false;
    }
    if (!SetHandleInformation(stderr_read, HANDLE_FLAG_INHERIT, 0)) {
        CloseHandle(stdin_read);
        CloseHandle(stdin_write);
        CloseHandle(stdout_handle);
        CloseHandle(stderr_read);
        CloseHandle(stderr_write);
        error_message = "failed to configure stderr pipe";
        return false;
    }

    STARTUPINFOA startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = stdin_read;
    startup.hStdOutput = stdout_handle;
    startup.hStdError = stderr_write;

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

    CloseHandle(stdin_read);
    CloseHandle(stdout_handle);
    CloseHandle(stderr_write);

    if (!ok) {
        CloseHandle(stdin_write);
        CloseHandle(stderr_read);
        error_message = "CreateProcess failed";
        return false;
    }

    CloseHandle(process.hThread);
    process_handle_ = process.hProcess;
    stdin_write_handle_ = stdin_write;
    stderr_read_handle_ = stderr_read;
    exit_code_.store(STILL_ACTIVE);
    {
        std::lock_guard<std::mutex> lock(stderr_mutex_);
        stderr_tail_.clear();
    }
    stderr_thread_ = std::thread(&WinProcessSink::stderr_pump, this);
    return true;
}

bool WinProcessSink::write_all(const std::uint8_t* source, std::size_t bytes_to_write, std::string& error_message) {
    if (stdin_write_handle_ == nullptr || source == nullptr) {
        error_message = "stdin handle unavailable";
        return false;
    }

    std::size_t bytes_written = 0;
    while (bytes_written < bytes_to_write) {
        DWORD chunk = 0;
        const auto remaining = static_cast<DWORD>(bytes_to_write - bytes_written);
        if (!WriteFile(as_handle(stdin_write_handle_), source + bytes_written, remaining, &chunk, nullptr)) {
            const DWORD win32_error = GetLastError();
            wait_for_exit(250);
            std::ostringstream message;
            message << "WriteFile failed (win32=" << win32_error << ")";
            if (!running()) {
                const auto code = exit_code();
                if (code != 0 && code != STILL_ACTIVE) {
                    message << " (child_exit=" << code << ")";
                }
            }
            const auto stderr_text = stderr_tail();
            if (!stderr_text.empty()) {
                message << " stderr=" << stderr_text;
            }
            error_message = message.str();
            return false;
        }
        if (chunk == 0) {
            error_message = "short write";
            return false;
        }
        bytes_written += static_cast<std::size_t>(chunk);
    }
    return true;
}

void WinProcessSink::stop() {
    if (stdin_write_handle_ != nullptr) {
        CloseHandle(as_handle(stdin_write_handle_));
        stdin_write_handle_ = nullptr;
    }
    if (process_handle_ != nullptr) {
        DWORD code = STILL_ACTIVE;
        if (GetExitCodeProcess(as_handle(process_handle_), &code)) {
            exit_code_.store(code);
        }
    }
    if (stderr_read_handle_ != nullptr) {
        CloseHandle(as_handle(stderr_read_handle_));
        stderr_read_handle_ = nullptr;
    }
    if (stderr_thread_.joinable()) {
        stderr_thread_.join();
    }
    if (process_handle_ != nullptr) {
        DWORD code = STILL_ACTIVE;
        if (!GetExitCodeProcess(as_handle(process_handle_), &code) || code == STILL_ACTIVE) {
            TerminateProcess(as_handle(process_handle_), 0);
            WaitForSingleObject(as_handle(process_handle_), 1000);
            code = 0;
        }
        exit_code_.store(code);
        CloseHandle(as_handle(process_handle_));
        process_handle_ = nullptr;
    }
}

bool WinProcessSink::running() const noexcept {
    if (process_handle_ == nullptr) {
        return false;
    }
    DWORD code = STILL_ACTIVE;
    if (!GetExitCodeProcess(as_handle(process_handle_), &code)) {
        return false;
    }
    exit_code_.store(code);
    return code == STILL_ACTIVE;
}

std::uint32_t WinProcessSink::exit_code() const noexcept {
    return exit_code_.load();
}

std::string WinProcessSink::stderr_tail() const {
    std::lock_guard<std::mutex> lock(stderr_mutex_);
    return stderr_tail_;
}

void WinProcessSink::wait_for_exit(std::uint32_t timeout_ms) noexcept {
    if (process_handle_ == nullptr) {
        return;
    }
    WaitForSingleObject(as_handle(process_handle_), timeout_ms);
    DWORD code = STILL_ACTIVE;
    if (GetExitCodeProcess(as_handle(process_handle_), &code)) {
        exit_code_.store(code);
    }
}

void WinProcessSink::stderr_pump() {
    auto* handle = as_handle(stderr_read_handle_);
    if (handle == nullptr) {
        return;
    }

    std::array<char, 512> buffer{};
    while (true) {
        DWORD read_bytes = 0;
        if (!ReadFile(handle, buffer.data(), static_cast<DWORD>(buffer.size()), &read_bytes, nullptr) || read_bytes == 0) {
            break;
        }
        std::lock_guard<std::mutex> lock(stderr_mutex_);
        stderr_tail_.append(buffer.data(), buffer.data() + read_bytes);
        constexpr std::size_t kMaxTail = 4096;
        if (stderr_tail_.size() > kMaxTail) {
            stderr_tail_.erase(0, stderr_tail_.size() - kMaxTail);
        }
    }
}

}  // namespace hogak::platform

#endif
