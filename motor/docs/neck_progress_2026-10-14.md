# Project-Neck 电机执行层进展（2026-10-14）

本文记录截至 2026-10-14 的阶段性进展。实验数字沿用已有记录，命令、参数和工具行为以当前仓库实现为准；下列测试命令仅供后续执行，本次文档整理未运行电机。

## 1. 目标

给电机组一串 rpy（可流式输入），三电机在各种速度下平滑执行；音频由小智流式输出到 Ubuntu，加延迟后由 Ubuntu 控制播放，头动与音频同步。

## 2. 系统与配置

- 3 个 ENCOS EC-A2806-P2-36（减速比 36，推断为 7 对极），EtherCAT 转 CAN，全部在 CAN1；已装平台，未装头。
- 标定与限位：`config/neck_calibration.json`。电机 1、2 为并联机构的两条曲柄支链，电机 3 为串联 yaw 轴。
- 补偿表：`config/ripple_comp.json`；未筛选的原始表另存为 `config/ripple_comp_raw.json`。每个电机正反向各一张，周期 10°、步长 0.02°、每张 500 格。
- 当前默认控制工作配置：力位混合模式，kp150、kd2，500 Hz，补偿 Comp=1。
- **工作配置与命令行默认值不同**：当前代码中 `NeckSweepMit`、`NeckRpyStream` 的 `Comp` 省略值仍为 0，使用上述工作配置时必须显式传入 1；`Lead` 默认 0 ms。`NeckRpyPlay` 当前不提供 `Comp` 或 `Lead` 参数，回放时前馈力矩为 0，不启用查表补偿。
- 当前 `config/ripple_comp.json` 已筛选为仅保留每 10° 的 7、14、28 次傅里叶分量，其余分量（含直流）置零。原建表 CSV 已不在当前数据目录，本次筛选基于此前保存的原始表完成，未混入补偿测试数据。

## 3. 已有命令与工具

### 3.1 `master_stack_test` 中的命令

以下命令在 `master_stack_test` 的交互提示符中输入，不是 shell 命令。参数格式取自 `command.cpp` 和 `neck_sweep.cpp`。

```text
NeckSweep <Axis(roll/pitch/yaw)> <Amp(deg)> <Vel(deg/s)> <Cycles> [Accel=100(deg/s^2)] [Rate=50(Hz)]

NeckSweepMit <Axis(roll/pitch/yaw)> <Amp(deg)> <Vel(deg/s)> <Cycles> <Kp> <Kd> [Accel=100(deg/s^2)] [Rate=500(Hz)] [Tf=0(Nm)] [Comp=0] [Lead=0(ms)]

NeckRpyPlay <File> [Kp=150] [Kd=2] [Rate=500(Hz)]

NeckRpyStream [Port=9870] [Kp=150] [Kd=2] [Rate=500(Hz)] [Comp=0] [Lead=0(ms)]
```

| 命令 | 用途与当前行为 |
| --- | --- |
| `NeckSweep` | 官方位置模式单轴扫动，用于位置模式对照、速度和跟踪情况检查。通过运动学生成三电机目标，完整预检后执行。 |
| `NeckSweepMit` | 力位混合模式单轴扫动，用于 PD 参数、摩擦前馈、力矩波动补偿及 Lead 对比。`Kp`、`Kd` 必填；`Tf` 为摩擦前馈幅值，按 `Tf × tanh(指令速度)` 叠加。 |
| `NeckRpyPlay` | 整段 rpy 数组回放。读取 `trajectories/` 下指定文件名对应的 CSV，列为 `t_s,roll_deg,pitch_deg,yaw_deg`；使用 Catmull-Rom 插值和运动学预检，再以混合模式按单调时钟回放。先移动到轨迹起点并保持 5 s，结束后在终点保持 5 s。 |
| `NeckRpyStream` | 纯 C++ 流式执行器，监听本机 `127.0.0.1` 的 UDP 端口。接收带绝对单调时钟时间戳的 rpy 点，完成插值、运动学、限位与断流恢复；启动时先归零，按 `q` 退出。退出后最后目标仍保持有效，不等同于电机断使能。 |

扫动命令中，`Amp` 和 `Vel` 是所选头部姿态轴的幅度及速度，不是每个电机各自的速度。`+Amp → -Amp → +Amp` 为一个 cycle；扫动前归零并保持 5 s，完成后回零并保持 5 s。

**`NeckSweepMit` 的基本参数顺序为：**

```text
<Axis> <Amp> <Vel> <Cycles> <Kp> <Kd> [Accel] [Rate] [Tf] [Comp]
```

当前实现又在 `Comp` 后追加了可选的 `[Lead]`。**写带 Rate、Tf、Comp 或 Lead 的命令时，不能漏掉 Accel**：这些是位置参数，不能跳过中间参数。只有不提供后续可选参数时，才可以整体省略并使用默认值。

例如，完整写出当前工作配置、关闭相位提前：

```text
NeckSweepMit yaw 20 5 1 150 2 100 500 0 1 0
NeckRpyStream 9870 150 2 500 1 0
```

`Lead` 只改变补偿查表角度，不改变位置或速度指令本身：

```text
查表角度（deg） = 指令角度（deg） + 指令速度（deg/s）× Lead（ms）/ 1000
```

查表按 10° 周期循环、格间线性插值，正反向表仍按指令速度平滑混合。运行输出和日志文件名记录 `Comp`、`Lead`。主 CSV 和同名 `_fb.csv` 保存到 `data/sweep/`，由后台线程异步写盘。

### 3.2 分析、建表与音频工具

| 工具 | 用途与当前实现 |
| --- | --- |
| `scripts/analyze_run.py` | 离线分析一次运行的主 CSV，自动读取同名 `_fb.csv`。输出控制周期及超时、实际/计划时长、运动学重建的头部跟踪误差与互相关滞后、卡顿区间、电机电流/温度/错误码；流式日志还输出缓冲余量、状态区间及切换前后速度/加速度峰值。速度和加速度的相关分析采用 20 ms 平滑。它不是此前补偿专项频谱对比的完整自动化脚本。 |
| `scripts/build_ripple_table.py` | 从一份或多份 `NeckSweepMit` 的 `_fb.csv` 提取有效匀速数据，去除局部电流均值，按反馈转角建立各电机正反向补偿表，并输出原始峰峰值和前后半相关系数。也可单独输入一份原始表 JSON。`--harmonics-only` 仅保留每 10° 的 7、14、28 次傅里叶分量，另存原始表为 `config/ripple_comp_raw.json`，结果写入 `config/ripple_comp.json`。 |
| `neck_audio_stream.py` | 用 WAV 模拟 60 ms 分块到达的音频流，生成 30 Hz rpy，经 UDP 发送给 `NeckRpyStream`，同时由 Ubuntu 播放音频。使用声卡 DAC 时间映射到 `CLOCK_MONOTONIC` 对齐播放，日志输出到 stdout。支持轨迹回放生成器 `--generator replay --rpy-csv ...`，以及按 `--clicks` 指定起始时刻的点头生成器 `--generator click`。`--port` 必填，`--delay` 默认 0.3 s，`--compensation-ms` 默认 8 ms。 |
| `stream_sender_sim.py` | 模拟头动生成器的分块输出、计算耗时、随机抖动、断流和恢复后的积压补发；通过 UDP 向 `NeckRpyStream` 发送轨迹，不实际播放音频。默认端口 9870、输出 30 Hz；支持 `--stall TAU DUR`、`--repeat`、`--gap` 等。输入文件按传入路径打开，不自动到 `trajectories/` 下查找。 |
| `record_clicks.py` | 计划用于 click 音画同步实验的录音/记录及声音与点头时间差测量。**当前仓库未找到该脚本**，具体录制方式和命令参数待提供脚本后核对，本文不假定其已实现。 |

流式 UDP 每个样本一行：

```text
t_mono_s,roll_deg,pitch_deg,yaw_deg
```

时间戳表示姿态应到达的绝对 `CLOCK_MONOTONIC` 时刻，不是 Unix 时间。音频工具的 `--compensation-ms` 提前的是头动执行时间戳；电机命令的 `Lead` 提前的是力矩补偿查表相位，两者不可混为一谈。

当前音频工具是 WAV 流式到达模拟与本地播放链路，不代表已完成小智真实音频流的接入。`replay_bg_34s.wav`、`click_test_8s.wav` 当前也未在仓库中找到，后续实验需另行准备。

## 4. 关键结论（按时间顺序）

1. **官方位置模式**：通信正常，但运动走走停停、静止来回摆动，滞后约 55 ms；提高下发频率无效。

2. **根因**：电机存在与转角锁定的力矩波动，输出端每 0.714°（转子每圈 14 次，另有 7 次）出现一次，三个电机都有，速度越低越明显；关节内部摩擦约 0.63 A。

3. **混合模式 kp150 kd2**：滞后降到 4～11 ms，位置偏差波动 0.011～0.030°（接近编码器分辨率 0.016°），静止不再摆动。驱动器为纯 PD：反馈电流 ≈ 2.19 × 控制器输出力矩。

4. **rpy 数组回放**：三轴组合、说话场景动作（最高约 54°/s）误差 RMS 0.03～0.08°，最大约 0.3°；30 Hz 输入经 Catmull-Rom 插值后与 100 Hz 基本一致。

5. **时序修复**：控制循环每 5 秒卡顿 15～40 ms 的原因是主线程同步写 CSV，已改为异步；回放轨迹改为按单调时钟推进，回放时长误差从 3.2% 降到 0。当前实现中，`NeckRpyPlay` 按实际单调时钟经过时间查询轨迹，`NeckRpyStream` 按单调时钟采样；`NeckSweep`、`NeckSweepMit` 的计划时间仍为 `n × dt`，不能把回放时长修复结论直接套用于扫动。各控制循环使用绝对单调时钟周期调度。

6. **流式执行 NeckRpyStream（纯 C++）**：正常流式与整段回放精度一致；断流时外推减速 → 保持 → 过渡 → 跟随，衔接平滑；过渡已改为实时轨迹融合、时长自适应。

7. **力矩波动补偿**：按转子一圈（10°）建表，正反向各一张。5°/s 时主峰降 40～45%、速度波动降 20～30%；20°/s 小幅改善；50°/s 基本无效。重启后相位只偏 0.04°，固定表可跨上电使用。

以上为既有实验结论，平台尚未装头。第 7 项的效果对应原始补偿表阶段；当前代码已经加入 `Lead`，配置也已切换为只保留 7/14/28 次分量的表，但新筛选表与非零 Lead 的组合仍待实测，不能直接沿用原表的改善百分比。跨上电结论基于已有重启对比，装头后的适用性仍需回归验证。

## 5. 未解决问题

- 高速（约 50°/s）运动时有震动，补偿对此改善有限；尚未用加速度计确认是电机还是机构共振。
- 补偿对 2.8 次/° 成分无改善，部分通道高频略增。
- 等待厂家回复：协议文档、极对数、7/14 次电频率波动的校准方法、位置模式电流阈值含义。

## 6. 下一步测试清单

以下均为待执行项目，不表示已经完成。shell 示例以 `motor/` 为工作目录；交互控制命令需在 `master_stack_test` 内输入。

1. **流式最坏情况**：`NeckRpyStream` 配合 `stream_sender_sim.py 02_speech_gestures_100hz.csv --stall 7.3 0.5`，在快速摇头途中断流，记录观感以及恢复是否有突跳。

   接收端使用当前工作配置：

   ```text
   NeckRpyStream 9870 150 2 500 1 0
   ```

   发送端使用仓库内实际轨迹路径：

   ```bash
   python3 stream_sender_sim.py trajectories/02_speech_gestures_100hz.csv --stall 7.3 0.5 --port 9870
   ```

2. **音频链路回放**：`neck_audio_stream.py` + `replay_bg_34s.wav` + 03 轨迹，端口统一为 9870。接收端沿用上述 `NeckRpyStream` 配置；准备 WAV 后运行发送与播放工具：

   ```bash
   python3 neck_audio_stream.py replay_bg_34s.wav --generator replay --rpy-csv trajectories/03_speech_gestures_30hz.csv --port 9870
   ```

   记录播放延迟、音频开始时刻、生成/发送日志和电机主 CSV、反馈 CSV；确认声音播放与头动使用同一时间基准。

3. **音画同步**：`click_test_8s.wav` + `record_clicks.py`，测声音与点头的时间差。先准备 WAV 和当前仓库缺失的录制脚本；若使用 `neck_audio_stream.py` 的 click 生成器，应按 WAV 中实际 click 时刻设置 `--clicks`，端口为 9870。`--clicks` 默认是空列表，不指定时不会生成点头事件。录制脚本参数待核对后再填写，不将预设的滞后补偿值当作测量结果。

4. **可选：补偿相位提前与频率筛选复测**。补偿加相位提前（Lead）和只保留 7/14/28 次成分后，再测 yaw 20°/s、50°/s。两项代码已经实现，当前待做的是实测对比；固定其他参数，比较 Lead=0 与非零 Lead，并记录使用原始表还是筛选表。写 `NeckSweepMit` 命令时保留完整的 `Accel → Rate → Tf → Comp → Lead` 参数顺序。

5. **可选：加速度计测量**。测量高速时的平台振动，辅助区分电机激励与机构共振。

6. **装头后回归**。用几组 `NeckSweepMit` 和一次流式回放做回归测试，重新检查平滑程度、误差、电流、补偿效果及断流恢复；未装头的结果不直接视为装头后的验收结果。
