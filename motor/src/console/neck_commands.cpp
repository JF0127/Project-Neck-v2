#include <cmath>

#include "command.h"
#include "probe.hpp"
#include "neck/controller.h"
#include "neck/neck_sweep.h"

#include <map>
#include <sstream>
#include <stdexcept>

namespace {
std::unique_ptr<neck::Controller> controller;
const std::map<std::string, size_t> required = {
    {"NeckRpySet", 3}, {"NeckMotorSet", 3}, {"NeckSweep", 3}, {"NeckStepTest", 2},
    {"NeckPlay", 1}, {"NeckStream", 0}, {"NeckStop", 0}, {"NeckRelease", 0},
    {"NeckStatus", 0}, {"q", 0}
};
}

void loadNeckRuntime() {
    controller = std::make_unique<neck::Controller>();
    controller->start(); // 后台查询/重试不阻塞查询与停止命令。
}
void stopNeck() {
    controller->stop();
    try { controller->waitIdle(); }
    catch (const std::exception& e) { std::cout << "Neck stop: " << e.what() << '\n'; }
}
void warnNeckRange() {
    try {
        const auto status = controller->status();
        if (!status.usable) std::cout << "WARNING (raw motor command allowed): " << status.detail << '\n';
    } catch (const std::exception& e) {
        std::cout << "WARNING (raw motor command allowed): " << e.what() << '\n';
    }
}
void shutdownNeck() { controller.reset(); }

void neckHelp() {
    std::cout << "\tNeckRpySet roll pitch yaw [time=]\n"
              << "\tNeckMotorSet q1 q2 q3 [time=]\n"
              << "\tNeckSweep axis(roll/pitch/yaw) amp vel [cycles=] [accel=]\n"
              << "\tNeckStepTest axis(roll/pitch/yaw) amp [times=1,0.6,0.4,0.3,0.25,0.2,0.15] [hold=1]\n"
              << "\tNeckPlay [name[.csv]] (no name: list demos)\n"
              << "\tNeckStream [port=]\n"
              << "\tNeckStop\n"
              << "\tNeckRelease\n"
              << "\tNeckStatus\n"
              << "\tq (stop UDP stream)\n"
              << "Neck motion commands: [kp=] [kd=] [comp=] [lead=] [tf=] [rate=] (any order)\n"
              << "All Neck commands: [vmax=] [amax=] (0=unlimited)\n"
              << "Units: angles=deg, vel/vmax=deg/s, accel/amax=deg/s^2, time/times/hold=s, lead=ms, rate=Hz\n"
              << "Current defaults:";
    for (const auto& entry : neck::defaults()) std::cout << ' ' << entry.first << '=' << entry.second;
    std::cout << '\n';
}

unsigned neckCommand(const std::vector<std::string>& input) {
    const size_t first_option = required.at(input[0]) + 1;
    try {
        if (input[0] == "NeckPlay" && input.size() == 1) {
            const auto names = neck::listTrajectories();
            for (const auto& name : names) std::cout << name << '\n';
            if (names.empty()) std::cout << "NeckPlay: no .csv files.\n";
            return 0;
        }
        if (input.size() < first_option) {
            neckHelp();
            return 1;
        }
        if (input[0] != "NeckStatus") {
            try {
                std::string probe_request;
                for (const auto& word : input) {
                    if (!probe_request.empty()) probe_request += ' ';
                    probe_request += word;
                }
                probe::send("neck_motion_request", probe_request);
            } catch (...) {}
        }
        if (input[0] == "NeckStop") {
            stopNeck();
            std::cout << "NeckStop: trajectory stopped; last motor target retained.\n";
            return 0;
        }
        if (input[0] == "NeckRelease") {
            controller->release();
            controller->waitIdle();
            std::cout << "NeckRelease: " << (running ? "damping command submitted" : "bus unavailable") << '\n';
            return 0;
        }
        if (input[0] == "NeckStatus") {
            const auto status = controller->status();
            std::cout << status.detail << "; RPY=" << status.rpy[0] << ' ' << status.rpy[1] << ' '
                      << status.rpy[2] << " deg; usable=" << (status.usable ? "yes" : "no") << '\n';
            return 0;
        }
        if (input[0] == "q") {
            controller->stopUdp();
            std::cout << "NeckStream: stop requested.\n";
            return 0;
        }
        auto options = neck::defaults();
        std::vector<double> times{1, 0.6, 0.4, 0.3, 0.25, 0.2, 0.15};
        double hold = 1;
        for (size_t i = first_option; i < input.size(); ++i) {
            const auto split = input[i].find('=');
            const auto key = input[i].substr(0, split), value = input[i].substr(split + 1);
            if (key == "times") {
                times.clear();
                std::istringstream list(value);
                std::string time;
                while (std::getline(list, time, ',')) times.push_back(std::stod(time));
            } else if (key == "hold") hold = std::stod(value);
            else options.at(key) = std::stod(value);
        }
        controller->setLimits(options.at("vmax"), options.at("amax"));
        controller->setParams(options.at("kp"), options.at("kd"), options.at("comp"),
                              options.at("lead"), options.at("tf"));
        controller->setRate(options.at("rate"));
        controller->start();
        if (input[0] == "NeckStream") {
            const int port = int(options.at("port"));
            if (!controller->listenUdp(port)) throw std::runtime_error("UDP bind failed");
            std::cout << "NeckStream: requested UDP 127.0.0.1:" << port << "; enter q to stop.\n";
            return 0;
        }
        if (input[0] == "NeckPlay") controller->playFile(input[1], options.at("time"));
        else if (input[0] == "NeckStepTest")
            controller->stepTest(input[1], std::stod(input[2]), times, hold);
        else if (input[0] == "NeckSweep")
            controller->sweep(input[1], std::stod(input[2]), std::stod(input[3]),
                              int(options.at("cycles")), options.at("accel"));
        else if (input[0] == "NeckMotorSet")
            controller->moveMotors(std::stod(input[1]), std::stod(input[2]), std::stod(input[3]), options.at("time"));
        else controller->moveTo(std::stod(input[1]), std::stod(input[2]), std::stod(input[3]), options.at("time"));
        std::cout << input[0] << " queued; console remains available.\n";
        return 0;
    } catch (const std::exception& e) {
        std::cout << input[0] << ": " << e.what() << '\n';
        return 1;
    }
}
