#pragma once

#include <atomic>
#include <array>
#include <map>
#include <string>
#include <vector>

namespace neck {
using Trajectory = std::map<double, std::array<double, 3>>;
Trajectory moveTrajectory(const std::array<double, 3>& start, const std::array<double, 3>& target,
                          double duration);
Trajectory sweepTrajectory(const std::array<double, 3>& start, const std::string& axis,
                           double amplitude, double velocity, int cycles, double acceleration);
std::vector<std::string> listTrajectories();
Trajectory readTrajectory(const std::string& name);
}

extern "C" {
#include "protocol/motor_control.h"
}

extern std::atomic<bool> neck_bus_ready;
// CAN1 的直接 PDO 目标槽：覆盖最新帧，不进入控制台命令队列。
void writeNeckDirect(const EtherCAT_Msg& frame, const std::array<double, 3>& compensation = {});
bool neckDirectPending();
void finishNeckDirect();
// 仅接收线程写快照；记录的是最近一次有效 PDO 中观察到的电机反馈。
void recordNeckFeedback(const EtherCAT_Msg& raw, const OD_Motor_Msg decoded[6],
                        const EtherCAT_Msg& sent, const std::array<double, 3>& compensation);
