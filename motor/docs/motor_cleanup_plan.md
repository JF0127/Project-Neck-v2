# motor/ 代码盘点与可提交版本整理方案

## 文档范围

本文记录对 `motor/` 的静态盘点结果与后续整理方案。所有路径均相对 `motor/`。

- 盘点依据：实际文件、CMake 构建入口、代码引用关系和现有文档。
- “当前使用”指代码引用、构建引用或文档提供的人工入口，不把“存在文件”自动等同于“最近运行过”。
- 盘点阶段没有修改代码、重新构建或运行电机/音频程序。
- 本次仅新增本文档；下文的移动、删除、改造和验收均为待执行事项。

## 一、盘点结论与需要先处理的依赖

盘点时（新增本文档之前），目录包含：

- **49 个项目文件**：源代码、脚本、配置、文档、素材，以及根目录的日志和杂项。
- `thirdparty/` 下 **126 个文件**，不含嵌套 `.git`。
- `data/sweep/` 下 **56 个 CSV，即 28 组主日志/反馈日志**，约 **560 MiB**。
- `build/`、`__pycache__/` 等生成物。

### 1.1 neck_target.py 目前仍被使用

当前调用链为：

```text
NeckMotorSet / NeckRpySet
  → neck_commands.cpp::neckPython()
  → neck_target.py

NeckSweepMit / NeckRpyPlay
  → neck_sweep.cpp
  → neckPython()
  → neck_target.py
  → neck_sweep.py
```

可以删除旧入口，但必须先迁移仍使用的单点解析、轨迹分发功能，不能直接删文件。

### 1.2 不能整体删除 neck_sweep.cpp / neck_sweep.py

- `neck_sweep.cpp` 同时实现 `NeckSweep`、`NeckSweepMit`、`NeckRpyPlay`、`NeckRpyStream` 的命令执行，以及补偿、反馈和日志。
- `neck_sweep.py` 同时承担 MIT 扫动和文件回放的离线规划。
- 应删除的是**位置模式分支**，不是这两个文件的全部功能。

### 1.3 空文件与不存在的早期文件

- 根目录 `NeckRpyPlay` 是零字节空文件，不是命令实现，也不是可执行程序，可以删除。
- 没有找到 `motor_test.cpp`、`run.sh` 或独立音频素材生成脚本。
- `transmit.cpp` 中仍有提示用户运行 `run.sh` 的过时文本。
- `audio/generators/` 应通过拆分现有 `neck_audio_stream.py` 中的类形成，不能当作已有目录。

### 1.4 Git 与第三方依赖现状

当前 `motor/` 及父目录没有 Git 仓库。`thirdparty/cpp-readline/` 自带 Git 仓库，且三个 CMake 文件有本地修改。提交前需要明确主仓库边界和第三方依赖管理方式，不能直接重拉第三方代码覆盖这些修改。

## 二、项目文件逐项清单与去向

表中的 `.{cpp,h}`、`.{c,h}` 分别表示两个实际文件。

### 2.1 C/C++、头文件与构建入口

| 当前文件 | 用途及当前使用关系 | 处理建议与目标位置 |
| --- | --- | --- |
| `CMakeLists.txt` | 主构建入口；生成 `motor` 库、`master_stack_test`，引入两个第三方库和 Python 桥接路径 | **保留、改造**，仍在根目录 |
| `main.cpp` | 程序入口、网卡初始化、控制台命令注册；属于 `master_stack_test` | **移动、改造** → `src/console/main.cpp` |
| `command.cpp` | `help`、电机 ID/零点/停止/速度/位置/角度查询命令；由 `main.cpp` 注册 | **移动、保留** → `src/console/command.cpp`；更新帮助文本 |
| `command.h` | 上述命令及 Neck 命令声明；被控制台和 Neck 实现引用 | **移动、改造** → `src/console/command.h`；删除 `neckSweep`，迁出 Python 桥接声明 |
| `neck_commands.cpp` | 当前 `NeckMotorSet`、`NeckRpySet`，均通过 Python 解析后发送位置模式命令；还实现 `neckPython()` | **移动、改造** → `src/console/neck_commands.cpp`；RPY 改 MIT，桥接函数迁入 Neck 层 |
| `config.h` | 定义打包的 `Motor_Msg`、`EtherCAT_Msg`；是协议数据结构，**不是运行参数配置** | **移动、改名** → `src/protocol/pdo_types.h` |
| `motor_control.c` | 电机协议编码、配置命令、反馈解码；被控制台、Neck 执行器和 EtherCAT 接收层调用 | **核心保留、移动** → `src/protocol/motor_control.c` |
| `motor_control.h` | 电机协议接口与反馈结构声明 | **核心保留、移动** → `src/protocol/motor_control.h` |
| `math_ops.c` | 协议浮点/整数转换等辅助函数；`float_to_uint`、`uint_to_float` 被协议层及 MIT 日志解码使用 | **核心保留、移动** → `src/protocol/math_ops.c` |
| `math_ops.h` | 数学转换接口，被 `motor_control.h` 引用 | **核心保留、移动** → `src/protocol/math_ops.h` |
| `transmit.cpp` | SOEM 初始化、PDO 收发、状态恢复、命令队列消费、Neck 直接目标槽；由主程序和 Neck 执行器使用 | **核心保留、移动、清理** → `src/ethercat/transmit.cpp` |
| `transmit.h` | EtherCAT 接口、最大从站数声明；被队列与控制台使用 | **核心保留、移动** → `src/ethercat/transmit.h` |
| `queue.h` | 单生产者/单消费者队列及三电机成组消息；控制台生产、EtherCAT 消费 | **保留、移动** → `src/ethercat/queue.h` |
| `periodic_clock.h` | 绝对单调时钟调度；被 `transmit.cpp`、`neck_sweep.cpp`、`neck_stream.cpp` 使用 | **保留、移动** → `src/ethercat/periodic_clock.h` |
| `neck_kinematics.cpp` | C++ 标定加载、正逆运动学；当前主要由流式执行器使用 | **核心保留、移动** → `src/neck/neck_kinematics.cpp`；供新版单点设置复用 |
| `neck_kinematics.h` | `neck::Model`、向量和解结构；被流式执行器引用 | **核心保留、移动** → `src/neck/neck_kinematics.h` |
| `neck_stream.cpp` | UDP 接收、插值、断流减速、恢复过渡、限位；由 `NeckRpyStream` 调用 | **核心保留、移动** → `src/neck/neck_stream.cpp` |
| `neck_stream.h` | `Stream`、`Polynomial` 等声明 | **核心保留、移动** → `src/neck/neck_stream.h` |
| `neck_sweep.cpp` | 扫动、回放、流式命令执行，补偿查询、反馈、异步日志；四个 Neck 命令共用 | **保留、移动、改造** → `src/neck/neck_sweep.cpp`；只删除位置模式扫动，抽取可复用 MIT 支持 |
| `neck_sweep.h` | 总线就绪、直接 PDO 槽、反馈记录接口；被 `transmit.cpp` 和 Neck 执行器引用 | **保留、移动** → `src/neck/neck_sweep.h`；不能随 `NeckSweep` 删除 |

建议这次保留低层 `MotorPositionSet` 和 `NeckMotorSet` 作为维护入口。**删除位置模式扫动，不等于删除协议层的位置模式 API。**

### 2.2 Python、分析与音频工具

| 当前文件 | 用途及当前使用关系 | 处理建议与目标位置 |
| --- | --- | --- |
| `neck_kinematics.py` | Python 正逆运动学；被标定模块导入，间接支持规划、分析、校验；自身也有离线演示入口 | **核心保留、移动** → `python/neck/kinematics.py` |
| `neck_calibration.py` | 读取 JSON、构建模型、派生软限位；被目标解析、轨迹规划、分析、校验脚本导入 | **核心保留、移动** → `python/neck/calibration.py` |
| `neck_sweep.py` | 扫动轨迹规划、Catmull–Rom 文件回放、完整轨迹预检；当前经 `neck_target.py` 调用 | **保留、移动、改造** → `python/neck/trajectory.py`；去掉仅服务位置模式的速度上限字段 |
| `neck_target.py` | 单点目标解析及 `sweep`/`play` 分发；当前为 CMake 指定的 Python helper | **迁移功能后删除**；单点功能转 C++，轨迹入口转 `scripts/plan_neck.py` |
| `neck_audio_stream.py` | WAV 分块到达模拟、DAC 时钟同步播放、生成 RPY 并发送 UDP；人工 CLI 入口，文档引用 | **核心保留、移动、拆分** → `audio/neck_audio_stream.py` |
| `stream_sender_sim.py` | 不播放音频的 UDP 发送模拟器；测试抖动、计算延迟、断流和恢复；文档引用 | **保留、移动** → `tools/stream_sender_sim.py` |
| `record_clicks.py` | 旧版录音工具，以回调到达时间倒推采样时间；只作为独立 CLI，未被程序导入 | **删除**，由 v2 替代；同步清理文档引用 |
| `record_clicks_v2.py` | 使用 ADC 时间戳检测 click，输出单调时钟时刻；独立 CLI | **保留、移动** → `tools/record_clicks_v2.py` |
| `scripts/analyze_run.py` | 分析电机主 CSV 和同名 `_fb.csv`；导入标定/运动学，文档引用 | **核心保留、移动** → `tools/analyze_run.py` |
| `scripts/build_ripple_table.py` | 从 MIT 反馈日志或原始 JSON 建补偿表；支持保留 7/14/28 次谐波 | **核心保留** → 原位置；增加明确的配置/输出路径参数 |
| `scripts/validate_neck_calibration.py` | 离线验证标定、软限位和参考工作范围；文档引用 | **保留** → 原位置；修改导入路径 |

音频生成器目前都在 `neck_audio_stream.py` 内，建议拆为：

| 现有类 | 新位置 |
| --- | --- |
| `Generator` | `audio/generators/base.py` |
| `ReplayGenerator` | `audio/generators/replay.py` |
| `ClickGenerator` | `audio/generators/click.py` |

另新增 `audio/generators/__init__.py`。这些类生成的是 **RPY 轨迹，不是 WAV 素材**。

### 2.3 配置、素材与文档

| 当前文件 | 用途及当前使用关系 | 处理建议与目标位置 |
| --- | --- | --- |
| `config/neck_calibration.json` | 电机映射、方向、零位、机械端点、限位策略、验证参考；Python 标定模块及 C++ `Model` 读取 | **核心保留** → 原位置；不改变标定数值 |
| `config/ripple_comp.json` | 当前运行补偿表；`RippleComp` 在 `Comp != 0` 时读取；建表脚本输出 | **核心保留** → 原位置 |
| `config/ripple_comp_raw.json` | 未筛选原始表；建表脚本可读取并重新筛选；不是运行时默认表 | **核心保留** → 原位置；不能当临时数据删除 |
| `trajectories/01_combo_smooth_100hz.csv` | 三轴组合测试轨迹；可由 `NeckRpyPlay`、发送模拟器、音频 Replay 显式读取 | **保留、移动** → `assets/trajectories/01_combo_smooth_100hz.csv` |
| `trajectories/02_speech_gestures_100hz.csv` | 说话动作 100 Hz 轨迹；文档用于流式测试，同样可回放 | **保留、移动** → `assets/trajectories/02_speech_gestures_100hz.csv` |
| `trajectories/03_speech_gestures_30hz.csv` | 说话动作 30 Hz 轨迹；文档用于音频 Replay | **保留、移动** → `assets/trajectories/03_speech_gestures_30hz.csv` |
| `click_test_8s.wav` | 8 秒、48 kHz、单声道 PCM；click 同步实验素材，由音频 CLI 显式传入 | **保留、移动** → `assets/audio/click_test_8s.wav` |
| `replay_bg_34s.wav` | 34 秒、48 kHz、单声道 PCM；Replay 音频素材，由音频 CLI 显式传入 | **保留、移动** → `assets/audio/replay_bg_34s.wav` |
| `README.MD` | 原始 SOEM/控制台部署文档，命令和架构说明已明显落后 | **改造、统一大小写** → 根目录 `README.md` |
| `Project-Neck 脖子运动学模型说明.md` | 模型推导、坐标约定、接口和限制；被标定文档链接 | **保留、移动** → `docs/neck_kinematics.md` |
| `docs/neck_calibration.md` | 标定记录、字段说明、离线验证步骤 | **保留、修订引用** → 原位置 |
| `docs/neck_progress_2026-10-14.md` | 阶段进展、实验结论、测试命令 | **保留历史记录、补充现状说明** → 原位置；不把历史结果改写成新版本验收结果 |

两份补偿 JSON 均包含电机 1、2、3 的正反向表，每张 500 个有限数值。原始表是重要的可复现输入，应随代码保留。

本文 `docs/motor_cleanup_plan.md` 是盘点后新增的整理方案，保留在 `docs/`；不计入上述盘点时的 49 个项目文件。

### 2.4 日志、生成物和杂项

| 当前文件/集合 | 用途及使用状态 | 处理建议与目标位置 |
| --- | --- | --- |
| `click_log.csv` | 音频 click 发送/生成日志，格式对应 `neck_audio_stream.py`；未见自动消费者 | **移动、不提交** → `data/audio/click_log.csv` |
| `replay_log.csv` | 音频 Replay 发送/生成日志；未见自动消费者 | **移动、不提交** → `data/audio/replay_log.csv` |
| `clicks_mic.csv` | 麦克风 click 时间记录；未见自动消费者 | **移动、不提交** → `data/audio/clicks_mic.csv` |
| `clicks_mic_info.txt` | ADC 时间有效性、输入延迟等诊断；格式对应 v2 | **移动、不提交** → `data/audio/clicks_mic_info.txt` |
| `data/sweep/*.csv` | 28 组运行日志：yaw 15、pitch 4、roll 4、stream 5；供分析工具，MIT `_fb.csv` 还可供建表 | **保留本地、不提交** → 原位置 |
| `NeckRpyPlay` | 零字节空文件，无实现、无构建引用 | **删除** |
| `.DS_Store` | Finder 元数据，无项目用途 | **删除、忽略** |
| `__pycache__/neck_calibration.cpython-310.pyc` | Python 缓存 | **清理、忽略** |
| `__pycache__/neck_kinematics.cpython-310.pyc` | Python 缓存 | **清理、忽略** |
| `__pycache__/neck_sweep.cpython-310.pyc` | Python 缓存 | **清理、忽略** |
| `__pycache__/neck_stream.cpython-310.pyc` | 对应源码已不存在的遗留缓存，不是当前 C++ Stream 的依赖 | **删除、忽略** |
| `build/**` | CMake 缓存、生成的 Makefile/编译器探测源码、对象、库、程序、第三方 example 等 | **不提交**；整理后用干净构建目录重建，不搬迁旧产物 |

注意：`analyze_run.py` 分析的是**电机主日志/反馈日志**，不是根目录这几份音频 CSV。

## 三、第三方文件清单与处理边界

建议保留 `thirdparty/` 作为第三方依赖边界，不把上游源码混进 `src/ethercat` 或 `src/console`。因此，指定业务目录之外，仍保留根 `CMakeLists.txt`、`README.md`、`.gitignore` 和 `thirdparty/`。

以下按同用途文件合并列出，花括号表示对应文件的完整集合。

### 3.1 cpp-readline

| 文件，相对 `thirdparty/` | 用途/当前引用 | 建议 |
| --- | --- | --- |
| `CMakeLists.txt` | 引入 SOEM 和 cpp-readline | 原位保留 |
| `cpp-readline/CMakeLists.txt` | 查找 Readline、构建库、控制示例构建 | 原位保留；默认关闭示例 |
| `cpp-readline/src/CMakeLists.txt` | 构建 `cpp-readline` 库 | 原位保留 |
| `cpp-readline/src/Console.{cpp,hpp}` | 控制台实现和 API；`main.cpp` 使用 | 核心依赖，原位保留 |
| `cpp-readline/cmake/modules/FindReadline.cmake` | 查找系统 GNU Readline | 原位保留 |
| `cpp-readline/example/CMakeLists.txt` | 定义 `cpp-readline-example` | 保留上游文件，但不纳入默认构建 |
| `cpp-readline/example/main.cpp` | 上游计算器/控制台演示；当前 `BUILD_EXAMPLES=ON`，确实被构建，但电机程序不依赖 | 同上 |
| `cpp-readline/exampleScript` | 示例程序通过 `executeFile()` 读取 | 随上游示例保留，不用于电机流程 |
| `cpp-readline/Makefile` | 独立编译上游演示；主工程不调用 | 随依赖保留 |
| `cpp-readline/README.md` | 上游说明 | 保留 |
| `cpp-readline/LICENSE` | 许可证 | 必须保留 |
| `cpp-readline/.gitignore` | 上游忽略规则 | 保留 |
| `cpp-readline/.git/**` | 嵌套仓库元数据，不参与编译 | 不作为源码文件提交；先确定 vendor/submodule 方式 |

当前 cpp-readline 基准提交为：

```text
cf908a84159f18abadbc30f3d1ebb1a9d37151b1
```

有本地修改的是根、`src/`、`example/` 三个 `CMakeLists.txt`。建议本次采用**带来源和本地修改记录的 vendor 快照**，避免只记录上游提交而丢失本地构建修改。

### 3.2 SOEM 核心与 Linux 后端

以下路径相对 `thirdparty/soem/`，全部建议原位保留：

| 文件 | 用途/当前引用 |
| --- | --- |
| `CMakeLists.txt` | 构建 `soem` 库，当前 Linux 构建入口 |
| `soem/ethercat.h` | 总入口头文件，项目 EtherCAT 层直接引用 |
| `soem/ethercattype.h` | 基础类型、常量，SOEM 各模块引用 |
| `soem/ethercatmain.{c,h}` | 主站上下文、过程数据及基础控制 |
| `soem/ethercatbase.{c,h}` | EtherCAT 基础报文操作 |
| `soem/ethercatconfig.{c,h}` | 从站发现、配置和 PDO 映射 |
| `soem/ethercatconfiglist.h` | 静态设备配置表，配置模块的条件编译依赖 |
| `soem/ethercatdc.{c,h}` | 分布式时钟，初始化调用 `ec_configdc()` |
| `soem/ethercatcoe.{c,h}` | CoE/SDO 支持，属于库和配置链路 |
| `soem/ethercateoe.{c,h}` | EoE 支持；编入库，业务未直接调用 |
| `soem/ethercatfoe.{c,h}` | FoE 支持；编入库，业务未直接调用 |
| `soem/ethercatsoe.{c,h}` | SoE 支持；编入库，业务未直接调用 |
| `soem/ethercatprint.{c,h}` | 状态/错误文本，项目使用错误解释函数 |
| `osal/osal.h` | OS 抽象接口 |
| `osal/linux/osal.c`、`osal/linux/osal_defs.h` | Linux 时间、线程等 OS 实现 |
| `oshw/linux/nicdrv.{c,h}` | Linux 网卡收发 |
| `oshw/linux/oshw.{c,h}` | Linux 硬件抽象 |

### 3.3 SOEM 其他平台、构建辅助和资料

这些文件**不参与当前 Linux 电机程序的正常构建/运行**，但属于第三方源码快照，建议本轮原位保留，而不是按“当前没调用”逐项裁剪。

| 文件集合，相对 `thirdparty/soem/` | 用途 |
| --- | --- |
| `osal/{erika,intime,macosx,rtems,rtk,vxworks,win32}/{osal.c,osal_defs.h}` | 各平台 OS 抽象实现 |
| `osal/win32/{inttypes.h,stdint.h,osal_win32.h}` | Windows 兼容类型/接口 |
| `oshw/{erika,intime,macosx,rtems,rtk,vxworks,win32}/{nicdrv.c,nicdrv.h,oshw.c,oshw.h}` | 各平台硬件/网卡后端 |
| `oshw/rtk/fec/fec_ecat.{c,h}` | RT-kernel FEC 后端 |
| `oshw/rtk/lw_mac/lw_emac.{c,h}` | RT-kernel Ethernet MAC 后端 |
| `cmake/Modules/Platform/{rtems,rt-kernel,rt-kernel-C,rt-kernel-gcc,rt-kernel-gcc-bfin,rt-kernel-gcc-kinetis}.cmake` | 平台构建定义 |
| `cmake/Toolchains/{rt-kernel-bfin,rt-kernel-kinetis}.cmake` | 交叉编译工具链 |
| `oshw/win32/wpcap/Include/{bittypes.h,ip6_misc.h,Packet32.h,pcap-bpf.h,pcap.h,pcap-namedb.h,pcap-stdinc.h,remote-ext.h,Win32-Extensions.h}` | Windows 抓包接口/兼容头文件 |
| `oshw/win32/wpcap/Include/pcap/{bluetooth.h,bpf.h,namedb.h,pcap.h,sll.h,usb.h,vlan.h}` | pcap 协议及接口头文件 |
| `oshw/win32/wpcap/Lib/{libpacket.a,libwpcap.a,Packet.lib,wpcap.lib}` | 上游附带 Windows 链接库 |
| `oshw/win32/wpcap/Lib/x64/{Packet.lib,wpcap.lib}` | 64 位 Windows 链接库 |
| `appveyor.yml` | 上游 Windows CI 配置 |
| `Doxyfile` | 上游文档生成配置；部分引用的 `doc/`、`test/` 输入在当前快照中缺失 |
| `ChangeLog` | 上游变更记录 |
| `drvcomment.txt` | 网卡延迟调优说明 |
| `LICENSE` | 许可证，必须保留 |

**需要删除的官方示例主要是 `transmit.cpp` 中三大段已注释的替代 `EtherCAT_Command_Set()`，不是 SOEM 的协议实现或平台后端。**

## 四、建议整理后的目录

这是目标结构；标注“新增/抽取”的内容目前不存在，本文档除外。

```text
motor/
├── CMakeLists.txt
├── README.md
├── .gitignore                         # 新增
├── thirdparty/                        # 保留第三方边界
├── src/
│   ├── ethercat/
│   │   ├── transmit.cpp
│   │   ├── transmit.h
│   │   ├── queue.h
│   │   └── periodic_clock.h
│   ├── protocol/
│   │   ├── pdo_types.h
│   │   ├── motor_control.c
│   │   ├── motor_control.h
│   │   ├── math_ops.c
│   │   └── math_ops.h
│   ├── neck/
│   │   ├── neck_kinematics.{cpp,h}
│   │   ├── neck_stream.{cpp,h}
│   │   ├── neck_sweep.{cpp,h}          # 不再包含位置模式扫动
│   │   ├── motion_common.{cpp,h}       # 抽取 MIT/补偿/公共执行支持
│   │   ├── python_planner.{cpp,h}      # 迁入 Python 规划进程桥接
│   │   └── runtime_config.{cpp,h}      # 新增配置读取
│   └── console/
│       ├── main.cpp
│       ├── command.{cpp,h}
│       └── neck_commands.cpp
├── config/
│   ├── runtime.json                   # 新增：统一运行默认参数
│   ├── neck_calibration.json
│   ├── ripple_comp.json
│   └── ripple_comp_raw.json
├── python/neck/
│   ├── __init__.py                    # 新增
│   ├── kinematics.py
│   ├── calibration.py
│   ├── trajectory.py
│   └── runtime.py                     # 新增：同一 JSON 的 Python 读取器
├── audio/
│   ├── neck_audio_stream.py
│   └── generators/
│       ├── __init__.py
│       ├── base.py
│       ├── replay.py
│       └── click.py
├── scripts/
│   ├── plan_neck.py                   # 替代 neck_target.py 的轨迹分发入口
│   ├── build_ripple_table.py
│   └── validate_neck_calibration.py
├── tools/
│   ├── analyze_run.py
│   ├── stream_sender_sim.py
│   └── record_clicks_v2.py
├── assets/
│   ├── trajectories/                 # 三份轨迹 CSV
│   └── audio/                        # 两份 WAV
├── docs/
│   ├── motor_cleanup_plan.md
│   ├── neck_kinematics.md
│   ├── neck_calibration.md
│   └── neck_progress_2026-10-14.md
└── data/                             # 全部忽略，不提交
    ├── sweep/
    └── audio/
```

第一版不必同时拆成许多独立 CMake 库。**先完成文件归位、公共功能抽取和入口清理，保留现有 `motor` 聚合库，减少重构变量。**

## 五、删除与改造的具体范围

### 5.1 删除 NeckSweep，保留 MIT/回放/流式

需要一起修改：

- `main.cpp`：删除 `"NeckSweep"` 注册。
- `command.h`：删除 `neckSweep()` 声明。
- `command.cpp`：删除位置模式扫动帮助。
- `neck_sweep.cpp`：
  - 删除 `neckSweep()` 包装入口。
  - 删除 `sweep(..., mit=false)` 路径和 `set_motor_position()` 扫动分支。
  - 保留 MIT 扫动、回放、流式、反馈和异步日志。
- Python 规划器：
  - 保留扫动轨迹和文件回放。
  - 删除只用于位置模式的速度上限计算/输出，但必须同步修改 C++ 计划解析。
  - **补上或保留 MIT 速度范围检查**，不能删除旧速度检查后失去边界保护。
- 日志：
  - 不必同时大改所有列名；可以保留兼容列并标记不适用，例如位置模式的 `speed_limit_raw_0p1rpm` 等列。
  - 若删列，要同步验证 `analyze_run.py` 和 `build_ripple_table.py`，避免格式断裂。

### 5.2 删除早期代码和残留

- 删除旧 `record_clicks.py`。
- 在功能迁移后删除 `neck_target.py`。
- 删除空文件 `NeckRpyPlay`、`.DS_Store`、失效缓存。
- 清理 `transmit.cpp`：
  - 三段注释掉的单从站/多电机/多从站示例。
  - 与这些示例相关的未使用 `frequency`、`POS_SPD`、`pos_set`、`delta_pos`、`i`。
  - 过时的 `run.sh` 提示。
- `motor_test.cpp` 当前不存在，列为“无需处理”，不虚构删除项。
- 保留协议说明、量化范围、版权和许可证注释，不做无差别注释清空。

### 5.3 NeckRpySet 改为混合模式

建议不是简单把一个函数名替换掉，而是：

1. 使用 C++ `neck::Model` 读取同一标定，完成 RPY → 驱动器绝对角度转换。
2. 检查有限数值、几何可达、传动角裕度、软限位，以及总线/反馈状态；复用 C++ 加载器前应补齐与现有 Python 加载器相当的配置合法性检查。
3. 复用公共 MIT 编码及补偿逻辑：

   ```text
   send_motor_ctrl_cmd(
       kp, kd,
       position_rad,
       velocity_rad_s,
       feedforward_torque_Nm
   )
   ```

4. 从当前反馈位置平滑过渡到目标，避免 `kp=150` 下直接发送大幅位置阶跃；检查整个过渡路径，而不只是终点。
5. 使用配置中的下发频率、增益、Comp；结束时目标速度为 0，并明确“保持最后目标”不等于断使能。
6. 将原来的位置模式 `Speed/Current` 参数从 `NeckRpySet` 接口移除，改为 MIT 参数；**不能静默把旧参数解释成新参数**。

同时让 `NeckMotorSet` 的映射和限位检查使用 C++，这样才可以彻底删除 `neck_target.py` 的单点分支。其低层维护用途可继续保留；原有从站/CAN 参数校验不能在迁移中丢失。

## 六、默认参数集中方案

建议新增 **`config/runtime.json`**，作为 C++ 和 Python 运行默认值的唯一来源。

标定、补偿表仍分别保留，不把它们塞进运行参数文件；`runtime.json` 只引用其路径。

### 6.1 当前值与统一建议

| 参数 | 当前情况 | 建议 |
| --- | --- | --- |
| `kp` | Play/Stream 默认 150；SweepMit 必填；RpySet 尚未使用 MIT | 统一默认 150 |
| `kd` | Play/Stream 默认 2；SweepMit 必填 | 统一默认 2 |
| `Comp` | SweepMit/Stream 省略为 0；文档工作配置为 1；Play 固定不补偿 | 配置集中管理；建议工作默认 1，但这是明确的行为变更 |
| Neck 下发频率 | MIT/Play/Stream 默认 500 Hz | `control.rate_hz = 500` |
| EtherCAT 周期 | `transmit.cpp` 固定 1000 Hz | 单独保存 `ethercat.rate_hz = 1000` |
| UDP 端口 | Stream/模拟器默认 9870；音频程序要求必填 | 统一默认 9870，保留 CLI 覆盖 |
| 音频侧补偿 | `--compensation-ms` 默认 8 ms | `audio.compensation_ms = 8` |
| 补偿相位提前 | `Lead` 默认 0 ms | 单独保存 `control.ripple_lead_ms = 0` |

**音频时间戳提前、补偿查表相位提前、EtherCAT 周期、轨迹下发频率是不同参数，不能合并。**

建议配置形态：

```json
{
  "schema_version": 1,
  "ethercat": {
    "interface": "enp4s0",
    "rate_hz": 1000
  },
  "control": {
    "kp": 150,
    "kd": 2,
    "comp": 1,
    "rate_hz": 500,
    "ripple_lead_ms": 0,
    "friction_torque_nm": 0
  },
  "udp": {
    "host": "127.0.0.1",
    "port": 9870
  },
  "audio": {
    "compensation_ms": 8,
    "delay_s": 0.3,
    "generator_hz": 30,
    "chunk_ms": 60
  },
  "paths": {
    "calibration": "neck_calibration.json",
    "ripple_compensation": "ripple_comp.json",
    "trajectories": "../assets/trajectories",
    "data": "../data/sweep"
  }
}
```

这是**建议值，不是已完成的参数切换**。特别是 Comp 从 0 改 1，以及让 Play 支持补偿，应单独验收；若第一份整理提交要求保持行为，先集中为 0，再单独提交默认值调整。

### 6.2 加载规则

- 优先级：**显式命令参数 > 配置文件**。
- 配置文件通过统一 `--config` 指定；开发构建可以提供默认位置。
- 配置中的相对资源路径以**该配置文件所在目录**解析，不依赖启动工作目录。
- C++ 和 Python 读取同一文件，不分别保留一套硬编码默认值。
- 配置错误、非有限参数、非法频率/端口应在启动或命令执行前拒绝。
- 控制循环外读取配置，不在 500/1000 Hz 循环中访问 JSON。
- 帮助文本展示实际加载值。
- 日志记录最终有效参数和配置来源，便于复现实验。

## 七、需要修改的引用路径与 CMake

### 7.1 C/C++ 引用

建议以 `src/` 为项目头文件根，采用明确前缀：

```cpp
#include "protocol/pdo_types.h"
#include "protocol/motor_control.h"
#include "ethercat/transmit.h"
#include "ethercat/queue.h"
#include "ethercat/periodic_clock.h"
#include "neck/neck_kinematics.h"
#include "neck/neck_stream.h"
#include "neck/neck_sweep.h"
#include "console/command.h"
```

具体修改点：

| 位置 | 修改内容 |
| --- | --- |
| `motor_control.h`、`transmit.h`、`command.h` | `config.h` → `protocol/pdo_types.h` |
| `main.cpp`、`command.cpp`、`neck_commands.cpp`、`neck_sweep.cpp` | 控制台及跨目录头文件路径 |
| `transmit.cpp` | 队列、周期时钟、Neck 反馈接口头文件路径 |
| `neck_stream.cpp` | 周期时钟头文件路径 |
| `neck_commands.cpp` | 移走 `neckPython()`，引入公共 MIT 支持和 C++ 模型 |
| `neck_sweep.cpp` | 不再用 `neckPython({"rpy","0","0","0"})` 获取映射，改用标定模型；规划调用转新入口 |

### 7.2 Python、素材与输出引用

| 位置 | 修改内容 |
| --- | --- |
| `python/neck/calibration.py` | `from neck_kinematics ...` → 包内 `.kinematics`；默认标定路径改为统一配置解析 |
| `python/neck/trajectory.py` | `from neck_calibration ...` → `.calibration` |
| `scripts/plan_neck.py` | 新建显式 `sweep`/`play` CLI，导入 `neck.trajectory` |
| `tools/analyze_run.py` | 根目录导入路径改为 `python/`，导入 `neck.calibration` |
| `scripts/validate_neck_calibration.py` | 同上；保留显式标定 `--config` 的语义，避免和运行配置混淆 |
| `scripts/build_ripple_table.py` | 输出默认仍为 `config/`；建议增加显式输出路径，避免试验时误覆盖正式表 |
| `audio/neck_audio_stream.py` | 引入拆分后的 generators 和统一运行配置 |
| `tools/stream_sender_sim.py` | UDP 默认值读取配置；更新使用示例中的素材路径 |
| 录音、音频使用命令 | stdout/stderr 重定向至 `data/audio/` |
| 全部文档 | 更新 Python 包导入、工具路径、素材路径、删除的命令和新的默认值 |

尤其要修正原 `neck_sweep.py:15` 的旧逻辑：

```python
Path(__file__).resolve().parent / "trajectories" / Path(path).name
```

它目前**丢弃用户传入的目录，只取文件名**。建议新规则为：

- 仅提供文件名：在配置的 `assets/trajectories/` 中查找。
- 提供显式相对/绝对路径：尊重该路径；相对输入路径按用户启动工作目录解释，与配置文件内部的相对路径规则区分。
- 音频 Replay 和发送模拟器采用同样明确的路径约定。

Python 包入口需要统一处理 `python/` 导入根：第一版可以由各 CLI 基于自身位置定位项目根并加入 `python/`，不依赖用户当前目录或临时手工设置的 `PYTHONPATH`。若后续改为可安装包，应另行加入包元数据和安装说明。

文档链接也需要同步修改，例如：

- `docs/neck_calibration.md` 中的模型/标定模块链接改为 `../python/neck/kinematics.py`、`../python/neck/calibration.py`。
- 模型说明移动到 `docs/neck_kinematics.md` 后，其中配置链接改为 `../config/neck_calibration.json`，标定文档链接改为同目录 `neck_calibration.md`。
- JSON 中用于说明来源的旧文件名可更新为新路径，但不借此改变标定数值和确认状态。

### 7.3 CMake 修改清单

1. **更新全部源文件位置**，原有源文件清单改为：

   ```text
   src/protocol/motor_control.c
   src/protocol/math_ops.c
   src/ethercat/transmit.cpp
   src/console/command.cpp
   src/console/neck_commands.cpp
   src/neck/neck_sweep.cpp
   src/neck/neck_kinematics.cpp
   src/neck/neck_stream.cpp
   ```

2. 将抽取/新增实现加入 `motor`：

   ```text
   src/neck/motion_common.cpp
   src/neck/python_planner.cpp
   src/neck/runtime_config.cpp
   ```

3. 主程序入口改为 `src/console/main.cpp`。本轮建议仍保留输出名 **`master_stack_test`**，避免目录整理和程序更名同时发生。

4. 使用目标级配置：
   - `target_include_directories()`，不再全局加入整个源码根目录。
   - 明确 C++17。
   - `find_package(Threads REQUIRED)` 并链接 `Threads::Threads`。
   - 使用 Boost 目标或规范的 include 变量。
   - `m` 改为目标级链接，替代全局 `LINK_LIBRARIES(m)`。
   - SOEM、cpp-readline 的头文件目录通过对应目标传递。

5. 修改编译宏：
   - `NECK_HELPER_PATH` → 新的 `NECK_PLANNER_PATH`，指向 `scripts/plan_neck.py`。
   - 删除硬编码数据/标定路径宏的使用，改为运行配置中的路径。
   - 若保留 `NECK_DEFAULT_CONFIG_PATH`，只作为开发构建默认位置，允许 `--config` 覆盖。

6. **仍需保留 Python 解释器依赖。** Stream 和新版 Set 可以纯 C++，但 SweepMit/Play 仍使用 Python 离线规划。当前 CMake 只确认 `python3` 存在，没有实际确认 NumPy，应补依赖检查或明确安装说明。

7. 在引入第三方前关闭 cpp-readline 示例构建：

   ```text
   BUILD_EXAMPLES=OFF
   ```

   现有缓存为 ON，必须用干净目录或显式覆盖，不能只修改默认值就假定旧缓存会变化。

8. 核对 SOEM 子工程中使用 `CMAKE_SOURCE_DIR` 的路径。模块、Windows 库等属于 SOEM 自身的路径应基于 `CMAKE_CURRENT_SOURCE_DIR`，避免作为子目录时错误指向主项目。

9. 不提交旧 `CMakeCache.txt`、生成 Makefile、对象文件或二进制。

## 八、忽略规则与提交顺序

### 8.1 建议新增 .gitignore

```gitignore
/build/
/build-*/
/data/

__pycache__/
*.py[cod]
.venv/
.DS_Store
```

不要全局忽略 `*.csv`、`*.wav`、`*.json`：轨迹、音频素材和正式配置都需要提交。也不要直接全局忽略 `*.a`，否则可能误排除第三方附带的库文件。

运行数据只是不提交，**无需删除本地实验记录**。当前进展文档说明原始建表 CSV 已不在目录中，因此不能声称仅凭现存 `data/sweep/` 就能从头复现原始表；应保留 `ripple_comp_raw.json` 及其来源说明。

### 8.2 建议分四个提交阶段

#### 阶段 1：目录归位与生成物隔离

- 移动文件、更新引用和 CMake。
- 建立 `.gitignore`。
- 保持控制行为不变。

#### 阶段 2：清理旧入口

- 迁移 Python helper。
- 删除位置模式 `NeckSweep`、旧录音脚本、注释示例和空文件。
- 验证 SweepMit/Play/Stream 没有被误删依赖。

#### 阶段 3：统一配置与 MIT 单点设置

- 增加公共配置读取、MIT 公共功能。
- 改造 `NeckRpySet`。
- 单独记录 Comp 默认行为变化。

#### 阶段 4：文档与验收

- 重写 README 的构建、命令和安全说明。
- 更新历史文档的现状说明；目前“找不到录音脚本/WAV”的描述已不符合目录现状。
- 增加 Python/音频依赖安装说明：NumPy、SciPy、sounddevice，以及系统音频依赖。
- 记录第三方来源、许可证和本地修改。

### 8.3 提交前验收清单

- [ ] 确定主 Git 仓库位置与第三方依赖管理方式，保留 cpp-readline 本地修改。
- [ ] 干净 CMake 构建，不依赖旧 `build/`。
- [ ] Python 导入和 CLI 能从不同工作目录运行。
- [ ] 标定离线校验通过。
- [ ] C++/Python 运动学及标定加载行为一致性检查通过。
- [ ] 三份轨迹均能完成离线规划和预检。
- [ ] 分析/建表工具仍能读取已有日志。
- [ ] 新旧配置路径、缺失配置、非法参数均能明确报错。
- [ ] `NeckSweep` 和旧 helper 的代码引用已清理；历史文档中的引用明确标记历史状态。
- [ ] 不提交运行日志、缓存、构建产物或嵌套 Git 元数据。
- [ ] 硬件回归另行安排，不能把离线检查或旧日志视为新版控制行为已验收。

另外，整理时应顺带对齐两处现有接口声明：`EtherCAT_Transmit()` 的参数声明，以及 `set_motor_feedbackKP` 与实现 `set_motor_feedbackKP_KD` 的名称/签名。它们当前未见业务调用，但不宜作为公开头文件不一致项带入提交版。
