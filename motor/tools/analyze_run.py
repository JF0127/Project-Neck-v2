#!/usr/bin/env python3
"""离线分析一次运行的主 CSV 及同名 _fb.csv。"""

import argparse
import builtins
from pathlib import Path
import re
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "third_party/probe"))
from probe import probe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from neck.neck_calibration import MODEL_JOINTS, load_neck_model

AXES = ("roll", "pitch", "yaw")


def print(*args, **kwargs):
    builtins.print(*args, **kwargs)
    try:
        probe("run_analysis_report", " ".join(map(str, args)))
    except Exception:
        pass


def spans(mask):
    return np.flatnonzero(np.diff(np.r_[False, mask, False])).reshape(-1, 2)


def lag_ms(target, actual, dt):
    if min(np.std(target), np.std(actual)) < 1e-6:
        return "N/A（无有效变化）"
    # FFT 计算去均值互相关；actual 相对 target 延迟时，峰值位于正滞后。
    n = 1 << (2 * len(target) - 1).bit_length()
    cross = np.fft.irfft(np.fft.rfft(actual - actual.mean(), n)
                        * np.fft.rfft(target - target.mean(), n).conj(), n)
    peak = int(np.argmax(cross))
    return f"{(peak if peak <= n // 2 else peak - n) * dt * 1000:.2f} ms"


def smooth20(values, dt):
    window = max(1, round(0.020 / dt))
    return np.column_stack([
        np.convolve(np.pad(col, (window // 2, (window - 1) // 2), mode="edge"),
                    np.ones(window) / window, mode="valid") for col in values.T])


def stream_summary(main, fb, model, ids):
    buffer_col = next((c for c in main.dtype.names
                       if c in ("buffer_s", "buffer_duration_s", "buffer_remaining_s",
                                "buffer_ms", "buffer_duration_ms", "buffer_remaining_ms")), None)
    state_col = next((c for c in ("state", "stream_state") if c in main.dtype.names), None)
    if buffer_col is None or state_col is None:
        return
    names = ("等待", "跟随", "外推减速", "保持", "过渡")
    aliases = ("wait waiting", "follow following tracking",
               "extrapolate_decel extrapolate extrapolating decel decelerating",
               "hold holding", "transition transitioning blend")
    labels = {alias: name for name, words in zip(names, aliases) for alias in words.split()}
    labels.update({str(i): name for i, name in enumerate(names)})
    states = np.array([labels.get(str(s).strip().lower().removesuffix(".0"), str(s))
                       for s in main[state_col]])
    t = main["elapsed_s"]
    starts = np.r_[0, np.flatnonzero(states[1:] != states[:-1]) + 1]
    remaining = main[buffer_col].astype(float) / (1000 if buffer_col.endswith("_ms") else 1)
    remaining = remaining[np.isfinite(remaining)]
    print(f"缓冲区剩余时长（{buffer_col}）：最小值={remaining.min():.6f} s，"
          f"中位数={np.median(remaining):.6f} s" if len(remaining) else "缓冲区剩余时长：N/A")
    print("状态区间（首行视为首次观测进入，末段截至日志末尾；数值状态 0..4 对应"
          "等待/跟随/外推减速/保持/过渡）：")
    events = []
    for a, b in zip(starts, np.r_[starts[1:], len(t) - 1]):
        print(f"  {states[a]}：进入 {t[a]:.6f} s，持续 {t[b] - t[a]:.6f} s")
        if states[a] in ("外推减速", "过渡"):
            events.append((t[a], states[a]))
    if not events:
        return

    # 使用全程反馈，避免裁掉等待/保持段内的切换窗口；先求导，再分别平滑 20 ms。
    ft = fb["timestamp_unix_s"] - (main["timestamp_unix_s"][0] - t[0])
    q = np.column_stack([fb[f"motor{i}_feedback_position_deg"] for i in ids])
    for probe_row in q:
        probe("logged_motor_angles", probe_row.tolist())
    probe_rpy = model.motor_to_rpy(q, degrees=True)
    for probe_row in probe_rpy:
        probe("reconstructed_head_angles", probe_row.tolist())
    rpy = np.degrees(np.unwrap(np.radians(probe_rpy), axis=0))
    dt = float(np.median(np.diff(ft)))
    grid = np.linspace(ft[0], ft[-1], round((ft[-1] - ft[0]) / dt) + 1)
    dt = grid[1] - grid[0]
    measured = np.column_stack([np.interp(grid, ft, col) for col in rpy.T])
    velocity = np.gradient(measured, dt, axis=0)
    speed = np.linalg.norm(smooth20(velocity, dt), axis=1)
    acceleration = np.linalg.norm(smooth20(np.gradient(velocity, dt, axis=0), dt), axis=1)
    print("切换前后各 0.5 s 的头部峰值（RPY 向量模长，各自 20 ms 平滑）：")
    for entered, state in events:
        selected = (grid >= entered - 0.5) & (grid <= entered + 0.5)
        coverage = "" if ft[0] <= entered - 0.5 and ft[-1] >= entered + 0.5 else "（反馈未覆盖完整窗口）"
        print(f"  {state} @ {entered:.6f} s，窗口 [{entered - 0.5:.6f}, {entered + 0.5:.6f}] s："
              f"最大速度={maximum(speed[selected])} °/s，"
              f"最大加速度={maximum(acceleration[selected])} °/s²{coverage}")

    # roll/pitch/yaw 是限幅后下发的 RPY；后向差分对应控制器的 v=Δp/dt、a=Δv/dt。
    # 在全程上差分后再按状态截取，保留进入过渡的首帧，不混入退出后的跟随样本。
    command = np.column_stack([main[f"{axis}_deg"] for axis in AXES])
    step = np.diff(t)[:, None]
    command_velocity = np.diff(command, axis=0) / step
    command_acceleration = np.r_[np.nan, np.nan, np.linalg.norm(
        np.diff(command_velocity, axis=0) / step[1:], axis=1)]
    print("过渡段内最大加速度（RPY 向量模长；下发目标按 elapsed_s 后向差分估算、"
          "不平滑；反馈采用上述 20 ms 平滑）：")
    for a, b in spans(states == "过渡"):
        end = t[b] if b < len(t) else t[-1]
        selected = (grid >= t[a]) & ((grid < end) if b < len(t) else (grid <= end))
        peaks = command_acceleration[a:b]
        peaks = peaks[np.isfinite(peaks)]
        check = ("超过" if peaks.max() > 1800 else "未超过") if len(peaks) else "无法判断是否超过"
        print(f"  {t[a]:.6f}–{end:.6f} s：下发目标={maximum(peaks)} °/s²，"
              f"反馈={maximum(acceleration[selected])} °/s²；目标{check} 1800 °/s²")


def tracking(main, fb, model, ids, period):
    prefix = "target_" if "target_roll_deg" in main.dtype.names else ""
    target = np.column_stack([main[f"{prefix}{axis}_deg"] for axis in AXES])
    plan = main["plan_time_s"]
    if not np.all(np.isfinite(target)):
        raise ValueError("目标 RPY 含无效数值")
    moving = np.any(np.abs(np.diff(target, axis=0)) > 1e-8, axis=1)
    holds = [(a, b) for a, b in spans(~moving)
             if plan[b] - plan[a] >= 5 - 2 * period]
    # 轨迹生成器先预定位，再保持 5 s；仅去掉外围保持，不删除轨迹内部停顿。
    changes = np.flatnonzero(moving)
    start = holds[0][1] if holds else len(main)
    end = changes[-1] + 1 if len(changes) else 0
    if end <= start:
        print("头部跟踪 / 卡顿：未识别到初始保持之后的轨迹执行段")
        return

    origin = main["timestamp_unix_s"][0] - main["elapsed_s"][0]
    mt = main["timestamp_unix_s"] - origin
    ft = fb["timestamp_unix_s"] - origin
    selected = (ft >= mt[start]) & (ft <= mt[end])
    ft = ft[selected]
    if len(ft) < 3:
        print("头部跟踪 / 卡顿：执行段反馈不足")
        return
    q = np.column_stack([fb[f"motor{i}_feedback_position_deg"][selected] for i in ids])
    if not np.all(np.isfinite(q)):
        raise ValueError("执行段电机反馈位置含无效数值")
    for probe_row in q:
        probe("logged_motor_angles", probe_row.tolist())
    actual = model.motor_to_rpy(q, degrees=True)
    for probe_row in actual:
        probe("reconstructed_head_angles", probe_row.tolist())
    if not np.all(np.isfinite(actual)):
        raise ValueError("运动学换算得到无效 RPY")
    actual = np.degrees(np.unwrap(np.radians(actual), axis=0))
    target = np.degrees(np.unwrap(np.radians(target), axis=0))
    reference = np.column_stack([np.interp(ft, mt, col) for col in target.T])
    error = (actual - reference + 180) % 360 - 180

    # 非等间隔反馈先重采样，供互相关及固定时间窗的速度平滑使用。
    dt = float(np.median(np.diff(ft)))
    t = np.arange(ft[0], ft[-1], dt)
    wanted = np.column_stack([np.interp(t, mt, col) for col in target.T])
    measured = np.column_stack([np.interp(t, ft, col) for col in actual.T])
    print(f"头部跟踪：执行段 {mt[start]:.6f}–{mt[end]:.6f} s；"
          f"反馈覆盖 {ft[0]:.6f}–{ft[-1]:.6f} s；滞后正值=反馈落后")
    for axis, err, goal, observed in zip(AXES, error.T, wanted.T, measured.T):
        k = np.argmax(np.abs(err))
        print(f"  {axis}: RMS={np.sqrt(np.mean(err ** 2)):.4f}°，"
              f"最大绝对误差={abs(err[k]):.4f}° @ {ft[k]:.6f} s，"
              f"滞后={lag_ms(goal, observed, dt)}")

    velocity = np.gradient(measured, dt, axis=0)
    smooth = smooth20(velocity, dt)
    target_speed = np.linalg.norm(np.gradient(wanted, dt, axis=0), axis=1)
    actual_speed = np.linalg.norm(smooth, axis=1)
    stalls = spans((target_speed > 5) & (actual_speed < 0.3 * target_speed))
    print("卡顿：RPY 速度向量模长，实际速度 20 ms 居中滑动平均；"
          f"目标 >5°/s 且实际 <目标的 30%，共 {len(stalls)} 段")
    for a, b in stalls:
        stop = min(t[0] + b * dt, ft[-1])
        print(f"  {t[a]:.6f}–{stop:.6f} s，持续 {stop - t[a]:.6f} s")


def maximum(values):
    values = values[np.isfinite(values)]
    return f"{values.max():.3f}" if len(values) else "N/A"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="主 CSV 路径（自动读取同名 _fb.csv）")
    path = parser.parse_args().csv
    try:
        fb_path = path.with_name(path.stem + "_fb.csv")
        main = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding="utf-8-sig", ndmin=1)
        fb = np.genfromtxt(fb_path, delimiter=",", names=True, encoding="utf-8-sig", ndmin=1)
        for data, name in ((main, path), (fb, fb_path)):
            times = data["timestamp_unix_s"]
            if len(times) < 2 or not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
                raise ValueError(f"{name}: 至少需要两行，且时间戳须有限、严格递增")
        elapsed, plan = main["elapsed_s"], main["plan_time_s"]
        if (not np.all(np.isfinite(elapsed + plan)) or np.any(np.diff(elapsed) <= 0)
                or np.any(np.diff(plan) < 0) or plan[-1] <= 0):
            raise ValueError("时间须有限：elapsed_s 严格递增，plan_time_s 非递减且末值为正")
        rate = re.search(r"_rate([^_]+)_\d+$", path.stem)
        steps = np.diff(plan)  # 等待/保持期间，计划时间可以不推进。
        period = 1 / float(rate[1]) if rate else float(np.median(steps[steps > 0]))
        if not np.isfinite(period) or period <= 0:
            raise ValueError("无法确定有效的设定周期")
        model, _, mapping = load_neck_model()
        joint_to_id = {joint: motor for motor, joint in mapping.items()}
        ids = [joint_to_id[joint] for joint in MODEL_JOINTS]

        cycles = np.diff(elapsed)
        overruns = np.flatnonzero(cycles > 1.5 * period)
        print(f"运行：{path}\n反馈：{fb_path}\n时刻单位为运行开始后的秒；N/A 表示无有效数据。")
        print(f"时序：设定周期={period * 1000:.3f} ms"
              f"（{'文件名 Rate' if rate else '由 plan_time_s 差分推定'}），"
              f"中位数={np.median(cycles) * 1000:.3f} ms，最大={cycles.max() * 1000:.3f} ms")
        print(f"  超过 1.5 倍设定周期：{len(overruns)} 次（时刻为周期结束时刻）")
        for i in overruns:
            print(f"    {elapsed[i + 1]:.6f} s：{cycles[i] * 1000:.3f} ms")
        print(f"  实际总时长 / 计划时长 = {elapsed[-1]:.6f} / {plan[-1]:.6f} s"
              f" = {elapsed[-1] / plan[-1]:.6f}"
              "（含保持；计划取末行 plan_time_s，中断日志仅代表已记录部分）")
        tracking(main, fb, model, ids, period)
        stream_summary(main, fb, model, ids)
        print("电机（全程，最大电流取绝对值）：")
        for i in sorted(ids):
            prefix = f"motor{i}_"
            codes = main[prefix + "error_code"]
            codes = ", ".join(str(int(x)) for x in np.unique(codes[np.isfinite(codes)])) or "N/A"
            current = np.r_[main[prefix + "feedback_current_A"], fb[prefix + "feedback_current_A"]]
            print(f"  电机 {i}: 错误码=[{codes}]，"
                  f"最高温度={maximum(main[prefix + 'temperature_degC'])} °C，"
                  f"最大电流={maximum(np.abs(current))} A")
    except (OSError, ValueError, ZeroDivisionError, np.linalg.LinAlgError) as exc:
        probe("run_analysis_report", f"分析失败：{exc}")
        parser.exit(1, f"分析失败：{exc}\n")


if __name__ == "__main__":
    main()
