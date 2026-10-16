# 模块接口与运行约定

本文以当前工作区的 `bridge/`、`speech_motion/`、`motor/src/`、`motor/include/` 及配置文件为准。角度默认使用度，时长默认使用秒；除特别说明外，时间戳均不是 Unix 时间。启动流程见 [README](../README.md)。

## 1. motor UDP 输入与 C++ push

### 1.1 地址、数据格式与坐标

先在 motor 控制台执行 `NeckStream port=9870`，或在已启动的 C++ 控制器上调用 `listenUdp(9870)`。

| 项目 | 当前约定 |
|---|---|
| UDP 地址 | `127.0.0.1:9870`，只绑定本机回环；motor 端口可通过命令或 C++ 参数修改 |
| 格式 | UTF-8/ASCII 数值文本，每行 `t_mono_s,roll_deg,pitch_deg,yaw_deg`，无表头；一个报文可含多行 |
| 时间单位 | 秒，绝对 `CLOCK_MONOTONIC` 时刻；Linux Python 的 `time.monotonic()` 与之对应 |
| 姿态顺序 | roll、pitch、yaw；不是三个电机的输出轴角度 |
| 角度 / 速度 / 加速度 | ° / °/s / °/s² |
| 正方向 | 机器人自身视角：`pitch > 0` 低头，`yaw > 0` 左转，`roll > 0` 右歪 |
| 发送帧率 | 应不低于 **25 Hz**；当前 motion 为 30 Hz，接收器不强制检查最低帧率 |
| motor 控制频率 | 默认 500 Hz，与发送帧率不同；底层 EtherCAT PDO 循环为 1000 Hz |

报文示意，时间值必须由发送程序实时生成，不能照抄示例数字：

```text
<t0>,0,1,3
<t0+1/30>,0,1,3
```

参考可用范围来自 `motor/config/neck_calibration.json`，均以其余轴为 0 的单轴情况为基准：

| 轴 | 单轴参考范围 |
|---|---|
| roll | 约 −21.7°～+21.7° |
| pitch | 约 −18.5°～+22.2° |
| yaw | 约 −86.2°～+86.2° |

**roll 与 pitch 同时运动，保守范围约各 ±8°**；不能把各单轴极值直接拼成一个组合姿态。实际可用性还受传动角裕度、电机软限位和机构耦合限制，以 `NeckStatus` 和模型判断为准。这些是标定/模型参考值，不是对任意载荷下机械安全的保证。超范围目标会沿当前姿态至目标的方向投影回可用区域，不保证到达原目标。

### 1.2 提前量与尾部覆盖

- 时间戳是姿态应到达的时刻，必须在该时刻之前送达。不能使用 `time.time()`，也不能直接使用从 0 开始的轨迹相对时间。
- UDP 在接收时丢弃 `t_s < now` 的样本，累计到 `dropped_samples`。接收时恰好等于 `now` 虽可通过过滤，也没有可靠的执行余量。
- 普通发送器可预留 0.2～0.4 s；当前 motion 每拍补发至 `now + 0.15 s`。跨机器的单调时钟没有天然共同原点，本接口默认同机使用。
- **尾部覆盖规则：**过滤过期样本后，取新报文中最早有效时间戳 `t0`，删除旧缓冲区中所有 `t >= t0` 的样本，再加入本报文。不是仅替换时间戳相同的点，也不是仅覆盖新报文自身覆盖的时间区间。
- 因此，新报文如要保留原来的远期尾部，必须把那部分一并重发。若本报文所有样本都过期，不修改旧轨迹尾部。
- 同一报文中的样本会按时间排序；相同时间戳保留该报文最后写入的姿态。

例如旧缓冲时间为 `10, 11, 12, 13`，新报文包含 `11.5, 11.6`，覆盖后为 `10, 11, 11.5, 11.6`，原来的 `12, 13` 也会删除。

### 1.3 插值、断流与恢复

实现：`motor/src/neck/neck_stream.cpp`。

- 正常跟随使用三次 Hermite 插值，结点速度共享；它保证位置、速度衔接，不普遍保证加速度连续。
- 样本耗尽后进入 `Decelerating`，按当前速度、加速度平滑减速，再进入 `Holding`。不是瞬间卸力，也不会自动回 RPY 零位。
- 减速时长按 `amax` 计算并限制在 0.060～0.150 s；默认 `amax=0` 时采用 0.060 s。
- 从等待、断流减速或保持状态恢复输入时，使用 min-jerk 权重衔接，默认过渡 0.250 s；设置加速度限制后过渡时长可在 0.250～0.600 s 内调整。
- `q` / `stopUdp()` 是主动停止接收并保持当前规划目标，和“样本自然耗尽后减速”不是同一个入口。

### 1.4 C++ `neck::Controller::push()`

公开头文件为 `motor/include/neck/controller.h`，CMake 链接目标为 `motor`，使用 C++17：

```cpp
struct neck::Sample { double t_s, roll, pitch, yaw; };
using neck::Samples = std::vector<neck::Sample>;
void neck::Controller::push(const neck::Samples& samples);
```

`push()` 异步提交绝对单调时钟样本，不需要先调用 `listenUdp()`，内部使用与 UDP 相同的尾部覆盖逻辑。当前实现在 `push()` 调用时捕获过滤用的 `now`，之后由控制线程处理，因此提前量也应覆盖排队时间。

**初始化前提：**当前 `Controller::start()` 本身不会调用 `startBus()`；独立应用需像 `motor/src/console/main.cpp` 一样先启动 EtherCAT 总线，再启动控制器。底层声明在 `motor/src/ethercat/transmit.h`，现有控制台已完成这一过程。同一套电机只使用一个控制器，不要再同时运行另一个 `master_stack_test`。

下面函数用于已有总线初始化和控制器所有权管理的 C++ 程序：

```cpp
#include <neck/controller.h>
#include <time.h>

void sendPose(neck::Controller& controller) {
    controller.start();
    controller.waitIdle();  // 等待启动结果，后台错误在这里抛出

    controller.moveTo(0, 0, 0, 3.0);
    controller.waitIdle();  // 若初始越界，先完成向可用零姿态的恢复

    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    const double t0 = ts.tv_sec + ts.tv_nsec * 1e-9 + 0.3;
    neck::Samples samples;
    for (int i = 0; i <= 30; ++i) {
        const double u = i / 30.0;
        const double w = u*u*u * (10 - 15*u + 6*u*u);
        samples.push_back({t0 + u, 0, 0, 3*w});
    }
    controller.push(samples);
    controller.waitIdle();  // 本段耗尽、减速并保持后返回
}
```

持续流式输入时，应周期性调用 `push()`，**不要逐帧调用 `waitIdle()`**；它等待的是控制状态空闲，不是单个样本已发送，也不保证物理反馈已精确到达目标。`play()` 的 `t_s` 则是相对调用时刻的秒数，不能与 `push()` 混用时间语义。

其他常用接口：`status()` 返回角度、RPY、裕度及 `usable`；`setRate()` 设置控制 Hz；`setLimits()` 设置各轴速度/加速度上限；`setParams(kp,kd,comp,lead,tf)` 设置控制参数；`stop()` 停止控制但不卸力；`release()` 发送仅阻尼命令。

## 2. motor 命令行

程序为 `motor/build/master_stack_test`。以下命令在程序的 `>` 提示符中输入，不是在 shell 执行。方括号表示可选项，不应原样输入。

### 2.1 颈部命令

| 命令 | 用法与行为 |
|---|---|
| `help` | 列出命令、单位及配置默认值 |
| `NeckStatus` | 查询实际电机角度、RPY、限位/裕度和 `usable`，不发起运动 |
| `NeckRpySet roll pitch yaw [time=1]` | 在指定时长内移到头部 RPY；如零位目标可用，可用于从当前越界姿态向内恢复 |
| `NeckMotorSet q1 q2 q3 [time=1]` | 指定三个输出轴绝对角度，换算为头部姿态后运动；不是原始驱动器位置模式 |
| `NeckSweep axis amp vel [cycles=1] [accel=100]` | `axis=roll/pitch/yaw`；先回零并保持 5 s，再到 `+amp`，执行指定轮数的 `−amp → +amp`，最后回零 |
| `NeckStepTest axis amp [times=1,0.6,0.4,0.3,0.25,0.2,0.15] [hold=1]` | 先回零；对每个时长 T 执行 `0 → amp → 保持 → 0 → 保持`，移动用 min-jerk；`times` 是逗号分隔的时长列表 |
| `NeckPlay` | 无参数时列出演示目录的所有 `.csv` 文件 |
| `NeckPlay name[.csv] [time=1]` | 回放指定名称的轨迹；`time` 仅控制接入首帧的过渡，不改变文件内时长；关闭 UDP 接收 |
| `NeckStream [port=9870]` | 开启本机 UDP 输入，不等待样本到达，控制台仍可接收命令 |
| `q` | 停止 UDP 接收并保持当前规划目标，**不是退出程序** |
| `NeckStop` | 停止颈部控制/轨迹和 UDP 接收，保留电机最后目标，不卸力 |
| `NeckRelease` | 停止控制并提交 `kp=0, kd=1, tor=0` 的仅阻尼命令；可能导致头部下落 |

运动命令均是**异步提交**，打印 `queued; console remains available` 不表示运动完成。`NeckStepTest` 也是异步的。查询可用 `NeckStatus`，需要停止用 `NeckStop`；不能照旧文档把命令返回当作到位确认。

### 2.2 参数与默认值

默认文件：`motor/config/runtime.json`。每条运动命令从该默认表重新取参数，不自动继承上一条命令临时覆盖的值；配置由进程缓存，修改文件后重启。

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `kp` | 150 | 混合控制位置增益 |
| `kd` | 2 | 混合控制速度增益 |
| `comp` | 0 | 位置/方向相关纹波补偿倍率；0 关闭，非零时读取 `motor/config/ripple_comp.json` |
| `lead` | 0 | 补偿查表角度的速度提前量，单位 ms |
| `tf` | 0 | 摩擦前馈幅值，Nm；前馈为 `tf * tanh(电机速度[°/s])` |
| `rate` | 500 | motor 规划/下发频率，Hz，不是 UDP 帧率 |
| `vmax` | 0 | 头部各轴速度限制，°/s；0 表示不限制 |
| `amax` | 0 | 头部各轴加速度限制，°/s²；0 表示不限制 |
| `time` | 1.0 | 定点运动时间，或 `NeckPlay` 首帧接入时间 |
| `port` | 9870 | `NeckStream` 的 UDP 端口 |
| `cycles` | 1 | `NeckSweep` 往返轮数 |
| `accel` | 100 | `NeckSweep` 轨迹加速度，°/s²，与执行时的 `amax` 不同 |
| `log` | 1 | session CSV 日志开关；在配置文件设置，不是有效的逐命令日志开关 |

`kp/kd/comp/lead/tf/rate/vmax/amax` 用于上述运动命令，放在必填位置参数之后，写成 `名字=值`，顺序不限。`times` 默认列表和 `hold=1` 在命令解析器中定义，供 `NeckStepTest` 使用；其初始回零时间取全局默认 `time`。`NeckStop`、`NeckRelease`、`NeckStatus`、`q` 在解析这些选项前就返回，不能靠给它们追加选项来更新控制参数。

示例：

```text
NeckRpySet 0 0 0 time=3
NeckSweep yaw 5 3 cycles=1 accel=20
NeckStepTest pitch 3 times=1,0.6,0.4 hold=1
NeckPlay 01_nod_yes time=1 kp=150 kd=2
NeckStream port=9870 rate=500
```

### 2.3 NeckPlay 目录与 CSV

当前 `motor/CMakeLists.txt` 中的 `NECK_TRAJECTORY_DIR` 为：

```text
/home/jhl/projects/Project-Neck-v2/motor/data/demos
```

`NeckPlay` 和 C++ `playFile(name, transition)` 只接受名称，不接受含父目录的路径；省略扩展名时追加 `.csv`。旧的 `motor/assets/trajectories/` 不是当前回放搜索目录。

```csv
t_s,roll_deg,pitch_deg,yaw_deg
0.000,0,0,0
1.000,0,3,0
2.000,0,0,0
```

- `t_s` 从 **0** 开始，严格递增，单位秒；角度为度，顺序是 RPY。
- 可有首行表头；数据必须是四列逗号分隔有限数值，不可追加额外列。
- 不要求文件自身为 500 Hz，motor 在控制周期内插值；稀疏结点不保证加速度连续。
- 若当前姿态不同于首帧，会先生成接入轨迹；配置了 `vmax/amax` 时，接入时长可能被延长。
- 结束后保持末帧，不自动卸力。
- 目前演示目录包括 `00_showreel`、`01_nod_yes`、`02_shake_no` 至 `11_speech_sway_test`；以无参数 `NeckPlay` 的实际列举为准。

默认日志为 `motor/data/sweep/session_rate{rate}_{timestamp}.csv` 和 `_fb.csv`，记录规划/实际编码命令及反馈。主日志中 `step_id=+n/-n` 标识 StepTest 第 n 个时长的去程/回程，其他段为 0。

### 2.4 原始电机命令

这些命令直接操作驱动器，不能代替安全的头部 RPY 接口。`MotorSpeedSet`、`MotorPositionSet`、`MotorStop` 会先停止颈部控制；原始运动仅警告头部越界，不提供颈部轨迹限位保护。

`SlaveId` 从 0 开始；当前配置一台从站。`PassAge` 是 PDO 槽位 1～6，CAN1 对应 1～3，CAN2 对应 4～6；`MotorId` 为驱动器 ID。

| 命令 | 参数与行为 |
|---|---|
| `MotorIdGet SlaveId` | 通过 CAN1 查询 ID |
| `MotorIdSet SlaveId MotorId NewMotorId` | 通过 CAN1 修改 ID |
| `MotorIdReset SlaveId` | 提交 ID 重置命令 |
| `MotorZeroSet SlaveId PassAge MotorId` | 设置驱动器零位，会改变标定基准；不是头部回中 |
| `MotorAngelGet SlaveId PassAge MotorId` | 查询输出轴角度，°；命令拼写确实为 `Angel` |
| `MotorStop SlaveId PassAge MotorId` | 提交制动命令；实现为 `cur_tor=10, ctrl_status=2, ack_status=0` |
| `MotorSpeedSet SlaveId PassAge MotorId [Speed [Current [AckStatus]]]` | 默认 `0,500,2`；Speed 是协议浮点 rpm |
| `MotorPositionSet SlaveId PassAge MotorId [Position [Speed [Current [AckStatus]]]]` | 默认 `0,50,500,2`；Position 为 °，Speed 为 0.1 rpm 的原始整数 |

两个原始伺服命令的 `Current` 均按协议原始 0.1 A 整数传入，`AckStatus` 为 0～3；不要把这些值当成 `Neck*` 命令的 °/s 或物理安培直接套用。额定值及制动参数仍须遵守电机手册。ID/零位命令应在单独维护时使用，不要在运行颈部轨迹时修改标定。

## 3. bridge 回调与轮次时间轴

实现：`bridge/__init__.py`。入口：

```python
from bridge import Bridge

# 同步回调；不要在这里阻塞网络接收。
def callback(event, *args):
    ...

# 在 asyncio 事件循环中运行：
# await Bridge(callback).serve(host="0.0.0.0", port=8766)
```

只接收一个在线 TCP 客户端；第二个连接会被关闭。回调不含到达时刻，player 自行用 `time.monotonic()` 记录。

| 回调形式 | 字段 |
|---|---|
| `callback("turn_start", turn, rate)` | 正整数轮次 ID；采样率 Hz；该轮第一个有效带轮次音频包到达时触发 |
| `callback("audio", turn, pcm_bytes, tau)` | 单声道、小端、有符号 PCM16；`tau` 是本块音频起点，秒 |
| `callback("text", turn, text, tau)` | 文本及锚定的音频起点，秒；不是逐字时间戳 |
| `callback("turn_end", turn, tau_end)` | 接收侧结束；`tau_end` 是已解码帧的音频末尾，不表示扬声器已播完 |
| `callback("abort", turn)` | 取消该轮；无额外时间或原因字段 |

支持 Opus 采样率 `8000/12000/16000/24000/48000` Hz、单声道；帧时长必须 `0 < duration_ms <= 120`，并要求每次解码样本数与声明时长吻合。同一轮格式改变会 abort。

### 3.1 τ、缺包与迟到包

设首个被接受的音频包序号为 `sequence0`，每包时长为 `F` 毫秒：

```text
index = (sequence - sequence0) mod 2^32
tau = index * F / 1000
```

τ 来自包序号，不是网络到达间隔，也不是接收机的墙钟。

- 正向跳号时，对缺失的每一帧调用 Opus PLC（空包丢包隐藏），依次发出对应 τ 的 `audio`；并非简单插入全零音频。
- 重复或落后于 `next_index` 的包丢弃；实现处理 uint32 回绕及反向的大距离差，不等待乱序重排。
- `robot_text` 先暂存；在下一个有效的**实际音频包**之前发出 `text`，其 τ 锚定到该包，不锚定到补出的 PLC 帧。
- 空白文本或去除左侧空白后以 `% ` 开头的文本被忽略；结束时尚未锚定到音频的文本被丢弃。

### 3.2 turn_end 的触发

1. 收到 `robot_audio_end` 后保存 `last_audio_sequence`；当它等于当前轮已处理的最后一个包序号时，立即结束。标记早到时等待匹配的音频包，标记晚到时可立即结束。
2. 收到 `robot_playback_end` 后开启 **0.35 s** 尾音等待；期间每收到有效音频，重新计时，超时触发结束。
3. 新轮音频到来时，若旧轮已有上述 `robot_playback_end` 等待计时，则把旧轮正常结束；否则 abort 旧轮。

`robot_audio_end` 本身不为尚未收到的尾包主动补帧；如果一直缺少最后一个包且没有后续包推动 PLC，不能仅靠该标记匹配完成，需要后续音频或 `robot_playback_end` 超时路径。

`robot_playback_abort` 立即触发 abort；TCP 断开也会 abort 当前轮。已关闭轮次的后续消息被忽略；断线清理后允许在新连接中重新使用轮次 ID。

独立 CLI：`python3 -m bridge [--host 0.0.0.0] [--port 8766] [--output bridge_wavs]`，记录每轮 WAV 和事件，不播放音频、不启动 motion。

## 4. player 播放时序

实现：`speech_motion/player.py`。运行 `python3 -m speech_motion.player` 同时启动 bridge 和 motion。CLI 仅有 `--host`、`--port`；动作参数从同目录 `config.json` 读取。

- bridge 回调 `Player.on_event()` 只记录单调时刻并入队；工作线程处理事件，sounddevice 的独立音频回调线程填充输出。
- 采样率取自 `turn_start`，PCM16 转 float32；使用 `sounddevice.OutputStream(channels=1, latency="low")`，保持 PortAudio 输出后端。
- 令 `t_first` 为 player 收到本轮首个 `audio` 回调时记录的单调时刻：

```text
wall_base = t_first + D
每块计划播放起点 = wall_base + tau
```

`D=0.5 s` 是固定预留时间，不是每包分别延迟 0.5 s；后续包沿用同一基准。首包通常 τ=0。

播放器读取流的 `time` 与 `time.monotonic()` 建立时钟偏移，在音频回调中将 `outputBufferDacTime` 换算到单调时钟，再按样本偏移把声音放到计划的 DAC 输出时刻，而不是把“进入回调的时刻”当成实际播放时刻。

**现有硬件实测：声卡输出延迟约 11 ms，已在 DAC 时序调度中补偿；实际出声时刻与计划时刻相差约 0～2 ms。** 这是当前硬件的实测结果，不是对任意声卡、负载或欠载场景的保证。日志中的 `actual_start` 是由 DAC 时间换算的首样本输出时刻，并非播放器内置麦克风测量。

- 开始日志包含首包到达、计划/实际起播、`outputBufferDacTime`、`output_latency = outputBufferDacTime - currentTime`、流的 `latency` 和起播延迟。
- 播空时填零，每次进入欠载状态打印一行并计数。不会重排 `wall_base`；晚到音频只能尽快播出，不能保证仍满足原计划时刻。
- `turn_end` 等待已排队音频及 DAC 输出排空；结束日志给出已收音频总时长、欠载次数、结束时刻。
- `abort` 调用流的 `abort()` 并清空本轮；新 `turn_start` 也会停止旧轮。停止日志时刻取 `abort()` 返回后。
- player 通知 motion `(turn, wall_base)`，并转发 `turn_start/audio/text/turn_end/abort`。player 本身不保存 WAV。

## 5. motion 配置、姿态切换与手势

实现：`speech_motion/motion.py`，由 player 创建 `Motion(config["motion"])`；没有单独的 motion 命令行入口。输出为基础姿态加手势，roll 恒为 0。

### 5.1 config.json 全部配置项

路径：`speech_motion/config.json`。下面为当前默认值；修改后重新启动 player。角度为 °，时间为 s。

| 配置路径 | 默认值 | 含义 |
|---|---|---|
| `D` | `0.5` | player 首包固定预留时间 |
| `motion.transition.base_time` | `0.5` | 普通姿态切换的固定时长项 |
| `motion.transition.degrees_per_second` | `15.0` | 位移换算时长的分母；不是瞬时速度硬上限 |
| `motion.transition.min_yaw_change` | `3.0` | 新 yaw 目标与上一 yaw 目标的最小差值 |
| `motion.transition.return_time` | `0.5` | 正常结束/abort 回中时长 |
| `motion.idle.interval` | `[2.5, 5.0]` | 待机切换起点间隔的随机范围 |
| `motion.idle.yaw` | `[-6.0, 6.0]` | 待机 yaw 目标的均匀随机范围 |
| `motion.idle.pitch` | `[-2.0, 2.0]` | 待机 pitch 目标的均匀随机范围 |
| `motion.speaking.min_interval` | `1.2` | 同一轮实际说话切换之间的最小起点间隔 |
| `motion.speaking.yaw` | `[-10.0, 10.0]` | 说话 yaw 随机目标范围 |
| `motion.speaking.pitch` | `[-3.0, 4.0]` | 说话 pitch 随机目标范围 |
| `motion.gestures.seconds_per_char` | `0.2` | 每字时长估计；用于关键词和逗号定位 |
| `motion.gestures.lead_time` | `0.2` | 手势相对关键词的提前量 |
| `motion.gestures.min_interval` | `1.5` | 同一轮手势起点的最小间隔 |
| `motion.gestures.nod.keywords` | `["是的","是啊","对的","没错","当然","好的"]` | 点头关键词，已不含“可以” |
| `motion.gestures.nod.prefixes` | `["是","对"]` | 句首点头词；忽略开头空白和标点 |
| `motion.gestures.nod.amplitudes` | `[0,5,0,3.5,0]` | 点头各结点的 pitch 偏移 |
| `motion.gestures.nod.segment_duration` | `0.3` | 点头每段时长，共 1.2 s |
| `motion.gestures.shake.keywords` | `["不是","不对","不行","不可以"]` | 摇头关键词，优先于点头 |
| `motion.gestures.shake.amplitudes` | `[0,8,-6,4,0]` | 摇头各结点的 yaw 偏移 |
| `motion.gestures.shake.segment_duration` | `0.5` | 摇头每段时长，共 2.0 s |

`motion.gestures.shake.prefixes` 当前未配置，代码按空表处理。原有正弦幅度/频率、音量增益、慢摆淡入淡出配置均已删除。

### 5.2 基础姿态切换

每段使用：

```text
s = clamp((t - t_start) / T, 0, 1)
w(s) = 10s³ - 15s⁴ + 6s⁵
姿态 = 起始姿态 + (目标姿态 - 起始姿态) × w(s)
T = 0.5 + max(|Δpitch|, |Δyaw|) / 15
```

这里的当前姿态是 motion 的**规划姿态**，不是 motor 反馈；达到目标后保持到下一次切换。每次运行创建新的 `random.Random()`，不设置固定种子。

- **待机：**启动从 `(pitch,yaw)=(0,0)` 开始，2.5～5 s 后第一次切换；此后按随机间隔切换。新 yaw 与当前目标相差至少 3°。
- **说话：**每条 `text` 事件视为一句，在句首 τ 及 `，`、`,` 处安排切换；逗号 τ 为 `句首τ + 逗号前非标点、非空白字符数 × 0.2`。不会自行把一条文本按句号拆成多句。
- **间隔：**1.2 s 仅在同一轮说话切换之间比较；新轮重置计时，不受待机或上一轮影响。τ=0 句首不会因为此前待机切换而跳过。
- **手势避让：**在点头/摇头进行中不启动普通姿态切换，顺延到手势结束；重叠手势会继续顺延。初始句首在同刻的延后队列中优先；随后仍按本轮间隔规则处理其他触发。
- **新轮接入：**不执行待机回中，保留当前基础过渡；真正开始说话切换时从该时刻的基础姿态直接起步。τ=0 如有手势，先完成手势再切换。
- **结束/abort：**正常结束在 `wall_base + tau_end - 0.003` 开始回中；abort 按处理时的单调时刻立即规划回中。回中优先于手势，从当前叠加姿态在 0.5 s 内到 `(0,0)`，取消待执行切换；回中完成再等待 2.5～5 s，进入下一次待机切换。正常结束以实际收到事件、已生成的时间轴为前提，不能撤回已经执行的过去动作。

### 5.3 点头与摇头

- 去掉句尾空白后，以 `？` 或 `?` 结尾的文本不触发任何手势；**仍会触发句首/逗号基础姿态切换**。
- 先查摇头词：“不是 / 不对 / 不行 / 不可以”。只要命中，就不再选择点头。
- 否则查点头关键词，或首个非空白、非标点字符处的“是 / 对”前缀。
- 同类多个命中选择位置最靠前的；同位置选择较长关键词。每条文本最多生成一个手势，起点与本轮上个手势不足 1.5 s 时不触发。
- 字数按 Unicode 字符计数，排除空白及 Unicode 标点，并非中文分词或真实音素对齐。

```text
tau_k = 句首tau + 关键词之前的有效字符数 × 0.2
手势起点 = max(0, tau_k - 0.2)
```

点头沿 pitch 走 `0 → 5 → 0 → 3.5 → 0`，每段 0.3 s，全部采用半余弦插值；摇头沿 yaw 走 `0 → 8 → −6 → 4 → 0`，每段 0.5 s，首末段 min-jerk、中间两段半余弦。它们直接叠加在基础姿态上；回中入口会接管叠加后的当前姿态。

触发日志包括 turn、原文本、关键词、类型、`tau_k`。姿态切换日志包括 turn（待机为 `idle`）、`reason=idle/句首/逗号/回中`、单调时刻、可用时的 τ、目标 yaw/pitch、移动时长；间隔不足打印 `skipped` 和本轮说话切换间隔，此时未抽取随机目标，目标及时间字段为 `None`。

### 5.4 UDP 时序与新增动作位置

- motion 从启动起持续以 30 Hz 发送，保持段和待机段也不断流；每拍发送到 `now + 0.15 s`。
- 说话动作对应 `t_mono = wall_base + tau - 0.003`。这 3 ms 是当前硬编码的动作时间偏移，**不是声卡约 11 ms 的输出延迟补偿**。
- 说话切换触发与手势叠加受已收到音频末尾约束；已开始的基础姿态过渡按绝对时间继续执行。
- abort、轮次变更等会回退尚未执行尾部的发送游标，利用 motor 的尾部覆盖规则重发未来样本。
- 目标固定为 `127.0.0.1:9870`；30 Hz、0.15 s、0.003 s 当前写在代码中，不属于 JSON 配置。

新增动作时主要修改：

1. `speech_motion/config.json` 的 `motion.gestures`：关键词、可选句首词、结点幅度、段时长。
2. `Motion._text()`：当前只依次枚举 `shake`、`nod`，新动作需加入判定顺序、优先级及选择规则；仅新增 JSON 项不会自动启用。
3. `Motion._gesture_pose()`：为新动作选择轴和曲线；当前只有 nod→pitch、其余已支持的 shake→yaw 分支，不能直接套成第三种动作。
4. `Motion._after_gestures()`：当前按结点数和统一段时长推导结束时刻；若新动作时长模型不同，需同步修改，保证姿态切换正确避让。
5. 若新增 roll 或其他维度，需同步调整 `_pose()`、`_send()` 及安全范围；当前发送端把 roll 写死为 0。新增基础姿态触发则修改 `_text()` 的切换队列或 `_send()` 调度。

## 6. 板子到 PC 的 TCP 消息

本节描述 **当前 bridge 接收端实际支持的协议**；本仓库不包含板卡发端源码，不定义云端认证、Wi-Fi 配置或未使用的额外消息字段。默认监听 `0.0.0.0:8766`，板子连接 PC 的实际 IP。

TCP 是字节流，同一连接中混合以下两类帧。接收器支持拆包/粘包，不依赖一次 `send()` 对应一次读取。

### 6.1 单行 JSON

每条从 `{` 开始，以换行结束；不要在 `{` 前加空格。裸 CR/LF 会被跳过。

| `type` | 字段 | 含义 |
|---|---|---|
| `robot_text` | `turn_id`、`text` | 机器人文本；等待下一个实际音频包锚定 τ |
| `robot_audio_end` | `turn_id`、`last_audio_sequence` | 该轮最后一个音频包的 uint32 序号，用于准确结束 |
| `robot_playback_end` | `turn_id` | 尾音 0.35 s 超时结束入口，不直接代表 PC 已播完 |
| `robot_playback_abort` | `turn_id` | 取消指定轮次 |

`turn_id` 必须为正整数，不能是布尔值或已关闭的轮次。`last_audio_sequence` 必须是 `0..4294967295` 的整数，是包序号而不是包数、时间戳或相对索引。

```json
{"type":"robot_text","turn_id":1,"text":"好的，我们开始。"}
{"type":"robot_audio_end","turn_id":1,"last_audio_sequence":42}
{"type":"robot_playback_end","turn_id":1}
```

abort 示例：

```json
{"type":"robot_playback_abort","turn_id":1}
```

其他 JSON 类型不触发业务事件，未使用字段不会被传到回调。`turn_start`、`audio`、`turn_end` 是 bridge 生成的回调事件名，不能当成同名 JSON 类型发送来驱动播放器。

### 6.2 二进制音频帧

先发送 1 字节类型，再发送固定长度头和 `length` 字节 payload。所有多字节整数均为**网络字节序（大端）**，无结构体填充；`length` 仅计算 payload，当前要求 `1..8192` 字节。

| 类型字节 | Python struct 头格式 | 头字段，按顺序 | 当前处理 |
|---|---|---|---|
| `0x01` | `!II` | `sequence:u32, length:u32` | 麦克风帧，消费 payload 但不解码、不播放 |
| `0x02` | `!IIIHB` | `sequence:u32, length:u32, sample_rate:u32, frame_duration_ms:u16, channels:u8` | 无轮次的旧版机器人音频，消费但不播放 |
| `0x03` | `!IIIIHB` | `sequence:u32, length:u32, turn_id:u32, sample_rate:u32, frame_duration_ms:u16, channels:u8` | 带轮次 Opus 音频，解码并生成回调 |

类型 3 示例封包方式：

```python
frame = b"\x03" + struct.pack(
    "!IIIIHB", sequence, len(opus_packet), turn_id,
    sample_rate, frame_duration_ms, 1
) + opus_packet
```

首个有效类型 3 音频包隐式开始该轮；不要求单独的开始 JSON。音频帧后不需要换行，payload 内可以有任意字节。未知类型字节、非法长度或解码错误会使当前连接退出清理，并 abort 活动轮次。

## 7. 已知限制与操作注意

1. **说话过程中不能通过当前整条语音链路实现语音打断。** bridge/player/motion 已能处理收到的 abort，但本仓库没有完整的板卡/上游打断实现，不能把接口能力当成端到端功能已经完成。
2. **低速 roll 存在黏滑。** 历史慢摆不再使用；当前基础姿态切换及点头/摇头都输出 roll=0。motor 的 roll 轴及含 roll 的离线演示仍可使用，不代表黏滑问题已解决。
3. **EtherCAT 网卡 `enp4s0` 硬编码**于 `motor/src/ethercat/transmit.cpp`；Python 的 `--host` 不能改变它。
4. **断电后头部会落出可用范围。** 上电确认总线与反馈后，先执行 `NeckRpySet 0 0 0 time=3`，等运动完成并检查 `NeckStatus`，再开启 `NeckStream`；不要重设驱动器零位来掩盖越界。
5. **演示目录是编译时绝对路径。** 迁移仓库时须处理 `NECK_TRAJECTORY_DIR`，不是改变 shell 当前目录即可解决。
6. **文本时间是估计，不是强制对齐。** 多条文本可能锚定同一个音频包；每字 0.2 s 不能保证关键词与发声完全重合。
7. **网络/输出不是硬实时保证。** 欠载、线程调度、晚到事件不能补救过去的时间戳；UDP 没有重传，且没有从 motor 到 motion 的姿态反馈闭环。
8. **基础姿态和手势会叠加。** JSON 中的基础随机范围不是最终叠加姿态的上限，最终仍受 motor 机构限位。改变参数前应检查组合工作空间。
9. **忽略规则不清理历史。** `.gitignore` 不会自动取消已跟踪文件或删除 Git 历史中的录音、日志或敏感数据。录音/文本日志也可能包含隐私。

## 8. 地址与敏感配置文件排查

当前工作树未发现项目自身的 Wi-Fi 密码或设备密钥配置文件；板卡固件不在本仓库，不能据此推断板卡配置安全。以下是含运行地址、端口或相关绑定逻辑、发布前应检查的文件，仅列路径和用途，不摘录任何密钥：

| 文件 | 可能需要检查的内容 |
|---|---|
| `bridge/__init__.py` | TCP 默认监听地址、端口 |
| `bridge/__main__.py` | 独立 bridge 的 host/port 参数 |
| `speech_motion/player.py` | 集成服务的 host/port 参数 |
| `speech_motion/motion.py` | UDP 目的地址和端口 |
| `motor/src/neck/controller.cpp` | 本机 UDP 绑定实现 |
| `motor/src/console/neck_commands.cpp` | 端口参数及地址提示 |
| `motor/config/runtime.json` | 默认 UDP 端口，目前不含密码/密钥 |
| `motor/tools/stream_sender_sim.py` | 模拟器目标 host/port |

`motor/INTERFACE.md`、`motor/docs/neck_progress_2026-10-14.md`、`motor/docs/motor_cleanup_plan.md` 等旧文档也含地址示例，复用前应核实；本文件和 README 中的本机/监听地址不是访问凭据。

第三方 `motor/thirdparty/soem/soem/ethercatfoe.c` 中的 `Password/password` 是 FoE 协议字段和函数参数，不是发现了硬编码设备密码。本次排查不覆盖 Git 历史、二进制及压缩包内容，也没有修改上述文件。
