#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <string>

namespace neck {
using Vec = std::array<double, 3>;
constexpr double RAD_TO_DEG = 57.29577951308232;
inline Vec operator+(Vec a, const Vec& b) { for (int i = 0; i < 3; ++i) a[i] += b[i]; return a; }
inline Vec operator-(Vec a, const Vec& b) { for (int i = 0; i < 3; ++i) a[i] -= b[i]; return a; }
inline Vec operator*(Vec a, double b) { for (double& x : a) x *= b; return a; }
inline Vec operator/(Vec a, double b) { return a * (1 / b); }
inline double dot(const Vec& a, const Vec& b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
inline double maxAbs(const Vec& a) { return std::max({std::abs(a[0]), std::abs(a[1]), std::abs(a[2])}); }

struct Solution {
    Vec q{}; // 驱动器绝对角度，deg；parallel() 内部为模型弧度。
    double margin = 90;
    bool reachable = true, ok = false, usable = false;
};

struct Model {
    std::array<int, 3> ids{};
    Vec sign{}, offset{}, lower{}, upper{}, zero{};
    double min_margin;
    explicit Model(const std::string& config);
    Solution inverse(const Vec& rpy) const; // 输入、输出均为 deg。
    Vec forward(const Vec& q) const;       // 启动归零时由反馈恢复当前姿态。
    Solution evaluate(const Vec& q) const; // 按实际电机角度检查范围及传动余量。
private:
    static Solution parallel(double a, double b);
};
} // namespace neck
