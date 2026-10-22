/*
 * @Description:
 * @Author: kx zhang
 * @Date: 2022-09-13 19:00:55
 * @LastEditTime: 2022-11-13 17:09:03
 */

#include <cstdio>
#include <readline/readline.h>
#include <map>

#include "probe.hpp"
#include "Console.hpp"
#include "command.h"
#include "time.h"

extern "C" {
#include "ethercat.h"
}

namespace cr = CppReadline;
using ret = cr::Console::ReturnCode;
std::thread comThread;

namespace {
void probeConsoleRequest(const std::vector<std::string>& input) noexcept {
    try {
        std::string text;
        for (const auto& word : input) {
            if (!text.empty()) text += ' ';
            text += word;
        }
        probe::send("motor_console_request", text);
    } catch (...) {}
}
void probeConsoleResponse(const std::string& command, unsigned code) noexcept {
    try {
        probe::send("motor_console_response", command + ": return=" + std::to_string(code));
    } catch (...) {}
}
}

void comImpl() {
    cr::Console c(">");
    
    std::map<std::string, cr::Console::CommandFunction> commands = {
        {"help", help},
        {"MotorIdGet", motorIdGet},
        {"MotorIdSet", motorIdSet},
        {"MotorIdReset", motorIdReset},
        {"MotorZeroSet", motorZeroSet},
        {"MotorStop", motorStop},
        {"MotorSpeedSet", motorSpeedSet},
        {"MotorPositionSet", motorPositionSet},
        {"MotorAngelGet", motorAngelGet},
        {"NeckMotorSet", neckCommand},
        {"NeckRpySet", neckCommand},
        {"NeckSweep", neckCommand},
        {"NeckStepTest", neckCommand},
        {"NeckPlay", neckCommand},
        {"NeckStream", neckCommand},
        {"NeckStop", neckCommand},
        {"NeckRelease", neckCommand},
        {"NeckStatus", neckCommand},
        {"q", neckCommand}
    };

    for (const auto& cmd : commands) {
        if (cmd.first.rfind("Motor", 0) == 0) {
            c.registerCommand(cmd.first, [execute = cmd.second](const std::vector<std::string>& input) {
                probeConsoleRequest(input);
                try {
                    if (input[0] == "MotorSpeedSet" || input[0] == "MotorPositionSet" || input[0] == "MotorStop")
                        stopNeck();
                    const auto code = execute(input);
                    probeConsoleResponse(input[0], code);
                    return code;
                } catch (const std::exception& e) {
                    std::cout << input[0] << ": " << e.what() << '\n';
                    probe::send("motor_console_response", e.what());
                    return 1;
                }
            });
        } else c.registerCommand(cmd.first, [execute = cmd.second](const std::vector<std::string>& input) {
            probeConsoleRequest(input);
            const auto code = execute(input);
            probeConsoleResponse(input[0], code);
            return code;
        });
    }

    c.executeCommand("help");
    int retCode;
    do {
        retCode = c.readLine();
        // We can also change the prompt based on last return value:
        if (retCode == ret::Ok)
            c.setGreeting(">");
        else
            c.setGreeting("!>");

        if (retCode == 1) {
            std::cout << "Received error code 1\n";
        } else if (retCode == 2) {
            std::cout << "Received error code 2\n";
        }

        usleep(100000);
        std::cout << std::endl;
    } while (retCode != ret::Quit);
}

int main() {
    printf("SOEM 主站测试\n");
    if (!startBus()) return 1;
    loadNeckRuntime();

    comThread = std::thread(comImpl);
    comThread.join();

    shutdownNeck();
    return 0;
}

