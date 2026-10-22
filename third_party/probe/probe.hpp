#ifndef PROBE_PROBE_HPP
#define PROBE_PROBE_HPP

#include <cerrno>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iomanip>
#include <limits>
#include <locale>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

namespace probe {
namespace detail {

inline long long parse_integer(const char* text, long long fallback) noexcept {
    if (text == nullptr) {
        return fallback;
    }
    char* end = nullptr;
    errno = 0;
    const long long value = std::strtoll(text, &end, 10);
    if (end == text || errno == ERANGE) {
        return fallback;
    }
    while (std::isspace(static_cast<unsigned char>(*end))) {
        ++end;
    }
    return *end == '\0' ? value : fallback;
}

struct State {
    using Clock = std::chrono::steady_clock;

    int socket_fd = -1;
    sockaddr_in destination{};
    long long max_hz = 100;
    std::mutex mutex;
    std::unordered_map<std::string, std::deque<Clock::time_point>> recent;

    State() {
        max_hz = parse_integer(std::getenv("PROBE_MAX_HZ"), 100);
        if (max_hz <= 0) {
            return;
        }

        const char* host = std::getenv("PROBE_HOST");
        const char* port_text = std::getenv("PROBE_PORT");
        const long long port = parse_integer(port_text ? port_text : "8766", -1);
        if (port < 0 || port > 65535) {
            return;
        }

        addrinfo hints{};
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_DGRAM;
        addrinfo* resolved = nullptr;
        if (::getaddrinfo(host ? host : "127.0.0.1", nullptr, &hints, &resolved) != 0) {
            return;
        }
        if (resolved == nullptr) {
            return;
        }
        if (resolved->ai_addr == nullptr || resolved->ai_addrlen < sizeof(destination)) {
            ::freeaddrinfo(resolved);
            return;
        }
        std::memcpy(&destination, resolved->ai_addr, sizeof(destination));
        destination.sin_port = htons(static_cast<unsigned short>(port));
        ::freeaddrinfo(resolved);

        // 初始化仅执行一次，失败后不重试创建 socket。
        socket_fd = ::socket(AF_INET, SOCK_DGRAM, 0);
        if (socket_fd < 0) {
            return;
        }
        const int flags = ::fcntl(socket_fd, F_GETFL, 0);
        if (flags < 0 || ::fcntl(socket_fd, F_SETFL, flags | O_NONBLOCK) < 0) {
            ::close(socket_fd);
            socket_fd = -1;
        }
    }

    ~State() noexcept {
        if (socket_fd >= 0) {
            ::close(socket_fd);
        }
    }

    bool allow(const std::string& name) {
        std::lock_guard<std::mutex> guard(mutex);
        const auto now = Clock::now();
        auto& times = recent[name];
        while (!times.empty() && now - times.front() >= std::chrono::seconds(1)) {
            times.pop_front();
        }
        if (times.size() >= static_cast<unsigned long long>(max_hz)) {
            return false;
        }
        times.push_back(now);
        return true;
    }
};

inline State& state() {
    // inline 函数的局部静态对象在所有翻译单元和所有重载之间共享。
    static State instance;
    return instance;
}

inline void write_string(std::ostream& out, const std::string& value) {
    constexpr char hex[] = "0123456789abcdef";
    out.put('"');
    for (unsigned char c : value) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20) {
                out << "\\u00" << hex[c >> 4] << hex[c & 0x0f];
            } else {
                out.put(static_cast<char>(c));
            }
        }
    }
    out.put('"');
}

inline bool write_value(std::ostream& out, double value) {
    if (!std::isfinite(value)) {
        return false;
    }
    out << value;
    return true;
}

inline bool write_value(std::ostream& out, const std::vector<double>& value) {
    out.put('[');
    bool first = true;
    for (double item : value) {
        if (!first) {
            out.put(',');
        }
        first = false;
        if (!write_value(out, item)) {
            return false;
        }
    }
    out.put(']');
    return true;
}

inline bool write_value(std::ostream& out, const std::string& value) {
    write_string(out, value);
    return true;
}

template <typename Value>
inline void send_value(const std::string& name, const Value& value) noexcept {
    const char* off = std::getenv("PROBE_OFF");
    if (off != nullptr && off[0] == '1' && off[1] == '\0') {
        return;
    }

    try {
        auto& shared = state();
        if (shared.socket_fd < 0 || shared.max_hz <= 0 || !shared.allow(name)) {
            return;
        }

        std::ostringstream json;
        json.imbue(std::locale::classic());
        json << std::setprecision(std::numeric_limits<double>::max_digits10);
        json << "{\"name\":";
        write_string(json, name);
        const double timestamp = std::chrono::duration<double>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        json << ",\"t\":" << timestamp << ",\"value\":";
        if (!write_value(json, value)) {
            return;
        }
        json.put('}');
        if (!json) {
            return;
        }

        const std::string data = json.str();
        ::sendto(shared.socket_fd, data.data(), data.size(), 0,
                 reinterpret_cast<const sockaddr*>(&shared.destination),
                 sizeof(shared.destination));
    } catch (...) {
        // 打点不影响调用方：分配、序列化和发送失败均静默忽略。
    }
}

} // namespace detail

inline void send(const std::string& name, double value) noexcept {
    detail::send_value(name, value);
}

inline void send(const std::string& name, const std::vector<double>& value) noexcept {
    detail::send_value(name, value);
}

inline void send(const std::string& name, const std::string& value) noexcept {
    detail::send_value(name, value);
}

} // namespace probe

#endif // PROBE_PROBE_HPP
