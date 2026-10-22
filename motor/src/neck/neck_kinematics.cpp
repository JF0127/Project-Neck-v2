#include "neck_kinematics.h"
#include "probe.hpp"

#include <boost/property_tree/json_parser.hpp>
#include <limits>

namespace neck {
namespace {
constexpr double R = 18, L = 94.003, X = 25.600;
Vec base(int i) { return {X, -75.131, i ? -27.0 : 27.0}; }
Vec upper(int i, double a, double b) {
    const double x = 26.349 * std::cos(b) - 27.364 * std::sin(b);
    const double y = 26.349 * std::sin(b) + 27.364 * std::cos(b);
    const double z = i ? -22 : 22;
    return {x, std::cos(a)*y - std::sin(a)*z, std::sin(a)*y + std::cos(a)*z};
}
Vec crank(double q) { return {0, R * std::cos(q), R * std::sin(q)}; }
Vec constraint(const Vec& q, double a, double b) {
    Vec f{};
    for (int i = 0; i < 2; ++i) {
        const Vec d = upper(i, a, b) - base(i) - crank(q[i]);
        f[i] = dot(d, d) - L*L;
    }
    return f;
}
}

Model::Model(const std::string& path) {
    boost::property_tree::ptree config;
    boost::property_tree::read_json(path, config);
    const double margin = config.get<double>("soft_limits.margin_deg");
    min_margin = config.get<double>("model.min_margin_deg");
    for (const auto& entry : config.get_child("motors")) {
        const auto& m = entry.second;
        const int i = m.get<std::string>("mapping.model_joint")[1] - '1';
        ids[i] = m.get<int>("id");
        sign[i] = m.get<double>("calibration.sign");
        offset[i] = m.get<double>("calibration.offset_deg");
        double lo = INFINITY, hi = -INFINITY;
        for (const auto& endpoint : m.get_child("mechanical_range.endpoints")) {
            const double angle = endpoint.second.get<double>("angle_deg");
            lo = std::min(lo, angle);
            hi = std::max(hi, angle);
        }
        lower[i] = lo + margin;
        upper[i] = hi - margin;
    }
    zero = parallel(0, 0).q;
}

Solution Model::parallel(double a, double b) {
    Solution result;
    for (int i = 0; i < 2; ++i) {
        const Vec d = neck::upper(i, a, b) - base(i);
        const double k = (dot(d, d) + R*R - L*L) / (2 * R);
        const double rho = std::hypot(d[1], d[2]);
        if (!(std::abs(k) <= rho)) {
            result.q[i] = std::numeric_limits<double>::quiet_NaN();
            result.reachable = false;
            continue;
        }
        result.q[i] = std::atan2(d[2], d[1]) + (i ? 1 : -1) * std::acos(k / rho);
        const Vec c = crank(result.q[i]), rod = d - c;
        const double angle = std::acos(dot(c, rod) / std::sqrt(dot(c, c) * dot(rod, rod))) * RAD_TO_DEG;
        result.margin = std::min(result.margin, std::min(angle, 180 - angle));
    }
    if (!result.reachable) result.margin = std::numeric_limits<double>::quiet_NaN();
    return result;
}

Solution Model::inverse(const Vec& rpy) const {
    probe::send("head_pose_inverse_input", std::vector<double>(rpy.begin(), rpy.end()));
    const Vec r = rpy / RAD_TO_DEG;
    const double cr = std::cos(r[0]), sr = std::sin(r[0]);
    const double cp = std::cos(r[1]), sp = std::sin(r[1]);
    const double cy = std::cos(r[2]), sy = std::sin(r[2]);
    // C_STD2CAD * (Rz(yaw) Ry(pitch) Rx(roll)) * C_STD2CAD.T 的所需元素。
    const double r00 = cy * cp, r01 = cy * sp * cr + sy * sr, r02 = sy * cr - cy * sp * sr;
    const double a = std::atan2(cy * sr - sy * sp * cr, cp * cr);
    const double b = std::atan2(-r01, std::hypot(r00, r02));
    Solution result = parallel(a, b);
    result.q[2] = std::atan2(r02, r00);
    result.ok = result.reachable && result.margin >= min_margin;
    result.usable = result.ok;
    for (int i = 0; i < 3; ++i) {
        result.q[i] = sign[i] * (result.q[i] - zero[i]) * RAD_TO_DEG + offset[i];
        result.usable = result.usable && result.q[i] >= lower[i] && result.q[i] <= upper[i];
    }
    probe::send("motor_pose_inverse_result", std::vector<double>{
        result.q[0], result.q[1], result.q[2], result.margin,
        double(result.reachable), double(result.usable)});
    return result;
}

Solution Model::evaluate(const Vec& measured) const {
    Solution result;
    result.q = measured;
    const Vec r = forward(measured) / RAD_TO_DEG;
    const double cr = std::cos(r[0]), sr = std::sin(r[0]), cp = std::cos(r[1]), sp = std::sin(r[1]);
    const double cy = std::cos(r[2]), sy = std::sin(r[2]);
    const double a = std::atan2(cy * sr - sy * sp * cr, cp * cr);
    const double b = std::atan2(-(cy * sp * cr + sy * sr),
                                std::hypot(cy * cp, sy * cr - cy * sp * sr));
    for (int i = 0; i < 2; ++i) {
        const Vec c = crank((measured[i] - offset[i]) / (sign[i] * RAD_TO_DEG) + zero[i]);
        const Vec rod = neck::upper(i, a, b) - base(i) - c;
        const double cosine = dot(c, rod) / std::sqrt(dot(c, c) * dot(rod, rod));
        result.reachable = result.reachable && std::isfinite(cosine) &&
                           std::abs(dot(rod, rod) - L * L) < 1e-3;
        const double angle = std::acos(std::clamp(cosine, -1.0, 1.0)) * RAD_TO_DEG;
        result.margin = std::min(result.margin, std::min(angle, 180 - angle));
    }
    if (!result.reachable) result.margin = std::numeric_limits<double>::quiet_NaN();
    result.ok = result.reachable && result.margin >= min_margin;
    result.usable = result.ok;
    for (int i = 0; i < 3; ++i)
        result.usable = result.usable && std::isfinite(measured[i]) &&
                        measured[i] >= lower[i] && measured[i] <= upper[i];
    return result;
}

Vec Model::forward(const Vec& measured) const {
    probe::send("motor_pose_forward_input", std::vector<double>(measured.begin(), measured.end()));
    Vec q;
    for (int i = 0; i < 3; ++i) q[i] = (measured[i] - offset[i]) / (sign[i] * RAD_TO_DEG) + zero[i];
    double a = 0, b = 0;
    constexpr double h = 1e-7;
    for (int n = 0; n < 30; ++n) {
        const Vec f = constraint(q, a, b);
        const Vec ja = (constraint(q, a + h, b) - constraint(q, a - h, b)) / (2 * h);
        const Vec jb = (constraint(q, a, b + h) - constraint(q, a, b - h)) / (2 * h);
        const double det = ja[0] * jb[1] - jb[0] * ja[1];
        const double da = (jb[0] * f[1] - jb[1] * f[0]) / det;
        const double db = (ja[1] * f[0] - ja[0] * f[1]) / det;
        a += da;
        b += db;
        if (std::hypot(da, db) < 1e-12) break;
    }
    const double ca = std::cos(a), sa = std::sin(a), cb = std::cos(b), sb = std::sin(b);
    const double cp = std::cos(q[2]), sp = std::sin(q[2]);
    const Vec probe_rpy = Vec{std::atan2(sa * cp - ca * sb * sp, ca * cb),
               std::asin(std::clamp(-ca * sb * cp - sa * sp, -1.0, 1.0)),
               std::atan2(ca * sp - sa * sb * cp, cb * cp)} * RAD_TO_DEG;
    probe::send("head_pose_forward_result", std::vector<double>(probe_rpy.begin(), probe_rpy.end()));
    return probe_rpy;
}
} // namespace neck
