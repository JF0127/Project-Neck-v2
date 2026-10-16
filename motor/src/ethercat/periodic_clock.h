#pragma once

#include <cerrno>
#include <cstdint>
#include <time.h>

struct PeriodicClock {
    static int64_t nowNs() {
        timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        return int64_t(t.tv_sec) * 1000000000 + t.tv_nsec;
    }
    const int64_t period, start;
    int64_t due;
    explicit PeriodicClock(double rate)
        : period(int64_t(1e9 / rate + 0.5)), start(nowNs()), due(start) {}
    double wait() const {
        const timespec t{time_t(due / 1000000000), long(due % 1000000000)};
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, nullptr) == EINTR) {}
        return (nowNs() - start) * 1e-9;
    }
    void next() {
        // 固定起点的绝对周期；错过的时隙跳过，不突发追赶，也不累计漂移。
        due = start + ((nowNs() - start) / period + 1) * period;
    }
};
