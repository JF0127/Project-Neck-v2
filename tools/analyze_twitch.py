#!/usr/bin/env python3
"""Correlate dialogue UDP samples with a motor session CSV; no hardware access."""

import argparse
from bisect import bisect_left, bisect_right
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "motor/python"))
from neck.neck_calibration import MODEL_JOINTS, load_neck_model

AXES = ("roll", "pitch", "yaw")
NEAR_TARGET = 1 / 60  # Half a 30 Hz frame, not an ordinary adjacent sample.
ACTIONS = {"backchannel", "ack", "speak_gaze_away", "look_back"}


def event_name(name):
    return name if name in ACTIONS else f"expr:{name}"


def load_motion(path):
    samples, events = [], []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["kind"] == "send":
                samples.append([float(row[key]) for key in
                                ("at_mono_s", "t_mono", "roll_deg", "pitch_deg", "yaw_deg", "unix_s")])
            elif row["kind"] == "event":
                events.append((float(row["t_mono"]), event_name(row["event"])))
    values = np.asarray(samples, dtype=float)
    if len(values) < 2 or not np.all(np.isfinite(values)):
        raise ValueError("motion 日志至少需要两条完整、有限的 send 记录（含 unix_s）")
    return values[np.argsort(values[:, 0], kind="stable")], events


def dialogue_events(path):
    if path is None:
        return []
    events = []
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    for file in files:
        log = json.loads(file.read_text(encoding="utf-8"))
        for key in ("speech_start_at", "speech_end_at"):
            if log.get(key) is not None:
                events.append((float(log[key]), key))
        for key in ("expr_events", "action_events"):
            for event in log.get(key, []):
                if event.get("skipped"):
                    stamp = event.get("attempted_at")
                    label = f"skipped:{event['name']}({','.join(event.get('reasons', []))})"
                else:
                    stamp = event.get("trigger_at")
                    label = event_name(event["name"])
                if stamp is not None:
                    events.append((float(stamp), label))
        for i, segment in enumerate(log.get("segments", []), 1):
            if segment.get("playback_start_at") is not None:
                text = segment["text"].replace("\n", " ")
                events.append((float(segment["playback_start_at"]), f"play:{i}:{text}"))
    return events


def motor_times(main, sent):
    # C++ timestamp_unix_s is system_clock AFTER command preparation; elapsed_s
    # is CLOCK_MONOTONIC - session_epoch, captured slightly earlier in that tick.
    # Use fresh last_rx_mono_s anchors to select contemporaneous wall/mono offsets.
    offsets = sent[:, 5] - sent[:, 0]
    rx = main["last_rx_mono_s"]
    fresh = np.r_[True, rx[1:] != rx[:-1]]
    fresh &= (rx >= sent[0, 0]) & (rx <= sent[-1, 0]) & (rx > 0)
    if np.any(fresh):
        candidates = (main["timestamp_unix_s"][fresh] - main["elapsed_s"][fresh]
                      - np.interp(rx[fresh], sent[:, 0], offsets))
        method = "使用 last_rx_mono_s 更新行及同期 unix/monotonic 偏移"
    else:
        candidates = main["timestamp_unix_s"] - main["elapsed_s"] - np.median(offsets)
        method = "无重叠接收锚点，使用中位墙钟偏移估计；对齐仅供参考"
    candidates = candidates[np.isfinite(candidates)]
    if not len(candidates):
        raise ValueError("无法重建 motor 会话的单调时钟起点")
    epoch = float(np.median(candidates))
    residual = float(np.quantile(np.abs(candidates - epoch), 0.95))
    print(f"时钟对齐：{method}；epoch≈{epoch:.9f} mono s，95%锚点残差={residual * 1000:.3f} ms")
    print("注意：墙钟与单调时间并非同时采样，此对齐含命令准备/时间采样误差；日志须来自同机、同次开机。")
    if residual > 0.020:
        print("警告：时钟对齐残差超过20 ms，附近事件关联的时间精度不足。")
    if not np.any(fresh) and np.ptp(offsets) > 0.020:
        print("警告：发送端墙钟偏移发生明显变化，缺少接收锚点时不能可靠对齐。")
    return epoch + main["plan_time_s"], epoch + main["elapsed_s"]


def regular(t, pose, dt, max_gap):
    """Uniform-time samples; latest write wins for equal timestamps, no gap bridging."""
    valid = np.isfinite(t) & np.all(np.isfinite(pose), axis=1)
    t, pose = t[valid], pose[valid]
    order = np.argsort(t, kind="stable")
    t, pose = t[order], pose[order]
    if not len(t):
        return t, pose
    keep = np.r_[t[1:] != t[:-1], True]
    t, pose = t[keep], pose[keep]
    pose = np.degrees(np.unwrap(np.radians(pose), axis=0))
    if len(t) < 2:
        return t, pose
    grid = t[0] + np.arange(int((t[-1] - t[0]) / dt) + 1) * dt
    values = np.column_stack([np.interp(grid, t, col) for col in pose.T])
    right = np.clip(np.searchsorted(t, grid), 1, len(t) - 1)
    holes = ((t[right] - t[right - 1] > max_gap)
             & (grid > t[right - 1] + 1e-9) & (grid < t[right] - 1e-9))
    values[holes] = np.nan
    return grid, values


def angles(value):
    return ", ".join(f"{axis}={number:+.5f}°" for axis, number in zip(AXES, value))


def motor_log_snapshot(path, unix_range=None):
    """Read a fixed-length snapshot of a CSV that may still be growing."""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        remaining = stream.tell()
        stream.seek(0)
        line_number = 0
        while remaining:
            line = stream.readline(remaining)
            if not line:
                raise ValueError("motor CSV 在读取期间被截短，请在日志稳定后重试")
            remaining -= len(line)
            line_number += 1
            if not remaining and not line.endswith(b"\n"):
                print(f"注意：忽略 motor CSV 快照中尚未写完整的末行 #{line_number} "
                      f"({len(line.split(b','))} 列)；原始日志未修改。")
                break
            if line_number > 1 and unix_range is not None:
                stamp = float(line.split(b",", 1)[0])
                if not unix_range[0] <= stamp <= unix_range[1]:
                    continue
            yield line.decode("utf-8-sig")


def analyze(args):
    sent, events = load_motion(args.motion_log)
    events += dialogue_events(args.dialogue_log)
    # Merge the same expr/nod recorded by both the motion and dialogue log.
    events = sorted({(round(stamp, 6), label) for stamp, label in events})
    event_times = [event[0] for event in events]
    unix_range = None
    if args.motion_window:
        # Restrict expensive FK to this dialogue, not unrelated hours of motor idle.
        offset = sent[:, 5] - sent[:, 0]
        unix_range = (min(sent[0, 0], np.min(sent[:, 1])) + np.min(offset) - 1,
                      max(sent[-1, 0], np.max(sent[:, 1])) + np.max(offset) + 1)
        print("分析范围：motion 时间窗及前后各 1 秒；不统计会话中无关的长时间待机。")
    main = np.genfromtxt(motor_log_snapshot(args.motor_log, unix_range), delimiter=",", names=True,
                         dtype=None, encoding="utf-8-sig", ndmin=1)
    if len(main) < 3:
        raise ValueError("motor 主 CSV 至少需要三行；不要传入 _fb.csv")
    mt, ft = motor_times(main, sent)
    if not np.all(np.isfinite(mt)) or np.any(np.diff(mt) <= 0):
        raise ValueError("motor plan_time_s 必须有限且严格递增")
    plan = np.column_stack([main[f"{axis}_deg"] for axis in AXES])
    model, _, mapping = load_neck_model()
    ids = [next(motor for motor, joint in mapping.items() if joint == wanted)
           for wanted in MODEL_JOINTS]
    q = np.column_stack([main[f"motor{i}_feedback_position_deg"] for i in ids])
    feedback = np.full(q.shape, np.nan)
    valid = np.all(np.isfinite(q), axis=1)
    if np.any(valid):
        feedback[valid] = model.motor_to_rpy(q[valid], degrees=True)
    print("反馈：使用主 CSV 的电机位置快照，经 motor/config/neck_calibration.json 和正运动学换算 RPY；不把电机角当头部角。")
    print("规划：roll_deg/pitch_deg/yaw_deg（限幅后的下发规划）；target_* 在当前日志中是同值副本。")
    print("发送端阈值：发送间隔>0.1 s；未来点 |Δt_target|≤16.667 ms 且任一轴差>0.5°；30 Hz 二阶差分>2°。")
    print("近目标冲突只是疑似改写，输出目标时间差供辨别正常运动；不比较普通相隔33.333 ms的两个点。")
    anomalies = []
    for i in np.flatnonzero(np.diff(sent[:, 0]) > 0.1) + 1:
        anomalies.append((sent[i, 0], "sender_gap",
                          f"interval={sent[i, 0] - sent[i - 1, 0]:.6f} s; previous={sent[i - 1, 0]:.6f}"))

    future = []
    for row in sent:
        at, target = row[:2]
        future = [old for old in future if old[1] > at]
        if target <= at:
            continue  # The motor drops already-expired samples rather than rewriting its future.
        for old in future:
            delta = (row[2:5] - old[2:5] + 180) % 360 - 180
            if abs(target - old[1]) <= NEAR_TARGET and np.max(np.abs(delta)) > 0.5:
                anomalies.append((target, "sender_near_target_rewrite",
                                  f"sent_at={at:.6f}; previous_send={old[0]:.6f}; "
                                  f"previous_target={old[1]:.6f}; Δtarget={(target - old[1]) * 1000:.3f} ms; "
                                  f"Δpose: {angles(delta)}"))
        # Match the motor's replacement rule: new start replaces the old future tail.
        future = [old for old in future if old[1] < target]
        future.append(row)

    st, sp = regular(sent[:, 1], sent[:, 2:5], 1 / 30, 0.1)
    sd2 = np.diff(sp, n=2, axis=0)
    for i in np.flatnonzero(np.any(np.abs(sd2) > 2.0, axis=1)):
        anomalies.append((st[i + 1], "sender_second_difference", angles(sd2[i])))

    buffer_ms = main["buffer_ms"].astype(float)
    for i in np.flatnonzero(buffer_ms < 20):
        anomalies.append((mt[i], "motor_low_buffer",
                          f"buffer={buffer_ms[i]:.3f} ms; state={main['state'][i]}"))
    dt = float(np.median(np.diff(mt)))
    pt, pp = regular(mt, plan, dt, 5 * dt)
    pd2 = np.diff(pp, n=2, axis=0)
    finite = np.abs(pd2[np.all(np.isfinite(pd2), axis=1)])
    baseline = np.median(finite, axis=0) if len(finite) else np.zeros(3)
    mad = np.median(np.abs(finite - baseline), axis=0) if len(finite) else np.zeros(3)
    threshold = np.maximum(0.01, baseline + 8 * 1.4826 * mad)
    print(f"motor 二阶差分：按日志中位控制周期 {dt * 1000:.6f} ms 重采样；")
    print(f"  每轴阈值=max(0.01°, median(|Δ²|)+8×1.4826×MAD(|Δ²|))：{angles(threshold)}")
    print("  0.01°下限抑制数值噪声，MAD随本次数据背景变化；不跨越>5个控制周期的空洞。")
    for i in np.flatnonzero(np.any(np.abs(pd2) > threshold, axis=1)):
        anomalies.append((pt[i + 1], "motor_plan_second_difference", angles(pd2[i])))
    reference = np.column_stack([np.interp(ft, mt, col) for col in plan.T])
    error = (feedback - reference + 180) % 360 - 180
    selected = (ft >= mt[0]) & (ft <= mt[-1]) & np.any(np.abs(error) > 1.0, axis=1)
    for i in np.flatnonzero(selected):
        anomalies.append((ft[i], "motor_tracking_error", f"feedback-plan: {angles(error[i])}"))

    anomalies.sort(key=lambda item: item[0])
    print(f"\n异常共 {len(anomalies)} 处（每个超阈值采样均列出；二阶差分时间取三点中的中点，单位°而非°/s²）：")
    for stamp, kind, detail in anomalies:
        left, right = bisect_left(event_times, stamp - 1), bisect_right(event_times, stamp + 1)
        nearby = "; ".join(f"{label}@{t:.6f}({t - stamp:+.3f}s)" for t, label in events[left:right])
        print(f"{stamp:.6f} mono s | {kind} | {detail}" + (f" | events: {nearby}" if nearby else ""))

    if args.no_plot:
        print("\n已禁用图片输出；不生成分析文件。")
        return

    origin = min(float(np.min(sent[:, 1])), float(mt[0]))
    fig, axes = plt.subplots(4, 1, figsize=(16, 12), sharex=True)
    for ax, axis in zip(axes[:3], (1, 0, 2)):
        ax.plot(sent[:, 1] - origin, sent[:, axis + 2], ".-", lw=0.8, ms=2, label="Sent (target time)")
        ax.plot(mt - origin, plan[:, axis], lw=1, label="Motor plan")
        ax.plot(ft - origin, feedback[:, axis], lw=0.8, alpha=0.8, label="Motor feedback (FK)")
        ax.set_ylabel(f"{AXES[axis]} (deg)")
        ax.legend(loc="upper right")
    axes[3].plot(mt - origin, buffer_ms, label="Buffer remaining")
    axes[3].axhline(20, color="orange", linestyle="--", label="20 ms")
    axes[3].set_ylabel("Buffer (ms)")
    marks = np.unique([item[0] for item in anomalies]) - origin
    for ax in axes:
        low, high = ax.get_ylim()
        if len(marks):
            ax.vlines(marks, low, high, color="red", alpha=0.15, lw=0.5)
        ax.set_ylim(low, high)
        ax.grid(alpha=0.2)
    axes[3].legend(loc="upper right")
    axes[3].set_xlabel(f"Time since {origin:.6f} s (CLOCK_MONOTONIC); red lines = anomalies")
    fig.tight_layout()
    output = ROOT / "logs" / datetime.now().strftime("twitch_%Y%m%d_%H%M%S_%f.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)
    print(f"\n图已保存：{output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motion_log", type=Path, required=True)
    parser.add_argument("--motor_log", type=Path, required=True, help="motor 主运行 CSV，不是 _fb.csv")
    parser.add_argument("--dialogue_log", type=Path, help="单轮 JSON 或包含多轮 JSON 的目录")
    parser.add_argument("--motion_window", action="store_true", help="只分析 motion 时间窗及前后各 1 秒")
    parser.add_argument("--no_plot", action="store_true", help="只输出诊断，不写入图片文件")
    args = parser.parse_args()
    try:
        analyze(args)
    except (OSError, ValueError, KeyError, np.linalg.LinAlgError) as exc:
        parser.exit(1, f"分析失败：{exc}\n")


if __name__ == "__main__":
    main()
