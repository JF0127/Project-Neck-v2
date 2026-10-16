# Project-Neck 脖子运动学模型说明

Oct 12, 2026 · @Johnny

> 2026-10-13 标定记录补充：当前电机 ID 映射、实测机械端点、方向、零位和限位策略的唯一数据来源为 [`config/neck_calibration.json`](config/neck_calibration.json)，读取入口为 [`neck_calibration.py`](neck_calibration.py)，详见[电机标定与限位记录](docs/neck_calibration.md)。本文的默认零位、示例标定数值及仅模型工作空间不作为真机标定数据；加载配置后，RPY 全零输出为记录的驱动器绝对零位，而非三个 0。`info['ok']` 不包含软件限位检查；1、2 号零位仍待目视确认水平。

## 1. 概述

输入头部姿态 `[roll, pitch, yaw]`，输出 3 个电机（左/右曲柄电机 + yaw 电机）的输出端角度；也支持反向：由电机读数算出头部姿态。实现文件为 `neck_kinematics.py`，几何参数全部从装配文件 `neck-crossing_asm1.stp` 提取。

| 项目 | 内容 |
| --- | --- |
| 输入 | 单个 `[roll, pitch, yaw]` 或 N×3 轨迹，弧度或角度 |
| 输出 | `[q1, q2, q3]`：电机输出端角度（减速后），相对头部摆正时的位置 |
| 反向 | 电机读数 → `[roll, pitch, yaw]`，用于编码器反馈与离线分析 |
| 可下发判据 | `info['ok']`：可达且传动角裕度 ≥ 30° |
| 依赖 | Python 3 + NumPy |

本模型只含运动学（位置与速度关系），不含质量、惯量与重力；动力学需要另行补充质量属性。

## 2. 机构结构与坐标系

机构为 2-RSS + U 并联加串联 yaw：两个底座电机经曲柄、连杆驱动装在十字轴上的上平台，yaw 电机装在上平台上直接带动头部。

&#91;embedded content: 运动链 · 2 条并联支链 + 串联 yaw\]

十字轴两根轴与 yaw 轴在 CAD 中交于同一点 O（误差约 1e-12 mm），因此头部是绕 O 的纯转动，没有平移耦合：

```latex
R_{cad} = R_x(a)\,R_z(b)\,R_y(\psi)
```

其中 a 是十字轴外轴（固定在底座）的转角，b 是内轴（固定在上平台）的转角，ψ 是 yaw 电机转角。顺序固定为外轴 → 内轴 → yaw。

| 坐标系 | 原点 | x | y | z | 用途 |
| --- | --- | --- | --- | --- | --- |
| 模型系（CAD 轴向） | O，CAD 坐标 (14.5, 72.631, −38.0) mm | 朝前（脸、连杆一侧） | 竖直向上 | 机器人右侧 | 几何参数、机构角 |
| 头部标准系（REP-103） | O | 朝前 | 朝左 | 朝上 | 输入输出的 rpy |

两者关系：x\_std = +X，y\_std = −Z，z\_std = +Y。

## 3. 几何参数

模型共 5 个几何量，全部取自 STEP 装配，并按设计意图做了左右对称化（坐标相对 O，单位 mm，对应代码中的 `Geometry`）。

| 参数 | 代码字段 | 数值 | 来源 |
| --- | --- | --- | --- |
| 上球铰中心（平台系） | `p_up` | (26.349, 27.364, ±22.0) | GEB5J 球心，去除装配倾斜后的平台系坐标 |
| 电机轴线位置 (y, z) | `m_yz` | (−75.131, ±27.0)，轴向 ∥ X | motor-base 安装孔中心 |
| 下球铰中心 x | `x_low` | 25.600 | 曲柄销上 GEB5J 球心 |
| 曲柄半径 | `r` | 18.0 | 电机轴线到曲柄销距离，两侧一致 |
| 连杆球心距 | `L` | 94.003 | 连杆两端轴承孔中心距，两根连杆一致 |

支链 1 指 CAD z 正侧（机器人右侧）的电机与连杆，支链 2 在左侧。

提取方法：解析 STEP 的装配树（NEXT\_ASSEMBLY\_USAGE\_OCCURRENCE + ITEM\_DEFINED\_TRANSFORMATION）得到各零件实例的位姿；转轴由轴承、电机的圆柱面轴线给出；球心取球铰两个截圆圆心的中点；连杆长度取连杆零件两端轴承孔 r = 6.5 mm 圆柱的中心距。

STEP 不含材料信息，因此质量、质心、惯量没有提取；需要时在 NX 中用“测量体”按刚体分组导出。

## 4. 角度约定

输入输出的 rpy 采用 ROS REP-103：先 roll、再 pitch、再 yaw，绕固定轴，pitch > 0 为低头。

```latex
R = R_z(\text{yaw})\,R_y(\text{pitch})\,R_x(\text{roll})
```

| 角度 | 层级 | 正方向 | rpy 全 0 时 |
| --- | --- | --- | --- |
| roll | 输入 | 头向右歪（左耳抬高） | 0 |
| pitch | 输入 | 低头 | 0 |
| yaw | 输入 | 向左转 | 0 |
| a | 机构（十字轴外轴，绕 X） | 头顶倒向右侧 | 0 |
| b | 机构（十字轴内轴，绕 Z） | 抬头 | 0 |
| ψ | 机构（yaw 电机，绕平台 Y） | 向左转 | 0 |
| q1, q2（模型角） | 曲柄电机 | 绕 +X 右手；q = 0 时曲柄指向 +Y | −59.678°, +59.678° |
| q3（模型角） | yaw 电机 | 绕平台 +Y 右手 | 0 |

输出给驱动器的指令角是相对零位的值：rpy 全为 0 时三个输出都是 0。习惯 pitch > 0 = 抬头的话，输入前把 pitch 取负即可。

小角度时，roll 对应两电机同向转，pitch 对应两电机反向转（零位附近 dq/da ≈ −1.14，dq/db ≈ ±1.58）。

## 5. 逆运动学：rpy → 电机角

全程是闭式解，无迭代，四步完成；对应 `NeckModel.rpy_to_motor()`。

1. **rpy → 头部旋转矩阵 → 模型系。** 先算标准系下的 R，再做基变换：

```latex
R_{cad} = C\,R\,C^{T},\qquad C = \begin{bmatrix}1&0&0\\0&0&1\\0&-1&0\end{bmatrix}
```

2. **分解出机构角 (a, b, ψ)。** 展开 Rx(a)·Rz(b)·Ry(ψ) 后逐项对照：

```latex
b = \operatorname{atan2}\!\left(-R_{01},\ \sqrt{R_{00}^2+R_{02}^2}\right),\quad a = \operatorname{atan2}(R_{21},R_{11}),\quad \psi = \operatorname{atan2}(R_{02},R_{00})
```

这一步不能省：机构的层级是侧倾在外、yaw 在内，与 rpy 的 yaw 在外相反，所以 yaw 不为 0 时 pitch、roll 会混合到 a、b 上；roll、pitch 同时不为 0 时 yaw 电机也要转（例如 roll 8°、pitch −8°、yaw 0 → q3 = 1.12°）。分解在 |b| = 90° 处奇异，机构到不了那里。

3. **(a, b) → 两个曲柄角。** 对支链 i，上球心 A\_i = R\_x(a) R\_z(b) p\_i，曲柄转动中心 m\_i = (x\_low, y\_i, z\_i)，d\_i = A\_i − m\_i。下球心 B\_i = m\_i + r(0, cos q\_i, sin q\_i)。约束 |A\_i − B\_i| = L 化为一个三角方程：

```latex
d_y\cos q_i + d_z\sin q_i = K_i,\qquad K_i = \frac{\lVert d_i\rVert^2 + r^2 - L^2}{2r}
```

```latex
q_i = \operatorname{atan2}(d_z, d_y) + s_i\arccos\frac{K_i}{\sqrt{d_y^2+d_z^2}},\qquad s_1=-1,\ s_2=+1
```

|K\_i| 大于 √(d\_y² + d\_z²) 时无解（不可达）。分支 s\_i 按装配姿态选取：两根曲柄朝内上方。

4. **模型角 → 驱动器指令角。** q3 = ψ（关节模组输出端角度）。最后减去零位、乘方向、加偏置：

```latex
q_{cmd} = \text{sign}\odot\left(q_{model} - q_{model,0}\right) + \text{offset}
```

q\_model,0 = (−59.678°, +59.678°, 0) 是 rpy 全 0 时的模型角；sign 与 offset 由标定得到（第 8 节）。

## 6. 正运动学：电机读数 → rpy

并联部分没有简单闭式正解，用牛顿迭代求 (a, b)；ψ 直接等于 q3。对应 `NeckModel.motor_to_rpy()`。

1. 指令角还原为模型角：q\_model = (q\_cmd − offset) / sign + q\_model,0。
2. 约束 f\_i(q, x) = |A\_i(x) − B\_i(q\_i)|² − L² = 0，x = (a, b)。迭代 x ← x − J\_x⁻¹ f，J\_x = ∂f/∂x。以上一采样点的结果作初值，通常 2～3 次收敛到 1e-12 rad。
3. 由 (a, b, ψ) 拼出 R\_cad，换回标准系 R = Cᵀ R\_cad C，再取 rpy：pitch = asin(−R₂₀)，roll = atan2(R₂₁, R₂₂)，yaw = atan2(R₁₀, R₀₀)。

速度关系由同一约束对时间求导得到：

```latex
J_q\,\dot q + J_x\,\dot x = 0 \;\Rightarrow\; \dot q = J\,\dot x,\qquad J = -J_q^{-1}J_x
```

静力学对偶为 τ\_platform = Jᵀ τ\_motor。零位处 J = \[\[−1.14, 1.58\], \[−1.14, −1.58\]\]：头部转 1°，曲柄电机转 1.14°～1.58°。det J\_q = 0 对应曲柄与连杆共线（边界奇异），det J\_x = 0 对应平台失控（并联奇异）。

## 7. 工作空间与可下发判据

抬头是瓶颈：yaw = 0 时纯抬头只能保持裕度到 18.5°，24.5° 处无解；低头可到 42°。roll 与 pitch 同时转动时的安全区约为 ±9° 的方形。

传动角裕度 = 曲柄与连杆夹角离 0°/180° 的最小距离。越接近 0 越接近奇异：传力变差、减速器间隙和结构柔性被放大。零位处裕度为 66°。`info['ok']` 要求可达且裕度 ≥ 30°（`min_margin_deg` 可调）。

| pitch（yaw = 0） | roll 范围，裕度 ≥ 30° | roll 几何可达范围 |
| --- | --- | --- |
| 低头 20° | ±30.2° | ±36.0° |
| 低头 13°（传动最佳） | ±36.5° | ±42.0° |
| 平视 0° | ±21.5° | ±30.2° |
| 抬头 10° | ±8.0° | ±14.0° |
| 抬头 15° | ±3.0° | ±8.2° |

yaw 不为 0 时，pitch、roll 会重新分配到 a、b 上，上表不能直接套用，以 `info['ok']` 为准。上球铰在零位已有约 6.4° 摆角，±10° 范围内最大约 14°，需对照 GEB5J-C 的允许摆角；十字轴支架的机械限位未建模。

## 8. 真机标定

上真机前必须标定每个电机的 `sign`（±1）和 `offset`（头部摆正时的驱动器读数，弧度），CAD 给不出这两组值：驱动器的正方向与零点由驱动器自己定义，且 CAD 装配本身有约 0.5° 的闭合误差。

1. 驱动器上电但不使能（或低刚度），手动把头部摆正：平视、不歪、朝正前。可用水平仪和正前方参照点辅助。
2. 记录 3 个电机的读数，填入 `Calibration.offset`。
3. 低刚度下逐个让电机小幅正转（几度），观察头部运动并与第 4 节的模型正方向对照：电机 1、2 正转时模型中曲柄绕 +X 右手转动，即两电机同向正转会让头向左歪（roll 为负，因为第 4 节中 dq/da ≈ −1.14）；电机 3 正转时模型中头部向左转。方向一致填 +1，相反填 −1。
4. 验证：下发 roll ±5°、pitch ±5°、yaw ±20° 的小幅指令，确认头部方向正确、左右对称；再用 `motor_to_rpy` 读回编码器，检查与指令一致。

```python
import numpy as np
from neck_kinematics import NeckModel, Calibration
cal = Calibration(sign=np.array([+1, -1, +1]),          # 示例值，按实测填写
                  offset=np.array([0.512, -1.037, 0.0]))  # 示例值，弧度
m = NeckModel(calib=cal)
```

零点误差会直接变成姿态误差；要更高精度，可以在多个姿态下用 IMU 或视觉测头部真实姿态，以 offset 和 L 为未知量做最小二乘。

## 9. 代码接口

对外只需要 `NeckModel` 的两个方法；单点和轨迹用同一个接口，输出与输入同单位。

```python
from neck_kinematics import NeckModel
m = NeckModel()                                         # 默认几何、未标定、裕度阈值 30°

q, info = m.rpy_to_motor([5, -12, 40], degrees=True)    # -> [-5.429, -25.539, 41.124]
if info['ok']:
    send_to_motors(q)                                   # 你的下发函数

Q, infos = m.rpy_to_motor(rpy_traj, degrees=True)       # rpy_traj: (N,3) -> Q: (N,3)
bad = [k for k, i in enumerate(infos) if not i['ok']]   # 不可下发的采样点

rpy = m.motor_to_rpy(q_measured, degrees=True)          # 编码器 -> 头部姿态
```

| 接口 | 输入 | 输出 |
| --- | --- | --- |
| `NeckModel(geometry, calib, min_margin_deg)` | 几何、标定、裕度阈值，均可省略 | 模型对象 |
| `rpy_to_motor(rpy, degrees=False)` | (3,) 或 (N,3) 的 \[roll, pitch, yaw\] | 指令角 q，及 info（单点为 dict，轨迹为 dict 列表） |
| `motor_to_rpy(q_cmd, degrees=False)` | (3,) 或 (N,3) 的驱动器读数 | \[roll, pitch, yaw\] |
| `info['mech']` | — | 机构角 (a, b, ψ)，弧度 |
| `info['reachable']` / `info['margin_deg']` / `info['ok']` | — | 是否有解 / 传动角裕度 / 是否可下发 |

不可达的点返回 nan，不要下发；轨迹中出现 `ok = False` 时应在上游规划阶段限幅，而不是在执行层截断。移植到 C++ Motor 进程时，逆解四步都是闭式运算，可直接逐行翻译；正解的牛顿迭代建议把数值雅可比换成解析式。

## 10. 验证与已知限制

数值上模型自洽：10 个测试姿态和一条 200 点轨迹做 rpy → 电机 → rpy 往返，误差在 1e-14° 量级；直接运行 `python neck_kinematics.py` 可复现。与 CAD 实物的一致性约在 0.5° 量级，原因如下。

| 项目 | 情况 | 影响 |
| --- | --- | --- |
| CAD 装配闭合误差 | 电机 2 偏离安装孔 0.41 mm；上球心与连杆孔心沿轴错开 0.3～0.5 mm；平台带 a ≈ 0.26°、b ≈ 0.52° 倾斜 | 用 CAD 姿态反算的曲柄角与 CAD 相差约 0.5°；模型采用对称的设计值，零点靠标定吸收 |
| 零件公差与装配间隙 | 未建模 | 实际 L、r、球心位置可能偏离设计值，需要高精度时做参数辨识 |
| 球铰摆角、十字轴限位、零件干涉 | 未建模 | 可达区可能比第 7 节更小，需在 NX 中核对 |
| 减速器间隙、柔性 | 未建模（纯运动学） | 实际姿态在静态下也可能有小误差 |
| 质量与动力学 | 未包含 | 重力前馈、力矩估计、增益设计需要另补质量属性 |

待确认：GEB5J-C 的允许摆角，以及三个电机实测的 sign 与 offset。
