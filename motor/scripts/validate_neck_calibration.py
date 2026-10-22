#!/usr/bin/env python3
"""仅离线校验标定和可用范围；任何不一致返回非零退出码。"""

import argparse
import builtins
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "third_party/probe"))
from probe import probe

# 允许从任意工作目录运行本脚本，不要求安装本项目为 Python 包。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))
from neck.neck_calibration import DEFAULT_CONFIG_PATH, MODEL_JOINTS, load_neck_model


ANGLE_ATOL_DEG = 1e-9  # 仅用于浮点换算/限位推导相等性，不放宽可用性判据。


def print(*args, **kwargs):
    builtins.print(*args, **kwargs)
    try:
        probe("calibration_result_report", " ".join(map(str, args)))
    except Exception:
        pass


def require(condition, message):
    if not condition:
        raise ValueError(message)


def symmetric_grid(half_span_deg, step_deg):
    require(np.isfinite(half_span_deg) and half_span_deg > 0, "扫描半宽必须为有限正数")
    require(np.isfinite(step_deg) and step_deg > 0, "扫描步长必须为有限正数")
    half_count = round(half_span_deg / step_deg)
    require(half_count > 0 and np.isclose(
        half_count * step_deg, half_span_deg, atol=ANGLE_ATOL_DEG, rtol=0
    ), "扫描半宽必须是步长的整数倍（确保包含零点和边界）")
    return np.arange(-half_count, half_count + 1, dtype=float) * step_deg


def usable_points(model, limits_deg, points_deg):
    for probe_row in np.atleast_2d(points_deg):
        probe("calibration_scan_points", probe_row.tolist())
    q_deg, infos = model.rpy_to_motor(points_deg, degrees=True)
    for probe_row, probe_info in zip(q_deg, infos):
        probe("calibration_scan_results", probe_row.tolist() + [
            float(probe_info["margin_deg"]), float(probe_info["reachable"]), float(probe_info["ok"])])
    return (
        np.array([info["ok"] for info in infos], dtype=bool)
        & np.all(np.isfinite(q_deg), axis=1)
        & np.all(q_deg >= limits_deg[:, 0], axis=1)
        & np.all(q_deg <= limits_deg[:, 1], axis=1)
    )


def square_boundary(half_span_deg, step_deg):
    # 四条边均采样；每个角点只保留一次；固定 yaw = 0。
    t = symmetric_grid(half_span_deg, step_deg)[:-1]
    side = np.full_like(t, half_span_deg)
    zero = np.zeros_like(t)
    return np.vstack([
        np.column_stack((t, -side, zero)),
        np.column_stack((side, t, zero)),
        np.column_stack((-t, side, zero)),
        np.column_stack((-side, -t, zero)),
    ])


def validate(config_path):
    model, limits_deg, id_to_model = load_neck_model(config_path)
    with config_path.open(encoding="utf-8") as stream:
        config = json.load(stream)
    by_joint = {motor["mapping"]["model_joint"]: motor for motor in config["motors"]}
    motors = [by_joint[joint] for joint in MODEL_JOINTS]
    margin_deg = config["soft_limits"]["margin_deg"]
    reference = config["validation"]
    step_deg = reference["scan_step_deg"]
    tolerance_deg = reference["range_tolerance_deg"]
    require(np.isfinite(tolerance_deg) and 0 < tolerance_deg <= 0.2,
            "范围校验容差必须在 (0, 0.2] 度内")
    require(0 < step_deg <= tolerance_deg, "扫描步长必须为正且不大于范围容差")

    print("Project-Neck 电机标定离线校验（不连接硬件）")
    print(f"配置: {config_path}")
    print(f"记录日期: {config['recorded_date']}")
    print("ID 映射: " + ", ".join(
        f"{motor_id} -> {id_to_model[motor_id]}" for motor_id in sorted(id_to_model)
    ))
    signs = np.array([motor["calibration"]["sign"] for motor in motors])
    require(np.array_equal(model.cal.sign, signs), "Calibration.sign 与配置不一致")
    require(model.min_margin == config["model"]["min_margin_deg"], "模型裕度阈值与配置不一致")
    print("Calibration.sign: [" + ", ".join(f"{sign:+.0f}" for sign in signs) + "]")
    print(f"模型 min_margin_deg: {model.min_margin:.1f}")

    expected_zero_deg = np.array([motor["calibration"]["offset_deg"] for motor in motors])
    probe("calibration_scan_points", [0.0, 0.0, 0.0])
    zero_deg, info = model.rpy_to_motor([0, 0, 0], degrees=True)
    probe("calibration_scan_results", zero_deg.tolist() + [
        float(info["margin_deg"]), float(info["reachable"]), float(info["ok"])])
    require(info["ok"], "rpy = 0 时模型不满足 ok")
    require(np.allclose(zero_deg, expected_zero_deg, atol=ANGLE_ATOL_DEG, rtol=0),
            f"零位不一致: 实际 {zero_deg}, 配置 {expected_zero_deg}")
    require(usable_points(model, limits_deg, [[0, 0, 0]])[0], "零位超出软限位")
    print("[PASS] rpy = [0, 0, 0] deg -> 电机角 [" +
          ", ".join(f"{angle:.6f}" for angle in zero_deg) + "] deg，等于配置零位")
    print("零位状态: " + ", ".join(
        f"ID {motor['id']}={motor['calibration']['offset_status']}" for motor in motors
    ))

    print(f"软限位检查: 机械范围每端收进 {margin_deg:.6f} deg")
    for index, motor in enumerate(motors):
        endpoints = [item["angle_deg"] for item in motor["mechanical_range"]["endpoints"]]
        low_deg, high_deg = min(endpoints), max(endpoints)
        lower_deg, upper_deg = limits_deg[index]
        # 独立检查两端实际收进量，而不复制读取函数的软限位构造代码。
        actual_margins_deg = [lower_deg - low_deg, high_deg - upper_deg]
        require(lower_deg < upper_deg and np.allclose(
            actual_margins_deg, [margin_deg, margin_deg], atol=ANGLE_ATOL_DEG, rtol=0
        ), f"ID {motor['id']} 软限位收进量不一致: {actual_margins_deg}")
        print(f"[PASS] ID {motor['id']} / {MODEL_JOINTS[index]}: "
              f"机械 [{low_deg:.6f}, {high_deg:.6f}] deg -> "
              f"软件 [{lower_deg:.6f}, {upper_deg:.6f}] deg")

    grid_deg = symmetric_grid(reference["axis_scan_half_span_deg"], step_deg)
    print(f"单轴扫描: [{grid_deg[0]:.2f}, {grid_deg[-1]:.2f}] deg，"
          f"步长 {step_deg:.2f} deg，其余轴为 0 deg，端点容差 {tolerance_deg:.2f} deg")
    print("可用判据: 模型 ok + 三个电机角均有限且不超软限位（包含端点）")
    for name, axis in (("pitch", 1), ("roll", 0), ("yaw", 2)):
        points_deg = np.zeros((len(grid_deg), 3))
        points_deg[:, axis] = grid_deg
        usable = usable_points(model, limits_deg, points_deg)
        require(usable[len(grid_deg) // 2], f"{name} 扫描零点不可用")
        require(not usable[0] and not usable[-1], f"{name} 扫描未包围可用范围")
        indices = np.flatnonzero(usable)
        require(np.all(np.diff(indices) == 1), f"{name} 可用区不连续")
        actual_deg = grid_deg[indices[[0, -1]]]
        expected_deg = np.asarray(reference["reference_ranges_deg"][name], dtype=float)
        require(expected_deg.shape == (2,) and np.all(np.isfinite(expected_deg)),
                f"{name} 参考范围必须为两个有限角度")
        error_deg = np.max(np.abs(actual_deg - expected_deg))
        require(error_deg <= tolerance_deg + ANGLE_ATOL_DEG,
                f"{name} 范围 {actual_deg} 与参考 {expected_deg} 不符，误差 {error_deg:.6f} deg")
        print(f"[PASS] 只动 {name}: [{actual_deg[0]:.2f}, {actual_deg[1]:.2f}] deg；"
              f"参考 [{expected_deg[0]:.1f}, {expected_deg[1]:.1f}] deg；"
              f"最大端点误差 {error_deg:.2f} deg")

    boundaries = reference["square_boundaries"]
    require(len(boundaries) == 2, "必须提供两组方形边界参考")
    for boundary in boundaries:
        span_deg = boundary["half_span_deg"]
        expected = boundary["expect_all_usable"]
        require(type(expected) is bool, "expect_all_usable 必须是布尔值")
        points_deg = square_boundary(span_deg, step_deg)
        usable = usable_points(model, limits_deg, points_deg)
        all_usable = bool(np.all(usable))
        require(all_usable == expected,
                f"roll/pitch ±{span_deg:g} deg 方形边界全部可用={all_usable}，预期={expected}")
        result = "全部可用" if all_usable else "不全部可用"
        print(f"[PASS] roll/pitch ±{span_deg:g} deg 方形四边，yaw=0 deg: "
              f"{np.count_nonzero(usable)}/{len(usable)} 点可用，{result}")
        if not all_usable:
            first_bad_deg = points_deg[np.flatnonzero(~usable)[0]]
            print("       首个不可用采样 rpy=[" +
                  ", ".join(f"{angle:.2f}" for angle in first_bad_deg) + "] deg")

    print("全部校验通过。注意：离线采样不代表耦合机械限位或硬件安全已经确认。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="标定 JSON 路径")
    args = parser.parse_args()
    try:
        validate(args.config.resolve())
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
