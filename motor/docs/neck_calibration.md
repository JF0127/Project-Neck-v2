# Project-Neck 电机标定与限位记录

记录日期：**2026-10-13**。本次仅整理已经确定的数据与离线校验，不连接硬件，不修改运动学计算或电机控制代码。

## 1. 唯一数据来源与适用范围

- **唯一可执行数据来源：[`config/neck_calibration.json`](../config/neck_calibration.json)**。下列表格是该记录的说明快照，不是另一份程序配置；程序不得从文档表格取值。
- 读取入口：[`neck_calibration.py`](../neck_calibration.py) 的 `load_neck_model(config_path)`。
- 校验入口：[`scripts/validate_neck_calibration.py`](../scripts/validate_neck_calibration.py)。
- 模型：[`neck_kinematics.py`](../neck_kinematics.py)；几何与推导见[《Project-Neck 脖子运动学模型说明》](../Project-Neck%20脖子运动学模型说明.md)。不复制或调整模型的默认 `Geometry`。

当前仓库使用 C++17/Boost；Python 模型依赖 NumPy，没有现成的 YAML 配置。采用 UTF-8 JSON，Python 使用标准库 `json`，C++ 可使用 JSON 解析器（例如已有 Boost 的 PropertyTree JSON reader）。本次不新增第三方依赖、不接入 C++ 控制链路。

3 个电机均为 **ENCOS EC-A2806-P2-36**。读数是**输出端绝对角度，单位度**；断电重启后读数不变，机械范围固定。无需再乘减速比，不对角度做取模或环绕处理。

左右统一从“**人面对机器头**”的视角描述：人的左边 = 机器人右侧。机器人自身坐标采用 REP-103：x 朝前、y 朝左、z 朝上。

## 2. 电机与模型对应关系

来源：已确定的装配对应关系与模型支链定义；状态：已确认。

| 电机 ID（无量纲） | 位置 | 对应模型 | 作用 |
| --- | --- | --- | --- |
| 1 | 人视角左侧 = 机器人右侧 | q1（支链 1，CAD +Z 侧） | 曲柄 |
| 2 | 人视角右侧 = 机器人左侧 | q2（支链 2） | 曲柄 |
| 3 | — | q3 | 头部左右旋转（yaw） |

模型输出顺序始终是 `[q1, q2, q3]`，不能把 JSON 中电机条目的排列顺序或 `ID - 1` 当成模型索引。读取函数依据 `mapping.model_joint` 重排。

## 3. 实测机械范围与方向

来源：驱动器输出端绝对角度实测；状态：已测量。1、2 号的测量只覆盖单电机转动、其他电机不动的情况。

| 电机 ID | 端点 1（deg） | 端点 2（deg） | 方向观察（已确认） |
| --- | --- | --- | --- |
| 1 | 最下 -3.875301 | 最上 80.715561 | 读数增大 → 曲柄向上 |
| 2 | 最下 173.235809 | 最上 87.385452 | 读数减小 → 曲柄向上 |
| 3 | 头转到人视角最左 238.812057 | 头转到人视角最右 56.473869 | 读数增大 → 头转向机器人右侧 |

**物理的“最下/最上”不等于读数的数值最小/最大。** 配置保留端点标签，计算软限位时先按数值排序。曲柄范围是单个电机转动、其他电机不动时测得，**耦合状态下极限可能不同**；不能据此声称所有组合姿态都已获得机械安全保证。

## 4. 软件限位

来源：已确定的限位策略；状态：策略已确认，不等于硬件安全已验证。

`margin_deg = 5.0`，在机械范围的**数值两端各收进 5°**：

```text
lower_deg = min(endpoint_1_deg, endpoint_2_deg) + margin_deg
upper_deg = max(endpoint_1_deg, endpoint_2_deg) - margin_deg
```

| 电机 ID | 软件下限（deg） | 软件上限（deg） |
| --- | --- | --- |
| 1 | 1.124699 | 75.715561 |
| 2 | 92.385452 | 168.235809 |
| 3 | 61.473869 | 233.812057 |

此表为读取函数计算结果的说明快照。**JSON 只保存实测端点和 `margin_deg`，不手抄派生限位。** 限位是驱动器绝对角度，不是相对零位的角度，也不是 RPY 范围。上下限包含端点。

## 5. 方向与零位

对应 `neck_kinematics.Calibration`：

```text
q_cmd = sign * (q_model - q_model_zero) + offset
```

`offset` 的含义是**头部 rpy = [0, 0, 0] 时的驱动器读数**，不是 CAD 的曲柄角。`sign` 比较驱动器与模型正方向：q1/q2 绕 CAD +X 右手，q3 绕 CAD +Y 右手。

| 电机 ID | sign（无量纲） | offset（deg） | 零位来源 | 零位状态 |
| --- | --- | --- | --- | --- |
| 1 | +1 | 38.42013 | 机械范围中点 | 暂定，待目视确认头部大致水平 |
| 2 | +1 | 130.310631 | 机械范围中点 | 暂定，待目视确认头部大致水平 |
| 3 | -1 | 147.642963 | 机械范围中点 | 已确认，中位朝正前方 |

- 三个 `sign` 均按已确定的对应关系记录为已确认。2 号虽“读数减小 → 曲柄向上”，其 `sign` 仍为 **+1**，不可仅根据“向上”来翻转符号。
- 1、2 号初始取行程中点，利用左右镜像对称使 roll ≈ 0；这不是头部水平已确认的证据。
- 若目视发现明显抬头或低头，**只允许 1、2 号同向等量调整**：两个 `offset_deg` 同时加上同一个带符号的 `delta_deg`。不得只改其中一个、改成反向调整或改 `sign` 来凑结果。
- 2 号中点精确到更多小数为 130.3106305°，记录保留已确定的 130.310631°。加载时不重新求中点、不覆盖已记录零位。
- JSON 保存度；读取函数使用 `np.radians(offsets_deg)` 转换为 `Calibration.offset` 所需的弧度。
- 加载后的 `rpy_to_motor(..., degrees=True)` 输出驱动器绝对角度（度）；`degrees=False` 的输入输出均为弧度。软限位返回值始终为度，比较时必须统一单位。

## 6. RPY 与模型裕度

```text
R = Rz(yaw) Ry(pitch) Rx(roll)
```

采用 REP-103，先 roll、再 pitch、再 yaw，绕固定轴：

| 角度 | 正方向 |
| --- | --- |
| roll > 0 | 机器人向右歪头，左耳抬高 |
| pitch > 0 | 低头 |
| yaw > 0 | 机器人向左转 |

`model.min_margin_deg = 30.0` 是**传动角裕度阈值**，来源为已有模型判据和本次已确定参数。它不是软件限位的 `margin_deg = 5.0`。

`info['ok']` 只表示几何可达且传动角裕度不低于阈值，**不会自动检查新增的软件限位**。离线可用性必须同时满足：

1. 模型 `info['ok']` 为真；
2. 三个电机输出均为有限值；
3. 三个输出均在对应的软件上下限内。

不得把不可用点简单截断为限位值并当成原姿态的有效逆解。原模型说明中的未标定示例、仅模型工作空间和 `NeckModel()` 的默认零位，不是本配置下的驱动器绝对角度或实测机械安全范围。

## 7. 配置字段说明

所有角度量使用 `_deg` 后缀，单位度。ID、版本号及 `sign` 无量纲，布尔值、字符串和描述性枚举不适用物理单位。日期格式为 `YYYY-MM-DD`。

| 字段/路径 | 含义、单位及来源/状态范围 |
| --- | --- |
| `schema_version` | 配置结构版本，无量纲；读取函数当前只接受 1 |
| `recorded_date` | 本次记录日期 2026-10-13；适用于本配置各项记录，不推断为每项的独立测量时间 |
| `units.angle_fields/sign/id_and_schema_version/date` | 字段单位及日期格式声明 |
| `hardware.motor_model` | 三个电机的统一型号 |
| `hardware.angle_reference` | `output_shaft_absolute`：输出端绝对角度 |
| `hardware.readings_persist_after_power_cycle` | 断电重启后读数不变，布尔值 |
| `hardware.mechanical_range_fixed` | 机械范围固定，布尔值 |
| `conventions.viewpoint` | 人面对机器头的左右视角 |
| `conventions.rpy_standard/rpy_order/head_axes` | REP-103、RPY 数组顺序、头部坐标轴定义 |
| `conventions.rotation_matrix/rotation_axes` | 旋转矩阵次序与固定轴约定；描述已有模型，不是可切换的计算模式 |
| `conventions.positive_roll/positive_pitch/positive_yaw` | 各轴正方向文字说明 |
| `soft_limits.margin_deg` | 机械范围每端收进量，度；读取时派生软限位 |
| `soft_limits.note` | 单电机测量及耦合机械范围限制 |
| `model.min_margin_deg` | 传给 `NeckModel` 的最小传动角裕度，度 |
| `motors[]` | 三个电机记录；数组顺序不决定模型顺序 |
| `motors[].id` | 驱动器电机 ID，正整数、无量纲、不可重复 |
| `motors[].mapping.model_joint` | `q1`、`q2` 或 `q3`；必须各出现一次 |
| `motors[].mapping.position/branch/role` | 实物位置、支链或串联轴、作用 |
| `motors[].mechanical_range.endpoints[]` | 两个实测端点；`label` 为物理含义，`angle_deg` 为驱动器读数（度） |
| `motors[].direction_observation.description` | 实测的读数变化与运动方向关系 |
| `motors[].calibration.sign` | 驱动器方向相对模型方向的 ±1，无量纲 |
| `motors[].calibration.offset_deg` | RPY 全零时驱动器读数，度；读取时转弧度 |
| `motors[].calibration.sign_source/sign_status` | 方向标定来源及确认状态 |
| `motors[].calibration.offset_source/offset_status/offset_note` | 零位来源、确认状态和待确认事项 |
| `zero_adjustment.offset_definition/crank_midpoint_rationale/rule` | 零位定义、曲柄中点依据、后续调整约束；描述性规则，不自动修改数据 |
| `validation.scan_step_deg` | 单轴与方形四边采样步长，度；当前 0.05° |
| `validation.axis_scan_half_span_deg` | 单轴扫描范围的半宽，度；当前扫描 [-90°, 90°] |
| `validation.range_tolerance_deg` | 参考范围端点的最大允许误差，度；当前 0.2° |
| `validation.reference_ranges_deg.pitch/roll/yaw` | 各轴单独运动的离线参考范围 `[lower, upper]`，度；不是控制限位 |
| `validation.square_boundaries[]` | `half_span_deg` 为 roll/pitch 方形半宽（度）；`expect_all_usable` 为所有边界采样点是否应全部可用（布尔值），固定 yaw = 0 |
| `validation.criterion` | 离线可用性判据说明 |
| 各分组的 `source/status` | 该组字段的来源与状态；端点继承所属 `mechanical_range` 的来源/状态，校验参考继承 `validation` 的来源/状态 |

状态含义：`confirmed` 已确认；`provisional` 暂定；`measured` 已测量；`measured_single_motor_only` 仅单电机测量；其他带 `policy` 的状态表示已确定策略但未必验证硬件安全，`regression_reference_not_measured_coupled_workspace` 表示离线回归参考而非耦合工作空间实测结果。具体待确认项见相邻的 `note` / `offset_note`。

## 8. Python 读取接口

依赖 Python 3 和现有 NumPy。在 `motor/` 目录可运行：

```python
import numpy as np
from neck_calibration import load_neck_model, MODEL_JOINTS

model, soft_limits_deg, id_to_model = load_neck_model()
# 也可传入自定义路径：load_neck_model("config/neck_calibration.json")
# 返回：
# model: NeckModel（默认几何，应用 JSON 标定与传动角裕度阈值）
# soft_limits_deg: (3, 2) ndarray，行 q1/q2/q3，列 [下限, 上限]，单位度
# id_to_model: {1: "q1", 2: "q2", 3: "q3"}

q_deg, info = model.rpy_to_motor([0, 0, 0], degrees=True)
usable = bool(
    info["ok"]
    and np.all(np.isfinite(q_deg))
    and np.all(q_deg >= soft_limits_deg[:, 0])
    and np.all(q_deg <= soft_limits_deg[:, 1])
)
angles_by_id_deg = {
    motor_id: float(q_deg[MODEL_JOINTS.index(joint)])
    for motor_id, joint in id_to_model.items()
}
print(usable, angles_by_id_deg)  # 仅离线计算，无硬件下发
```

默认路径相对 `neck_calibration.py` 定位，不依赖当前工作目录。读取函数校验结构版本、映射唯一性、有限数值、sign、收进后有效行程、零位在软限位内等；不合法时抛出 `ValueError`，文件读取失败时抛出 `OSError`。暂定零位可以加载，离线校验通过不会自动把状态改为已确认。

## 9. 离线校验与参考范围

从 `motor/` 目录运行（不需要 root）：

```bash
python3 scripts/validate_neck_calibration.py
# 指定配置：
python3 scripts/validate_neck_calibration.py --config config/neck_calibration.json
```

校验脚本从同一 JSON 读取参考值，不另存一组运行标定参数。成功退出码为 0；任何不一致打印 `[FAIL]` 并以非零码退出，不使用可能被 `python -O` 去掉的断言。

| 检查 | 预期 |
| --- | --- |
| rpy = [0, 0, 0] | 三个输出等于配置零位；模型 ok 且在软限位内 |
| 软件限位 | 机械数值两端各收进 `margin_deg = 5.0°` |
| 只动 pitch（roll = yaw = 0） | -18.5° ～ 22.2°，正为低头 |
| 只动 roll（pitch = yaw = 0） | -21.7° ～ 21.7° |
| 只动 yaw（roll = pitch = 0） | -86.2° ～ 86.2° |
| roll/pitch 同时 ±8° 方形四边，yaw = 0 | 全部可用 |
| roll/pitch 同时 ±10° 方形四边，yaw = 0 | 不全部可用 |

单轴扫描步长 0.05°，范围端点允许误差 0.2°；还检查零点可用、可用区连续、扫描两端已离开可用区，避免只找到一个截断区间。当前扫描结果为 pitch [-18.50°, 22.20°]、roll [-21.65°, 21.65°]、yaw [-86.15°, 86.15°]，均满足参考容差。

方形检查不是只检查四个角点：沿四条完整边以 0.05° 采样并去除重复角点，±8° 共 1280 点，±10° 共 1600 点。“全部可用”指该分辨率下所有边界采样点通过，**不是连续边界的解析证明，也不是方形内部、非零 yaw 或真实机械安全的证明**。采样之外的几何干涉、球铰摆角、耦合限位等仍需后续独立验证。

## 10. 后续修改零位流程

1. 保留当前配置版本/备份，记录修改日期、原因和确认依据。不要修改模型几何、运动学计算、ID 映射或方向符号来消除零位误差。
2. 本任务不执行真机操作。后续由负责标定的人目视确认头部大致水平；若需要修正明显抬头或低头，按已确定规则选定同一个带符号的 `delta_deg`，**仅将 1、2 号 `offset_deg` 同时加上该值**。不允许两侧单独修改或反向修改；若此规则无法完成确认，应停止并重新评审标定方案。
3. 3 号中位朝前已确认，不随曲柄零位调整而改变。若确需重标定 yaw，应单独记录新的确认依据。
4. 只在 JSON 修改有效数据，更新 `recorded_date`、受影响项的 `offset_source`、`offset_status`、`offset_note`，记录原中点和本次调整量/原因。目视确认前保持 `provisional`；确认后才改为 `confirmed`。不要将调整后的零位重新覆盖成机械中点。
5. 原始机械端点不因修改零位而变化；除非独立重新测量，否则不修改机械范围。软限位由原端点和 margin 自动派生，不能手填。检查新的零位仍在软限位内。
6. 重新运行完整离线校验并保存输出。零位变化可能影响软限位约束下的可用范围；若超出当前参考容差，脚本应报错。不要为消除失败而放宽限位或容差；先审查变化原因，经确认后才更新 JSON 内的离线回归参考及其来源/状态。
7. 同步更新本文表格/日期/扫描结果等说明快照，交叉检查与 JSON 一致。离线校验通过不替代后续独立的硬件安全确认，也不会触发任何硬件运动。
