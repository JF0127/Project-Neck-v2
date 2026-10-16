# motor 接口

## 1. 概述

控制三电机颈部的头部姿态、轨迹回放和实时流式运动。提供进程内 C++ 接口和本机 UDP 接口。

## 2. 约定

| 项目 | 约定 |
|---|---|
| 姿态顺序、单位 | `roll, pitch, yaw`；角度 °，速度 °/s，加速度 °/s²，时长 s |
| 正方向（机器人自身视角） | `pitch > 0` 低头；`yaw > 0` 向左转；`roll > 0` 向右歪 |
| 单轴参考可用范围 | pitch −18.5°～22.2°；roll ±21.7°；yaw ±86.2°（其余轴为 0） |
| 组合运动 | roll 与 pitch 同时运动约各 ±8°；不能把单轴范围直接组合使用 |
| 越界目标 | 超出可用范围会被压回可用边界，不保证到达原始目标 |
| 电机角度 | `q1, q2, q3` 为驱动器输出轴绝对角度，不是头部 RPY |
| 默认参数 | `config/runtime.json`；修改文件后重启，运行时可用接口或命令参数覆盖 |

## 3. C++ 接口

头文件：`include/neck/controller.h`；链接 CMake 目标 `motor`（产物 `libmotor.a`，依赖 `soem`）。使用 C++17，同一套电机只运行一个控制实例。

```cmake
add_subdirectory(motor)
add_executable(app main.cpp)
target_compile_features(app PRIVATE cxx_std_17)
target_link_libraries(app PRIVATE motor)
```

```cpp
#include <neck/controller.h>

// 头文件提供的类型：
// neck::Sample { double t_s, roll, pitch, yaw; }
// neck::Samples = std::vector<neck::Sample>
// neck::State = Waiting / Following / Decelerating / Holding / Transitioning
const std::map<std::string, double>& config = neck::defaults();
```

下表为 `neck::Controller` 的公开接口；“异步”表示不等待动作完成，随后调用 `waitIdle()` 等待并接收错误。

| 签名 | 用法 / 是否阻塞 |
|---|---|
| `Controller()` | 创建实例，不连接电机、不启动运动 |
| `~Controller()` | 阻塞至控制停止、EtherCAT 关闭；不等同于 `release()` |
| `Controller(const Controller&) = delete` | 不可复制 |
| `Controller& operator=(const Controller&) = delete` | 不可复制赋值 |
| `bool start()` | 异步连接并保持当前反馈位置；返回请求是否接受，成功与否用 `waitIdle()` 获取；失败后可再次调用 |
| `void stop()` | 异步停止控制和 UDP 接收；电机最后目标仍有效，不卸力 |
| `void release()` | 异步停止控制，向三电机发送一次 `kp=0, kd=1, tor=0`，仅保留阻尼 |
| `void moveTo(double roll, double pitch, double yaw, double duration = defaults().at("time"))` | 异步移动到头部 RPY；`duration` 为运动时长，默认 1 s |
| `void moveMotors(double q1, double q2, double q3, double duration = defaults().at("time"))` | 异步移动到电机角度对应的姿态；`duration` 默认 1 s |
| `void sweep(const std::string& axis, double amp, double vel, int cycles = int(defaults().at("cycles")), double accel = defaults().at("accel"))` | 异步单轴往返扫动后回零；`axis` 为 `roll/pitch/yaw`，`amp` 幅度、`vel` 速度、`cycles` 轮数（默认 1）、`accel` 轨迹加速度（默认 100） |
| `void stepTest(const std::string& axis, double amp, const std::vector<double>& times, double hold)` | 阻塞至各段运动及保持结束；先回零，每个 `T` 执行 `0 → amp → 保持 hold → 0 → 保持 hold`，移动时长均为 `T` |
| `void playFile(const std::string& path)` | 异步回放 CSV；仅传文件名时从 `assets/trajectories/` 查找，否则使用所给路径 |
| `void play(const Samples& samples)` | 异步回放整段样本；`t_s` 为相对调用时刻的秒数，从 0 开始 |
| `void push(const Samples& samples)` | 异步提交流式样本；`t_s` 为绝对 `CLOCK_MONOTONIC` 秒，无需先调用 `listenUdp()` |
| `bool listenUdp(int port = int(defaults().at("port")))` | 同步绑定本机 UDP 端口，异步开启接收；不等待样本，绑定失败返回 `false`；默认 9870 |
| `void stopUdp()` | 异步停止 UDP 接收并保持当前目标；控制仍运行 |
| `void setParams(double kp, double kd, double comp, double lead, double tf)` | 异步设置 PD 增益、补偿倍率、补偿提前量（ms）、摩擦前馈幅值（Nm） |
| `void setRate(double rate)` | 异步设置控制频率（Hz），默认 500；不是流式样本发送帧率 |
| `void setLimits(double vmax, double amax)` | 异步设置 RPY 各轴速度、加速度上限；各自取 0 表示不限制，默认均为 0 |
| `void waitIdle()` | 阻塞至启动/停止处理完成或运动进入空闲保持；抛出后台错误；不保证实际姿态已到达目标 |
| `State state() const` | 同步读取当前状态，不等待运动完成 |
| `std::array<double, 3> rpy() const` | 同步读取反馈换算的头部 RPY（°），不等待运动完成 |
| `std::array<double, 3> targetRpy() const` | 同步读取最近一次定点/轨迹的终点目标 RPY（限位前）；不是流式实时目标 |
| `std::array<double, 3> motors() const` | 同步读取三个电机反馈角度（°），不等待运动完成 |

运动及接收样本前先 `start(); waitIdle();`；停止后重新运动也需再次启动。持续流式输入期间不要用 `waitIdle()` 等待每一帧。

最小调用示例：

```cpp
#include <neck/controller.h>
#include <time.h>

int main() {
    neck::Controller c;
    c.start();
    c.waitIdle();                       // 启动失败会抛出异常
    c.moveTo(0, 5, 0, 1.0);
    c.waitIdle();

    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    const double t0 = ts.tv_sec + ts.tv_nsec * 1e-9 + 0.2;
    neck::Samples samples;
    for (int i = 0; i <= 30; ++i)
        samples.push_back({t0 + i / 30.0, 0, 5, 0}); // 30 Hz，保持当前姿态
    c.push(samples);
    c.waitIdle();                       // 样本耗尽后减速、保持
    c.stop();
    c.waitIdle();                       // 等待停止；若需卸力则改用 release()
}
```

轨迹 CSV（`playFile` / `NeckPlay`）：

```csv
t_s,roll_deg,pitch_deg,yaw_deg
0,0,0,0
1,0,5,0
2,0,0,0
```

## 4. UDP 接口

先在控制台执行 `NeckStream port=9870`，或在 C++ 中完成 `start(); waitIdle();` 后调用 `listenUdp()`。

| 项目 | 用法 |
|---|---|
| 地址、默认端口 | `127.0.0.1:9870`，仅本机；端口可覆盖 |
| 报文 | 文本，每行 `t_mono_s,roll_deg,pitch_deg,yaw_deg`，一个报文可含多行，无表头 |
| 时间戳 | `CLOCK_MONOTONIC` 秒，表示姿态应到达的时刻；不是 Unix 时间或相对时长 |
| 提前量 | 样本需提前送达，例如预留 0.2～0.4 s；接收时已过期的样本会被丢弃 |
| 样本帧率 | 建议不低于 25 Hz，可将多帧合并发送 |
| 断流 / 恢复 | 断流自动减速停住；恢复后平滑接上 |

Python 发送示例（30 Hz，提前 0.2 s）：

```python
import math, socket, time
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for i in range(150):
    t = time.clock_gettime(time.CLOCK_MONOTONIC) + 0.2
    yaw = 5 * math.sin(i / 30)
    sock.sendto(f"{t:.6f},0,0,{yaw:.4f}\n".encode(), ("127.0.0.1", 9870))
    time.sleep(1 / 30)
```

## 5. 终端测试

在 `motor/` 根目录执行；Linux，EtherCAT 网卡为 `enp4s0`：

```bash
cmake -S . -B build
cmake --build build -j
sudo ./build/master_stack_test
```

启动自动连接并保持当前反馈位置，打印电机角度、RPY 和可用范围状态；失败后控制台仍可用，下一条 Neck 运动命令会重试。

`axis` 取 `roll/pitch/yaw`；方括号表示可选参数，不要原样输入。参数以 `名字=值` 覆盖，置于必填参数之后，顺序不限。

| 命令格式 | 一行示例 |
|---|---|
| `NeckRpySet roll pitch yaw [time=1]` | `NeckRpySet 0 5 0 time=1 kp=150 kd=2` |
| `NeckMotorSet q1 q2 q3 [time=1]` | `NeckMotorSet 38.42013 130.310631 147.642963 time=1` |
| `NeckSweep axis amp vel [cycles=1] [accel=100]` | `NeckSweep yaw 10 5 cycles=2 accel=100` |
| `NeckStepTest axis amp [times=1,0.6,0.4,0.3,0.25,0.2,0.15] [hold=1]` | `NeckStepTest yaw 5 times=1,0.6,0.3 hold=1 vmax=0 amax=0` |
| `NeckPlay file` | `NeckPlay 01_combo_smooth_100hz.csv kp=150 kd=2` |
| `NeckStream [port=9870]` | `NeckStream port=9870 rate=500` |
| `NeckStop` | `NeckStop` |
| `NeckRelease` | `NeckRelease` |

| 可选参数 | 当前默认值 | 含义 |
|---|---:|---|
| `kp` / `kd` | 150 / 2 | PD 增益 |
| `comp` | 0 | 补偿倍率；0 关闭 |
| `lead` | 0 | 补偿提前量，ms |
| `tf` | 0 | 摩擦前馈幅值，Nm |
| `rate` | 500 | 控制频率，Hz |
| `vmax` / `amax` | 0 / 0 | 各轴速度 / 加速度上限；0 不限制 |

运动命令支持上表参数；所有 Neck 命令均接受 `vmax=`、`amax=`。`accel` 仅设置扫动轨迹加速度，`amax` 是执行器上限。

- 定点、扫动、回放及 StepTest 等待结束后打印反馈/目标 RPY，任一轴误差超过 1° 时警告；末端目标继续保持。
- `NeckStream` 按 `q` 停止接收并保持；`NeckStop` 停止控制但不卸力；`NeckRelease` 改为仅阻尼。
- `NeckStepTest` 使用最小加加速度曲线，每个去程/回程开始打印 `T` 和理论峰值速度、加速度。

UDP 模拟器：先执行 `NeckStream port=9870`，再在另一终端的 `motor/` 目录运行：

```bash
python3 tools/stream_sender_sim.py assets/trajectories/02_speech_gestures_100hz.csv --port 9870 --hz 30 --delay 0.4
# 模拟第 10 s 处断流 0.8 s
python3 tools/stream_sender_sim.py assets/trajectories/02_speech_gestures_100hz.csv --port 9870 --hz 30 --delay 0.4 --stall 10 0.8
```

## 6. 日志

默认写入 `data/sweep/session_rate{rate}_{timestamp}.csv`（主日志）和同名前缀的 `_fb.csv`（反馈日志）；由 `config/runtime.json` 的 `log` 开关控制，主日志 `step_id=+n/-n` 表示第 n 个时长的去程/回程及其保持段，`0` 表示非测试段。
