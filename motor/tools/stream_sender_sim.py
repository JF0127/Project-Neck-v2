#!/usr/bin/env python3
"""模拟头动生成器的流式输出，通过 UDP 发送给 NeckRpyStream。

每个样本一行：t_mono_s,roll_deg,pitch_deg,yaw_deg
t_mono_s = CLOCK_MONOTONIC 时间（Python 的 time.monotonic() 在 Linux 上即 CLOCK_MONOTONIC），
表示该姿态应到达的时刻 = T0 + tau，T0 = 本句开始时刻 + 延迟 D。

模拟方式：音频按实时到达，第 k 段（覆盖 tau ∈ [k·C, (k+1)·C)）在其最后一点音频到达后
再经过"计算耗时 + 随机抖动"才产出并发送。--stall 模拟生成器在某处卡住，
恢复后积压的段依次快速补发（追上实时）。注意 D 需大于 段长 C + 计算耗时 + 抖动，否则每段都会晚到。

示例：
  python3 stream_sender_sim.py 02_speech_gestures_100hz.csv                       # 正常
  python3 stream_sender_sim.py 02_speech_gestures_100hz.csv --stall 10 0.8         # 第 10 s 处生成器卡住 0.8 s，之后追上
  python3 stream_sender_sim.py 02_speech_gestures_100hz.csv --repeat 2 --gap 3     # 两句话，中间停 3 s
"""
import argparse, csv, random, socket, time


def load(path, out_hz):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append((float(r['t_s']), float(r['roll_deg']), float(r['pitch_deg']), float(r['yaw_deg'])))
    # 降采样到生成器输出帧率（线性插值）
    t_end = rows[-1][0]
    out, j, n = [], 0, int(t_end * out_hz) + 1
    for i in range(n):
        t = i / out_hz
        while j + 1 < len(rows) - 1 and rows[j + 1][0] < t:
            j += 1
        (t0, *a), (t1, *b) = rows[j], rows[j + 1]
        w = 0.0 if t1 == t0 else min(max((t - t0) / (t1 - t0), 0.0), 1.0)
        out.append((t, *[x + (y - x) * w for x, y in zip(a, b)]))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('file')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=9870)
    p.add_argument('--hz', type=float, default=30.0, help='生成器输出帧率')
    p.add_argument('--chunk', type=float, default=0.2, help='每段覆盖的音频时长 C (s)')
    p.add_argument('--delay', type=float, default=0.4, help='播放延迟 D (s)')
    p.add_argument('--compute', type=float, default=0.03, help='每段计算耗时 (s)')
    p.add_argument('--jitter', type=float, default=0.05, help='随机抖动上限 (s)')
    p.add_argument('--stall', type=float, nargs=2, metavar=('TAU', 'DUR'), help='在音频时间 TAU 处生成器卡住 DUR 秒')
    p.add_argument('--repeat', type=int, default=1, help='连续发送几句话')
    p.add_argument('--gap', type=float, default=3.0, help='句与句之间的静默 (s)')
    p.add_argument('--seed', type=int, default=0)
    a = p.parse_args()
    random.seed(a.seed)

    samples = load(a.file, a.hz)
    dur = samples[-1][0]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log = []

    start = time.monotonic() + 0.5
    for u in range(a.repeat):
        T0 = start + a.delay
        k, last_ready = 0, 0.0
        release = start + a.stall[0] + a.stall[1] if a.stall else None
        while k * a.chunk < dur + 1e-9:
            lo, hi = k * a.chunk, (k + 1) * a.chunk
            # 正常：该段音频到齐后，经计算耗时和抖动产出；生成器串行处理，卡住期间积压的段在恢复后依次快速处理
            ready = start + min(hi, dur) + a.compute + random.uniform(0, a.jitter)
            ready = max(ready, last_ready + a.compute)
            if release and hi > a.stall[0]:
                ready = max(ready, release + a.compute)
            last_ready = ready
            now = time.monotonic()
            if ready > now:
                time.sleep(ready - now)
            seg = [s for s in samples if lo <= s[0] < hi or (hi >= dur and s[0] >= lo)]
            msg = ''.join(f'{T0 + s[0]:.6f},{s[1]:.4f},{s[2]:.4f},{s[3]:.4f}\n' for s in seg)
            if msg:
                sock.sendto(msg.encode(), (a.host, a.port))
            sent = time.monotonic()
            margin = (T0 + seg[0][0] - sent) * 1000 if seg else float('nan')
            log.append((u, k, lo, hi, sent - start, margin))
            k += 1
        start = start + dur + a.gap

    late = [r for r in log if r[5] < 0]
    print(f'发送完成：{a.repeat} 句，共 {len(log)} 段；每段首样本距执行时刻的余量最小 {min(r[5] for r in log):.0f} ms，'
          f'中位 {sorted(r[5] for r in log)[len(log) // 2]:.0f} ms；晚到段数 {len(late)}')
    for u, k, lo, hi, s, m in late:
        print(f'  晚到：第 {u + 1} 句 第 {k} 段 tau {lo:.2f}-{hi:.2f} s，余量 {m:.0f} ms')


if __name__ == '__main__':
    main()
