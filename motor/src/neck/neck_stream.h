#pragma once

#include "neck_kinematics.h"
#include <cstdint>
#include <map>

namespace neck {
struct Motion { Vec p{}, v{}, a{}; };
struct Polynomial {
    std::array<Vec, 6> c{};
    double start = 0, duration = 1;
    Polynomial() = default;
    Polynomial(const Motion& from, const Motion& to, double time, double length);
    Motion sample(double time) const;
};
struct StreamTarget {
    Vec rpy, q, motor_velocity;
    double buffer_ms;
    int state;
    std::uint64_t dropped_samples;
    double last_rx_mono_s;
};

class Stream {
public:
    Stream(const Model& model, const Vec& initial_q, double now);
    void play(std::map<double, Vec> fresh, double now);
    void hold(double now);
    void listen();
    // 样本时间为 CLOCK_MONOTONIC 秒；由 Controller 的控制线程统一写入。
    void pushSamples(std::map<double, Vec> fresh, double now, std::uint64_t dropped = 0);
    StreamTarget step(double now, double dt, double vmax, double amax);
private:
    enum { WAIT, FOLLOW, DECEL, HOLD, TRANSITION };
    const Model& model;
    bool udp = false;
    Motion current, braking, transition_from;
    Vec q;
    double brake_start = 0, pending = -1;
    double transition_start = 0, transition_duration = .250;
    int state = WAIT;
    std::map<double, Vec> points;
    bool changed = false;
    double first_new = 0, last_rx_mono_s = 0;
    std::uint64_t dropped_samples = 0;
    Vec tangent(std::map<double, Vec>::const_iterator point) const;
    Motion sample(double time) const;
    bool usable(const Vec& target) const;
    Vec project(Vec target, const Vec& origin) const;
    void limit(const Vec& target, double dt, double vmax, double amax);
};
} // namespace neck
