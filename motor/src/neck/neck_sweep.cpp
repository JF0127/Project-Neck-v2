#include "neck_kinematics.h"
#include "neck_stream.h"
#include "neck_sweep.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace neck {
Trajectory moveTrajectory(const Vec& start, const Vec& target, double duration) {
    constexpr double dt = .01;
    Trajectory points{{0, start}};
    const Polynomial curve({start}, {target}, 0, duration);
    for (int i = 1; i <= int(std::ceil(duration / dt)); ++i) {
        const double time = std::min(i * dt, duration);
        points[time] = curve.sample(time).p;
    }
    points.rbegin()->second = target;
    return points;
}

Trajectory sweepTrajectory(const Vec& start, const std::string& axis, double amplitude,
                           double velocity, int cycles, double acceleration) {
    constexpr double dt = .01;
    Trajectory points{{0, start}};
    auto append = [&](const Vec& end) {
        const double start_time = points.rbegin()->first;
        const Vec from = points.rbegin()->second, delta = end - from;
        const double distance = maxAbs(delta);
        if (distance < 1e-12) { points.rbegin()->second = end; return; }
        const double ta = std::min(velocity / acceleration, std::sqrt(distance / acceleration));
        const double peak = acceleration * ta;
        const double tc = std::max(0.0, distance / peak - ta), duration = 2 * ta + tc;
        const int count = std::max(1, int(std::ceil(duration / dt)));
        const double scale = count * dt / duration;
        for (int i = 1; i <= count; ++i) {
            const double t = i * dt / scale;
            const double s = t < ta ? .5 * acceleration * t * t :
                (t < ta + tc ? .5 * acceleration * ta * ta + peak * (t - ta) :
                 distance - .5 * acceleration * (duration - t) * (duration - t));
            points[start_time + i * dt] = from + delta * (s / distance);
        }
        points.rbegin()->second = end;
    };
    const std::map<std::string, int> axes{{"roll", 0}, {"pitch", 1}, {"yaw", 2}};
    Vec peak{};
    peak[axes.at(axis)] = amplitude;
    append({});
    const double home_time = points.rbegin()->first;
    for (int i = 1; i <= 500; ++i) points[home_time + i * dt] = {};
    append(peak);
    for (int i = 0; i < cycles; ++i) {
        append(peak * -1);
        append(peak);
    }
    append({});
    return points;
}

std::vector<std::string> listTrajectories() {
    std::vector<std::string> names;
    if (!std::filesystem::exists(NECK_TRAJECTORY_DIR)) return names;
    for (const auto& entry : std::filesystem::directory_iterator(NECK_TRAJECTORY_DIR))
        if (entry.is_regular_file() && entry.path().extension() == ".csv")
            names.push_back(entry.path().filename().string());
    std::sort(names.begin(), names.end());
    return names;
}

Trajectory readTrajectory(const std::string& name) {
    std::filesystem::path file(name);
    if (name.empty() || file.has_parent_path() || file == "." || file == "..")
        throw std::runtime_error("NeckPlay expects a name, not a path");
    if (file.extension() != ".csv") file += ".csv";
    const auto path = std::filesystem::path(NECK_TRAJECTORY_DIR) / file;
    std::ifstream csv(path);
    if (!csv) throw std::runtime_error("Cannot open " + path.string());
    Trajectory points;
    std::string line;
    int number = 0;
    while (std::getline(csv, line)) {
        ++number;
        if (number == 1 && line.compare(0, 3, "\xef\xbb\xbf") == 0) line.erase(0, 3);
        if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
        std::istringstream row(line);
        double time;
        Vec p;
        char a, b, c;
        if (!(row >> time)) {
            if (number == 1) continue; // 仅非数字首行作为表头。
            throw std::runtime_error("Invalid CSV time at line " + std::to_string(number));
        }
        if (!(row >> a >> p[0] >> b >> p[1] >> c >> p[2]) || a != ',' || b != ',' || c != ',' ||
            !std::isfinite(time) || !std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2]))
            throw std::runtime_error("Expected t_s,roll_deg,pitch_deg,yaw_deg at line " + std::to_string(number));
        row >> std::ws;
        if (!row.eof()) throw std::runtime_error("Extra CSV data at line " + std::to_string(number));
        if (points.empty() ? time != 0 : time <= points.rbegin()->first)
            throw std::runtime_error("CSV time must start at 0 and strictly increase (line " + std::to_string(number) + ')');
        points[time] = p;
    }
    if (!csv.eof()) throw std::runtime_error("Cannot read " + path.string());
    if (points.empty()) throw std::runtime_error("Empty trajectory: " + path.string());
    return points;
}
} // namespace neck
