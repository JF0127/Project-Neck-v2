# Project-Neck-v2

将小智板卡输出的机器人语音与文本转换为 PC 音频播放和三电机颈部动作。`bridge` 解码带轮次标识的 Opus 音频，`player` 建立统一播放时间轴，`motion` 按文本生成姿态切换、点头和摇头，`motor` 负责插值、运动学、限位及 EtherCAT/CAN 控制。

当前动作是 **min-jerk 姿态切换 + 手势叠加**，不再使用说话慢摆、待机正弦微动或音量驱动幅度；roll 输出恒为 0。

详细格式、命令与配置见 [docs/INTERFACE.md](docs/INTERFACE.md)。本文及该接口文档按当前源码整理；板卡固件和云端语音服务不在本仓库内。

## 整体结构

```text
小智板卡（需支持 BoardBridge TCP 消息）
    │  TCP：带 turn_id 的 Opus 音频 + 单行 JSON 控制/文本
    │  PC 默认监听 0.0.0.0:8766
    ▼
bridge / Bridge
    │  turn_start / audio / text / turn_end / abort
    ▼
speech_motion / player
    ├── sounddevice / PortAudio ──► PC 声卡、扬声器
    │
    │  wall_base = 首个音频回调到达时刻 + D
    │  轮次事件、文本和音频范围
    ▼
speech_motion / motion
    │  30 Hz，持续输出带 CLOCK_MONOTONIC 时间戳的 RPY
    │  UDP 127.0.0.1:9870
    ▼
motor / master_stack_test（NeckStream）
    │  默认 500 Hz 规划、插值、运动学与限位
    │  SOEM / EtherCAT，PDO 通信循环 1000 Hz
    ▼
EtherCAT-CAN 从站 ──► 三个电机 ──► 头部姿态
```

目录：

| 路径 | 用途 |
|---|---|
| `bridge/` | TCP 接收、Opus 解码、轮次事件；独立运行可录 WAV |
| `speech_motion/player.py` | 定时播放及 bridge/motion 接入 |
| `speech_motion/motion.py` | 随机基础姿态切换、文本手势、UDP 发送 |
| `speech_motion/config.json` | 播放延迟与动作参数 |
| `motor/src/`、`motor/include/` | C++ 电机控制与公开接口 |
| `motor/config/` | 运行参数、机构标定、补偿表 |
| `motor/data/demos/` | 当前 `NeckPlay` 使用的演示轨迹，保留在版本管理范围内 |
| `motor/data/sweep/`、`motor/data/analysis/` | 命令/反馈日志、分析图，不提交 |
| `motor/thirdparty/` | SOEM、cpp-readline 等第三方代码 |

## 硬件与依赖

### 硬件

- Linux PC，连接板卡的网络，以及连接 EtherCAT 从站的网口。
- 支持本项目 TCP 协议的小智板卡；将板卡连接目标设为 **PC 的实际局域网 IP、端口 8766**，不要填监听通配地址 `0.0.0.0`。
- PC 可用的声卡和扬声器。播放使用 `sounddevice.OutputStream` 的默认输出设备、单声道、低延迟模式。
- EtherCAT-CAN 从站和三电机颈部。当前标定记录的电机型号为 **ENCOS EC-A2806-P2-36**，ID 为 1、2、3；颈部使用从站索引 0 的 CAN1 三个槽位。
- 当前 EtherCAT 网卡名硬编码为 **`enp4s0`**，见 `motor/src/ethercat/transmit.cpp`。换机器时必须先处理该配置限制。

运动前确认头部有活动空间；断电或卸力会使头部下落，不能把软件限位当成机械安全保证。

### 软件安装与编译

需要 Python **3.10 或以上**、NumPy、sounddevice、系统 libopus 和 PortAudio；C++ 部分使用 C++17、CMake、Boost、Readline，SOEM 源码已随仓库提供。

Ubuntu/Debian 示例：

```bash
sudo apt install build-essential cmake libboost-dev libreadline-dev \
    python3-venv python3-pip libopus0 libportaudio2

cd /home/jhl/projects/Project-Neck-v2
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install numpy sounddevice

cmake -S motor -B motor/build
cmake --build motor/build -j
```

`motor/CMakeLists.txt` 当前把演示目录硬编码为 `/home/jhl/projects/Project-Neck-v2/motor/data/demos`；仓库换位置时需相应调整后重新构建。运行参数和标定路径也在构建时指向 `motor/config/`。

## 启动顺序

顺序为 **`master_stack_test` → `NeckStatus` → `NeckStream` → `python3 -m speech_motion.player`**。

### 1. 启动 motor 并确认姿态

终端 A：

```bash
cd /home/jhl/projects/Project-Neck-v2
sudo ./motor/build/master_stack_test
```

SOEM 使用原始套接字，示例以 root 权限运行。程序先连接 EtherCAT，再启动颈部控制；等待初始化结果后，在它的交互控制台输入：

```text
NeckStatus
```

若断电后头部落出可用范围，先回中：

```text
NeckRpySet 0 0 0 time=3
```

该命令是异步的；等头部运动完成，再执行 `NeckStatus`，确认 `usable=yes` 后继续，不要用 `MotorZeroSet` 代替回中。

### 2. 开启 UDP 输入

仍在终端 A 的控制台：

```text
NeckStream port=9870
```

控制台保持可用；`q` 停止 UDP 接收并保持当前规划目标。不要同时启动第二个电机控制实例。

### 3. 启动 player、bridge 和 motion

终端 B：

```bash
cd /home/jhl/projects/Project-Neck-v2
source .venv/bin/activate
python3 -m speech_motion.player
```

这一条命令同时创建 bridge、播放器和 motion 线程，不需要再单独启动 `python3 -m bridge`。即使没有语音，motion 也会持续发送待机姿态，等待 2.5～5 秒后开始第一次随机切换。

player 可选参数：

```bash
python3 -m speech_motion.player --host 0.0.0.0 --port 8766
```

确认板卡接入同一网络并连接该 TCP 服务。bridge 只接受一个同时在线的客户端；当前协议没有认证或 TLS，应仅在可信网络中使用。

### 停止

先在终端 B 按 `Ctrl+C` 停止 player/motion，再在 motor 控制台输入 `q`；需要停止颈部控制时用 `NeckStop`。`NeckStop` 保留电机最后目标，不等于卸力；`NeckRelease` 切到仅阻尼，使用前须支撑头部。

## 运行演示轨迹

演示只需要 motor，不需要板卡或 player。先停止正在运行的 player，避免其 UDP 流干扰演示，然后在 motor 控制台操作：

```text
q
NeckStatus
NeckPlay
NeckPlay 01_nod_yes
NeckPlay 02_shake_no
NeckPlay 11_speech_sway_test time=1 kp=150 kd=2
```

上述回放命令应逐条执行，等上一条完成后再执行下一条；新的回放会替换旧轨迹。

- 单独输入 `NeckPlay` 列出当前演示目录中的 `.csv` 文件。
- 只接受文件名，可省略 `.csv`，**不接受路径**。
- 当前目录是 `motor/data/demos/`，不是旧的 `motor/assets/trajectories/`。
- `time` 是当前姿态到文件首帧的过渡时长，不会缩放文件内部时间轴。
- CSV 格式为 `t_s,roll_deg,pitch_deg,yaw_deg`，时间从 0 开始并严格递增；格式详情见接口文档。
- `11_speech_sway_test` 是保留的慢摆测试轨迹，包含 roll，不代表当前 motion 仍使用慢摆。

默认命令与反馈日志分别写入 `motor/data/sweep/session_rate{rate}_{timestamp}.csv` 和同名前缀的 `_fb.csv`。

仅排查板卡数据、需要录音而不播放或运动时，可单独使用：

```bash
python3 -m bridge --host 0.0.0.0 --port 8766 --output bridge_wavs
```

它与 player 默认占用同一个 TCP 端口，不要同时运行。

## 当前限制

- 当前整条板卡语音链路不支持说话过程中语音打断；代码有 `abort` 处理，但不等于上游已具备打断能力。
- 低速 roll 有黏滑现象；历史慢摆已取消，当前 motion 的 roll 固定为 0。
- 网卡名和演示目录存在硬编码；断电后通常需要先执行 `NeckRpySet 0 0 0 time=3`。
- 完整限制及地址/敏感配置排查见 [接口文档](docs/INTERFACE.md)。

## 第三方许可证

本仓库内的 SOEM 许可证以 [`motor/thirdparty/soem/LICENSE`](motor/thirdparty/soem/LICENSE) 为准：

- **GNU GPL version 2，带文件中明确列出的链接等特殊例外**，不是 MIT 或 BSD 许可证。
- 例外说明：仅因链接、模板、宏或内联函数使用该文件，不会由该文件本身使整个组合程序自动受 GPL 覆盖；但相关 SOEM 源码仍须依 GPL 第 3 条履行源码提供义务，且例外不排除其他 GPL 适用理由。
- 分发时应保留版权、许可证和免责声明，处理 SOEM 及其修改的相应源码义务。
- 许可证还声明 EtherCAT 技术、名称和标志属于 Beckhoff，并要求取得适用的 EtherCAT Master License。

另外，仓库内 `cpp-readline/LICENSE` 是 GPLv3，GNU Readline 也有自身许可要求；SOEM 的例外不豁免其他依赖的义务，不能据此把整个控制台程序视为宽松许可。以上为随仓库许可证的说明，不替代完整条款。
