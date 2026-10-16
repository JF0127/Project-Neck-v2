#pragma once

#include <array>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace neck {
const std::map<std::string, double>& defaults();

struct Sample { double t_s, roll, pitch, yaw; };
using Samples = std::vector<Sample>;
enum class State { Waiting, Following, Decelerating, Holding, Transitioning };
struct Status {
    std::array<double, 3> q{}, rpy{};
    double margin = 0;
    bool usable = false;
    std::string detail;
};

class Controller {
public:
    Controller();
    ~Controller();
    Controller(const Controller&) = delete;
    Controller& operator=(const Controller&) = delete;

    // 异步启动；返回请求是否接受，后台错误由 waitIdle() 抛出。
    bool start();
    void stop();
    void release();
    void moveTo(double roll, double pitch, double yaw, double duration = defaults().at("time"));
    void moveMotors(double q1, double q2, double q3, double duration = defaults().at("time"));
    void sweep(const std::string& axis, double amp, double vel,
               int cycles = int(defaults().at("cycles")), double accel = defaults().at("accel"));
    void stepTest(const std::string& axis, double amp, const std::vector<double>& times, double hold);
    void playFile(const std::string& name, double transition = defaults().at("time"));
    void play(const Samples& samples); // t_s 相对调用时刻，从 0 开始。
    void push(const Samples& samples); // t_s 为 CLOCK_MONOTONIC 秒。
    bool listenUdp(int port = int(defaults().at("port")));
    void stopUdp();
    void setParams(double kp, double kd, double comp, double lead, double tf);
    void setRate(double rate);
    void setLimits(double vmax, double amax);
    void waitIdle();

    Status status() const;
    State state() const;
    std::array<double, 3> rpy() const;    // 当前反馈姿态，deg。
    std::array<double, 3> targetRpy() const; // 轨迹终点目标姿态（限位前），deg。
    std::array<double, 3> motors() const; // 当前反馈电机角度，deg。

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
} // namespace neck
