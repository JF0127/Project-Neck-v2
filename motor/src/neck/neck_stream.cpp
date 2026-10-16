#include "neck_stream.h"

namespace neck {
namespace {
double norm(const Vec& v) { return std::sqrt(dot(v, v)); }
double transitionTime(const Motion& from, const Motion& to, double amax) {
    if (!amax) return .250;
    // max|w''|=10/sqrt(3)/T²，max|w'|=1.875/T；计入速度差引起的位置差增长。
    const double d = (10 / std::sqrt(3.0)) * norm(to.p - from.p);
    const double v = (10 / std::sqrt(3.0) + 3.75) * norm(to.v - from.v);
    return std::clamp((v + std::sqrt(v*v + 4 * amax * d)) / (2 * amax), .250, .600);
}
Motion brake(const Motion& from, double elapsed) {
    const double u = std::clamp(elapsed / .150, 0.0, 1.0);
    return {from.p + from.v * (.150 * (u - u*u*u + u*u*u*u / 2)),
            from.v * (1 - 3*u*u + 2*u*u*u), from.v * ((-6*u + 6*u*u) / .150)};
}
}

Polynomial::Polynomial(const Motion& from, const Motion& to, double time, double length)
    : start(time), duration(length) {
    c[0] = from.p;
    c[1] = from.v * length;
    c[2] = from.a * (length * length / 2);
    const Vec d = to.p - c[0] - c[1] - c[2];
    const Vec e = to.v * length - c[1] - c[2] * 2;
    const Vec f = to.a * (length * length) - c[2] * 2;
    c[3] = d * 10 - e * 4 + f / 2;
    c[4] = d * -15 + e * 7 - f;
    c[5] = d * 6 - e * 3 + f / 2;
}
Motion Polynomial::sample(double time) const {
    const double u = std::clamp((time - start) / duration, 0.0, 1.0);
    Motion out;
    for (int i = 5; i >= 0; --i) out.p = out.p * u + c[i];
    for (int i = 5; i >= 1; --i) out.v = out.v * u + c[i] * i;
    for (int i = 5; i >= 2; --i) out.a = out.a * u + c[i] * (i * (i - 1));
    out.v = out.v / duration;
    out.a = out.a / (duration * duration);
    return out;
}

Stream::Stream(const Model& model, const Vec& initial_q, double now)
    : model(model), q(initial_q) {
    current.p = model.forward(q);
    points[now] = current.p;
}
void Stream::play(std::map<double, Vec> fresh, double now) {
    points = std::move(fresh);
    udp = false;
    state = FOLLOW;
    changed = false;
    pending = -1;
    last_rx_mono_s = now;
}
void Stream::hold(double now) {
    play({{now, current.p}}, now);
    current.v = current.a = {};
    state = HOLD;
}
void Stream::listen() {
    points.clear();
    udp = true;
    state = WAIT;
    changed = false;
    pending = -1;
    current.v = current.a = {};
}

void Stream::pushSamples(std::map<double, Vec> fresh, double now, std::uint64_t dropped) {
    last_rx_mono_s = now;
    dropped_samples += dropped;
    if (fresh.empty()) return; // 全部过期的报文仍记录接收时刻和丢弃数。
    if (!udp) {
        points.clear();
        udp = true;
        state = HOLD;
        pending = -1;
    }
    const double first = fresh.begin()->first;
    first_new = changed ? std::min(first_new, first) : first;
    changed = true;
    points.erase(points.lower_bound(first), points.end());
    points.merge(fresh); // 新包起点之后的未执行旧尾部被替换。
    auto keep = points.upper_bound(now);
    for (int i = 0; i < 2 && keep != points.begin(); ++i) --keep;
    points.erase(points.begin(), keep);
}

Vec Stream::tangent(std::map<double, Vec>::const_iterator point) const {
    if (points.size() == 1) return {};
    auto left = point == points.begin() ? point : std::prev(point);
    auto right = std::next(point) == points.end() ? point : std::next(point);
    if (point == left || point == right)
        return udp ? (right->second - left->second) / (right->first - left->first) : Vec{};
    const double a = point->first - left->first, b = right->first - point->first;
    return ((point->second - left->second) * (b / a) +
            (right->second - point->second) * (a / b)) / (a + b);
}
Motion Stream::sample(double time) const {
    if (time < points.begin()->first) return {points.begin()->second};
    const auto last = std::prev(points.end());
    if (time >= last->first)
        return udp ? brake({last->second, tangent(last)}, time - last->first) : Motion{last->second};
    const auto right = points.upper_bound(time), left = std::prev(right);
    const double h = right->first - left->first, u = (time - left->first) / h;
    const Vec v0 = tangent(left), v1 = tangent(right);
    const Vec a = (left->second - right->second) * 2 + (v0 + v1) * h;
    const Vec b = (right->second - left->second) * 3 - (v0 * 2 + v1) * h;
    const Vec c = v0 * h;
    return {((a * u + b) * u + c) * u + left->second,
            ((a * (3 * u) + b * 2) * u + c) / h, (a * (6 * u) + b * 2) / (h * h)};
}
bool Stream::usable(const Vec& target) const {
    const auto from = model.inverse(current.p), to = model.inverse(target);
    // 起点越界时允许向内恢复；回到可用范围后恢复原约束。
    if (!to.reachable || to.margin < std::min(model.min_margin, from.margin)) return false;
    for (int i = 0; i < 3; ++i)
        if (to.q[i] < std::min(model.lower[i], from.q[i]) ||
            to.q[i] > std::max(model.upper[i], from.q[i])) return false;
    return true;
}
Vec Stream::project(Vec target, const Vec& origin) const {
    for (int i = 0; i < 3; ++i) target[i] = std::clamp(target[i], i == 1 ? -90.0 : -180.0, i == 1 ? 90.0 : 180.0);
    if (usable(target)) return target;
    double low = 0, high = 1;
    for (int i = 0; i < 18; ++i) {
        const double mid = (low + high) / 2;
        if (usable(origin + (target - origin) * mid)) low = mid;
        else high = mid;
    }
    return origin + (target - origin) * low;
}
void Stream::limit(const Vec& target, double dt, double vmax, double amax) {
    vmax = vmax ? vmax : INFINITY;
    amax = amax ? amax : INFINITY;
    const Vec projected = project(target, current.p);
    Vec limited;
    for (int i = 0; i < 3; ++i)
        limited[i] = std::clamp(std::clamp((projected[i] - current.p[i]) / dt, -vmax, vmax),
                                current.v[i] - amax * dt, current.v[i] + amax * dt);
    auto stop_time = [&](const Vec& v) { return maxAbs(v) / amax; };
    const Vec braking = maxAbs(current.v) ? current.v * std::max(0.0, 1 - dt / stop_time(current.v)) : Vec{};
    auto fits = [&](const Vec& v) {
        const Vec p = current.p + v * dt;
        return usable(p) && usable(p + v * (stop_time(v) / 2 + dt));
    };
    if (!fits(limited)) {
        double low = 0, high = 1;
        for (int i = 0; i < 18; ++i) {
            const double mid = (low + high) / 2;
            if (fits(braking + (limited - braking) * mid)) low = mid;
            else high = mid;
        }
        limited = braking + (limited - braking) * low;
    }
    current.p = current.p + limited * dt;
    current.a = (limited - current.v) / dt;
    current.v = limited;
}

StreamTarget Stream::step(double now, double dt, double vmax, double amax) {
    if (state == WAIT && !udp)
        return {current.p, q, {}, 0, state, dropped_samples, last_rx_mono_s};
    Motion target{current.p};
    double remaining, last_rx;
    std::uint64_t dropped;
    const Motion from{current.p + current.v * dt, current.v, current.a};
    {
        const double tail = points.empty() ? 0 : points.rbegin()->first;
        if (changed && (state == WAIT || state == DECEL || state == HOLD) && pending < 0)
            pending = std::max(now, first_new - .250);
        remaining = std::max(0.0, tail - now) * 1000;
        dropped = dropped_samples;
        last_rx = last_rx_mono_s;
        if (!udp) {
            if (state != WAIT) state = now < tail ? FOLLOW : HOLD;
            target = sample(now);
        } else {
            if ((state == FOLLOW || state == TRANSITION) && now >= tail) {
                state = DECEL;
                // 从上一控制采样衔接，首帧也推进 dt，避免将进入加速度清零。
                brake_start = now - dt;
                braking = current;
            }
            if (pending >= 0 && now >= pending && tail > now) {
                state = TRANSITION;
                transition_start = now;
                transition_from = from;
                transition_duration = transitionTime(from, sample(now), amax);
                pending = -1;
            } else if (state == TRANSITION && now >= transition_start + transition_duration) {
                state = FOLLOW;
            }
            if (state == TRANSITION) {
                const double elapsed = now - transition_start;
                const double u = std::clamp(elapsed / transition_duration, 0.0, 1.0);
                const double w = u*u*u * (10 - 15*u + 6*u*u);
                target.p = brake(transition_from, elapsed).p * (1 - w) + sample(now).p * w;
            } else if (state == FOLLOW) target = sample(now);
            else if (state == DECEL || state == HOLD) {
                const double duration = std::clamp(amax ? norm(braking.v) / amax : 0.0, .060, .150);
                const double u = std::clamp((now - brake_start) / duration, 0.0, 1.0);
                // 三次 Hermite 速度：v(0)=v0、a(0)=a0，v(T)=a(T)=0。
                const Vec velocity = (braking.v * (1 + 2*u) + braking.a * (duration * u)) * ((1-u)*(1-u));
                target.p = current.p + velocity * dt;
                if (now - brake_start >= duration) state = HOLD;
            }
        }
        changed = false;
    }
    limit(target.p, dt, vmax, amax);
    const Vec new_q = model.inverse(current.p).q;
    const Vec motor_velocity = (new_q - q) / dt;
    q = new_q;
    return {current.p, q, motor_velocity, remaining, state, dropped, last_rx};
}
} // namespace neck
