#!/usr/bin/env python3
"""从多次 NeckSweepMit 的 _fb.csv 建立输出轴力矩波动补偿表。"""

import argparse
import builtins
import json
from pathlib import Path
import re
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "third_party/probe"))
from probe import probe

STEP, PERIOD, BINS = 0.02, 10.0, 500
SPEED_STEP = np.degrees(36 / 4095)  # MIT 指令速度量化；零速解码约为 -0.252°/s。


def print(*args, **kwargs):
    builtins.print(*args, **kwargs)
    try:
        probe("ripple_build_report", " ".join(map(str, args)))
    except Exception:
        pass


def spans(mask):
    return np.flatnonzero(np.diff(np.r_[False, mask, False])).reshape(-1, 2)


def residuals(data, prefix, sign):
    velocity = np.degrees(data[prefix + "active_cmd_spd_rad_s"])
    command = data[prefix + "active_cmd_position_deg"]
    angle = data[prefix + "feedback_position_deg"]
    current = data[prefix + "feedback_current_A"]
    time = data["timestamp_unix_s"]
    valid = np.isfinite(velocity + command + angle + current + time)
    for a, b in spans(valid & (sign * velocity > SPEED_STEP)):
        q = sign * command[a:b]
        if np.any(np.diff(q) < -1e-6):
            continue
        grid = np.arange(q[0], q[-1], STEP)
        if len(grid) < 51:
            continue
        speeds = np.interp(grid, q, sign * velocity[a:b])
        windows = np.lib.stride_tricks.sliding_window_view(speeds, 51)
        # 指令速度在局部 1° 内变化不超过 10%（至少容许一个协议量化步长）。
        steady = np.ptp(windows, axis=1) <= np.maximum(0.1 * windows.mean(axis=1), 1.01 * SPEED_STEP)
        steady = np.interp(q, grid[25:-25], steady.astype(float), left=0, right=0) == 1
        order = np.argsort(angle[a:b], kind="stable")
        x, amps = angle[a:b][order], current[a:b][order]
        lo, hi = np.searchsorted(x, x - 0.5), np.searchsorted(x, x + 0.5, side="right")
        sums = np.r_[0, np.cumsum(amps)]
        ripple = amps - (sums[hi] - sums[lo]) / (hi - lo)
        keep = steady[order] & (x >= x[0] + 0.5) & (x <= x[-1] - 0.5)
        if np.any(keep):
            yield np.column_stack((time[a:b][order][keep], x[keep], ripple[keep]))


def table(samples):
    # 格中心为 k*0.02°，第 0 格跨越周期边界，与运行时插值节点一致。
    index = (np.floor((samples[:, 1] % PERIOD) / STEP + 0.5).astype(int) % BINS)
    count = np.bincount(index, minlength=BINS)
    bins = np.flatnonzero(count)
    if len(bins) < 2:
        raise ValueError("有效相位格不足，需增加匀速往返数据")
    mean = np.bincount(index, weights=samples[:, 2], minlength=BINS)[bins] / count[bins]
    return np.interp(np.arange(BINS), bins, mean, period=BINS) / 2.19


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="+", type=Path, help="NeckSweepMit 的 _fb.csv（可多个），或一份原始表 JSON")
    parser.add_argument("--harmonics-only", action="store_true", help="仅保留每 10° 的 7、14、28 次傅里叶分量，并另存原始表")
    args = parser.parse_args()
    try:
        samples, motors = {}, {}
        for path in args.csv:
            if path.suffix.lower() == ".json":
                if len(args.csv) != 1:
                    raise ValueError("原始表 JSON 必须单独输入")
                motors = json.loads(path.read_text())["motors"]
                break
            data = np.genfromtxt(path, delimiter=",", names=True, encoding="utf-8-sig", ndmin=1)
            ids = [m[1] for name in data.dtype.names
                   if (m := re.fullmatch(r"motor(\d+)_active_cmd_spd_rad_s", name))]
            if not ids:
                raise ValueError(f"{path}: 缺少 MIT 指令速度列")
            for motor in ids:
                for direction, sign in (("positive", 1), ("negative", -1)):
                    samples.setdefault((motor, direction), []).extend(residuals(data, f"motor{motor}_", sign))
        for (motor, direction), chunks in sorted(samples.items()):
            if not chunks:
                raise ValueError(f"motor{motor} {direction}: 无有效匀速数据")
            rows = np.concatenate(chunks)
            rows = rows[np.argsort(rows[:, 0], kind="stable")]
            values = table(rows)
            first, last = table(rows[:len(rows) // 2]), table(rows[len(rows) // 2:])
            corr = np.corrcoef(first, last)[0, 1] if min(first.std(), last.std()) > 1e-12 else float("nan")
            print(f"motor{motor} {direction}: 原始峰峰值={np.ptp(values):.6f} Nm，"
                  f"前/后半相关系数={corr:.4f}，样本数={len(rows)}")
            motors.setdefault(motor, {})[direction] = values.round(9).tolist()
        output = Path(__file__).resolve().parents[1] / "config/ripple_comp.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.harmonics_only:
            raw_output = output.with_name("ripple_comp_raw.json")
            raw_output.write_text(json.dumps({"motors": motors}, ensure_ascii=False, allow_nan=False) + "\n")
            print(f"原始表已写入 {raw_output}")
            for motor, directions in motors.items():
                for direction, values in directions.items():
                    values = np.asarray(values, dtype=float)
                    if values.shape != (BINS,) or not np.all(np.isfinite(values)):
                        raise ValueError(f"motor{motor} {direction}: 表必须包含 {BINS} 个有限数值")
                    spectrum = np.fft.rfft(values)
                    spectrum[~np.isin(np.arange(len(spectrum)), (7, 14, 28))] = 0
                    filtered = np.fft.irfft(spectrum, n=BINS)
                    directions[direction] = filtered.tolist()
                    print(f"motor{motor} {direction}: 筛选后峰峰值={np.ptp(filtered):.6f} Nm")
        output.write_text(json.dumps({"motors": motors}, ensure_ascii=False, allow_nan=False) + "\n")
        print(f"已写入 {output}")
    except (OSError, ValueError, KeyError) as exc:
        probe("ripple_build_report", f"建表失败：{exc}")
        parser.exit(1, f"建表失败：{exc}\n")


if __name__ == "__main__":
    main()
