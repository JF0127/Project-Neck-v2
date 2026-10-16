"""
Project-Neck 运动学模型：头部 RPY  <->  3 个电机角
（几何参数从 neck-crossing_asm1.stp 提取）
====================================================================

用法
----
    from neck_kinematics import NeckModel
    m = NeckModel()
    q, info = m.rpy_to_motor([0.1, -0.2, 0.3])        # 单个 [roll, pitch, yaw]，弧度
    Q, info = m.rpy_to_motor(rpy_traj, degrees=True)  # 轨迹 (N,3)，角度制
    rpy     = m.motor_to_rpy(q)                       # 反过来，用于编码器反馈/数据分析

输出 q = [q1, q2, q3] 为【电机输出端】角度（关节模组减速后的角度），
相对于"头部 rpy = 0"时的位置，已乘上标定的方向和零点偏置。

RPY 约定（ROS REP-103）
-----------------------
头部坐标系：x 朝前（脸的方向），y 朝左，z 朝上。原点在三轴交点 O。
    R = Rz(yaw) @ Ry(pitch) @ Rx(roll)      （先 roll、再 pitch、再 yaw，绕固定轴）
    roll  > 0 : 头向右歪（左耳抬高）
    pitch > 0 : 低头
    yaw   > 0 : 向左转
如果你习惯 pitch > 0 = 抬头，把输入的 pitch 取负即可。

机构
----
底座 --U 副[先绕 CAD-X(底座), 再绕 CAD-Z(平台)]--> 上平台 --yaw[绕平台 CAD-Y]--> 头部
两个底座电机(轴线 ∥ CAD-X) -- 曲柄 r -- 球铰 -- 连杆 L -- 球铰(固定在上平台)
三轴交于一点 O，头部姿态 R_cad = Rx(a) @ Rz(b) @ Ry(psi)。
CAD 坐标：X 朝前(连杆一侧)，Y 向上，Z 朝机器人右侧；单位 mm。

注意：机构的转动顺序是 roll(外) -> pitch -> yaw(内)，而 RPY 是 yaw 在最外层。
两者不是一一对应的简单关系：例如先 yaw 90° 再低头，在机构上要靠 a(侧倾轴)来实现。
本模块通过完整的旋转矩阵分解处理这种耦合，不做小角度近似。
"""
from dataclasses import dataclass, field
import numpy as np


# ====================================================================== 参数
@dataclass
class Geometry:
    # 上球铰中心（平台坐标系，相对 O）
    p_up: np.ndarray = field(default_factory=lambda: np.array([
        [26.349, 27.364, +22.0],   # 支链 1：CAD z 正侧（机器人右侧）的电机
        [26.349, 27.364, -22.0],   # 支链 2：机器人左侧的电机
    ]))
    m_yz: np.ndarray = field(default_factory=lambda: np.array([   # 电机轴线 (y, z)，相对 O
        [-75.131, +27.0],
        [-75.131, -27.0],
    ]))
    x_low: float = 25.600      # 下球铰中心 x
    r: float = 18.0            # 曲柄半径
    L: float = 94.003          # 连杆两球心距
    branch: tuple = (-1, +1)   # IK 分支（两曲柄朝内上方，与装配一致）


@dataclass
class Calibration:
    """模型角 -> 驱动器指令角：  q_cmd = sign * (q_model - q_model_zero) + offset
    sign  : 驱动器正方向与模型正方向一致为 +1，相反为 -1（模型正方向：q1,q2 绕 CAD +X 右手，q3 绕 CAD +Y 右手）
    offset: 头部 rpy=0 时驱动器读数（弧度）。
    这两组值必须上真机标定，CAD 不能给出（CAD 装配本身存在 ~0.5° 的闭合误差）。"""
    sign: np.ndarray = field(default_factory=lambda: np.array([1.0, 1.0, 1.0]))
    offset: np.ndarray = field(default_factory=lambda: np.zeros(3))


# ====================================================================== 基本旋转
def Rx(t):
    c, s = np.cos(t), np.sin(t); return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
def Ry(t):
    c, s = np.cos(t), np.sin(t); return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
def Rz(t):
    c, s = np.cos(t), np.sin(t); return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

# 头部标准系(x前 y左 z上) 各轴在 CAD 系中的表示：x=+X, y=-Z, z=+Y
C_STD2CAD = np.array([[1, 0, 0],
                      [0, 0, 1],
                      [0, -1, 0]], float)


def rpy_to_R(roll, pitch, yaw):
    return Rz(yaw) @ Ry(pitch) @ Rx(roll)


def R_to_rpy(R):
    pitch = np.arcsin(np.clip(-R[2, 0], -1, 1))
    return np.array([np.arctan2(R[2, 1], R[2, 2]), pitch, np.arctan2(R[1, 0], R[0, 0])])


def R_to_mech(Rc):
    """R_cad = Rx(a) Rz(b) Ry(psi)  ->  (a, b, psi)。|b|=90° 处奇异（机构到不了）。"""
    b = np.arctan2(-Rc[0, 1], np.hypot(Rc[0, 0], Rc[0, 2]))
    a = np.arctan2(Rc[2, 1], Rc[1, 1])
    psi = np.arctan2(Rc[0, 2], Rc[0, 0])
    return np.array([a, b, psi])


def mech_to_R(a, b, psi):
    return Rx(a) @ Rz(b) @ Ry(psi)


# ====================================================================== 并联部分
class ParallelPart:
    def __init__(self, g: Geometry):
        self.g = g

    def ik(self, a, b):
        """平台角 (a,b) -> 曲柄角 q1,q2（模型角，q=0 时曲柄指向 +Y）。不可达返回 nan。"""
        g = self.g; R = Rx(a) @ Rz(b); q = np.full(2, np.nan)
        for i in range(2):
            d = R @ g.p_up[i] - np.array([g.x_low, *g.m_yz[i]])
            K = (d @ d + g.r**2 - g.L**2) / (2 * g.r)      # d_y cos q + d_z sin q = K
            rho = np.hypot(d[1], d[2])
            if abs(K) <= rho:
                q[i] = np.arctan2(d[2], d[1]) + g.branch[i] * np.arccos(K / rho)
        return q

    def _B(self, i, qi):
        g = self.g
        return np.array([g.x_low, g.m_yz[i][0] + g.r * np.cos(qi), g.m_yz[i][1] + g.r * np.sin(qi)])

    def constraint(self, q, x):
        R = Rx(x[0]) @ Rz(x[1])
        return np.array([np.sum((R @ self.g.p_up[i] - self._B(i, q[i]))**2) - self.g.L**2 for i in range(2)])

    def jacobians(self, q, x, h=1e-7):
        Jq = np.zeros((2, 2)); Jx = np.zeros((2, 2))
        for k in range(2):
            e = np.zeros(2); e[k] = h
            Jq[:, k] = (self.constraint(q + e, x) - self.constraint(q - e, x)) / (2 * h)
            Jx[:, k] = (self.constraint(q, x + e) - self.constraint(q, x - e)) / (2 * h)
        return Jq, Jx

    def fk(self, q, x0=(0.0, 0.0), iters=30, tol=1e-12):
        x = np.array(x0, float)
        for _ in range(iters):
            _, Jx = self.jacobians(q, x)
            dx = np.linalg.solve(Jx, -self.constraint(q, x)); x += dx
            if np.linalg.norm(dx) < tol:
                break
        return x

    def transmission_margin(self, a, b):
        """曲柄-连杆夹角离 0°/180° 的最小距离(deg)。越接近 0 越接近奇异；建议工作在 >= 30°。"""
        q = self.ik(a, b)
        if not np.all(np.isfinite(q)):
            return np.nan
        R = Rx(a) @ Rz(b); m = []
        for i in range(2):
            B = self._B(i, q[i]); c = B - np.array([self.g.x_low, *self.g.m_yz[i]]); l = R @ self.g.p_up[i] - B
            t = np.degrees(np.arccos(c @ l / np.linalg.norm(c) / np.linalg.norm(l))); m.append(min(t, 180 - t))
        return min(m)


# ====================================================================== 对外接口
class NeckModel:
    def __init__(self, geometry: Geometry = None, calib: Calibration = None, min_margin_deg=30.0):
        self.par = ParallelPart(geometry or Geometry())
        self.cal = calib or Calibration()
        self.min_margin = min_margin_deg
        self.q_model_zero = np.r_[self.par.ik(0.0, 0.0), 0.0]   # 头部 rpy=0 时的模型角

    # ---- 单点
    def _rpy_to_motor_one(self, rpy):
        Rc = C_STD2CAD @ rpy_to_R(*rpy) @ C_STD2CAD.T
        a, b, psi = R_to_mech(Rc)
        q12 = self.par.ik(a, b)
        reachable = bool(np.all(np.isfinite(q12)))
        margin = self.par.transmission_margin(a, b) if reachable else np.nan
        q_model = np.r_[q12, psi]
        q_cmd = self.cal.sign * (q_model - self.q_model_zero) + self.cal.offset
        info = dict(mech=np.array([a, b, psi]), reachable=reachable, margin_deg=margin,
                    ok=reachable and margin >= self.min_margin)
        return q_cmd, info

    def rpy_to_motor(self, rpy, degrees=False):
        """rpy: (3,) 或 (N,3) = [roll, pitch, yaw]。
        返回 q: 同形状的 3 电机指令角（与输入同单位），info: dict 或 dict 列表。
        info['ok'] 为 False 表示不可达或传动角裕度低于 min_margin_deg，此时不要把该点下发给电机。"""
        arr = np.atleast_2d(np.asarray(rpy, float))
        if degrees:
            arr = np.radians(arr)
        out = []; infos = []
        for row in arr:
            q, info = self._rpy_to_motor_one(row); out.append(q); infos.append(info)
        out = np.array(out)
        if degrees:
            out = np.degrees(out)
        if np.ndim(rpy) == 1:
            return out[0], infos[0]
        return out, infos

    def motor_to_rpy(self, q_cmd, degrees=False, x0=(0.0, 0.0)):
        """电机读数 -> 头部 rpy（编码器反馈、离线分析用）。q_cmd: (3,) 或 (N,3)。"""
        arr = np.atleast_2d(np.asarray(q_cmd, float))
        if degrees:
            arr = np.radians(arr)
        out = []; x = np.array(x0, float)
        for row in arr:
            q_model = (row - self.cal.offset) / self.cal.sign + self.q_model_zero
            x = self.par.fk(q_model[:2], x0=x)                 # 用上一个点做初值，轨迹上连续
            Rc = mech_to_R(x[0], x[1], q_model[2])
            out.append(R_to_rpy(C_STD2CAD.T @ Rc @ C_STD2CAD))
        out = np.array(out)
        if degrees:
            out = np.degrees(out)
        return out[0] if np.ndim(q_cmd) == 1 else out


if __name__ == "__main__":
    np.set_printoptions(precision=3, suppress=True)
    m = NeckModel()
    tests = [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, -10, 0], [0, 0, 30],
             [8, -8, 0], [0, 15, 60], [5, -12, 40], [0, -25, 0], [15, -15, 0]]
    print(" roll pitch  yaw  |   q1(deg)  q2(deg)  q3(deg) | 裕度  ok  | 往返误差(deg)")
    for t in tests:
        q, info = m.rpy_to_motor(t, degrees=True)
        back = m.motor_to_rpy(q, degrees=True) if info["reachable"] else np.full(3, np.nan)
        print(f"{t[0]:5.0f}{t[1]:6.0f}{t[2]:6.0f}  | {q[0]:9.3f}{q[1]:9.3f}{q[2]:9.3f} |"
              f" {info['margin_deg']:5.1f} {str(info['ok']):5s} | {np.max(np.abs(back - t)):.1e}")
    # 轨迹（数组）用法
    traj = np.c_[10 * np.sin(np.linspace(0, 2 * np.pi, 200)), np.zeros(200), 30 * np.sin(np.linspace(0, 4 * np.pi, 200))]
    Q, infos = m.rpy_to_motor(traj, degrees=True)
    print("轨迹 200 点，全部可下发:", all(i["ok"] for i in infos), " 输出形状:", Q.shape)
