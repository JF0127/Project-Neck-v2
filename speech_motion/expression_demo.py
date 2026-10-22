"""Demonstrate expression poses over the realtime UDP posture stream."""

import argparse
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "third_party/probe"))
from probe import probe

if __package__:
    from .render import _gesture_duration, render
else:
    from render import _gesture_duration, render


def main():
    library = json.loads(Path(__file__).with_name("expressions.json").read_text())
    expressions = library["expressions"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="+", default=list(expressions))
    parser.add_argument("--intensity", type=float, default=1.0)
    parser.add_argument("--side", type=int, default=1)
    parser.add_argument("--hold", type=float, default=3.0)
    parser.add_argument("--gap", type=float, default=2.0)
    parser.add_argument("--port", type=int, default=9870)
    args = parser.parse_args()

    events, duration = [], 0.0

    def add(name, hold):
        nonlocal duration
        spec = expressions[name]
        events.append({"t": duration, "type": "expr", "name": name,
                       "intensity": args.intensity, "side": args.side, "hold": hold})
        duration = (duration + spec["enter"] + _gesture_duration(spec.get("gesture"))
                    + hold)

    add("neutral", args.gap)
    for name in args.names:
        add(name, args.hold)
        add("neutral", args.gap)
    probe("demonstration_events", json.dumps(events, ensure_ascii=False))
    samples = render(events, duration, fps=30)
    for probe_sample in samples:
        probe("demonstration_pose_samples", probe_sample[1:].tolist())

    # Send samples 0.3 s ahead; print names at their actual playback times.
    actions = [(event["t"], event["name"], None) for event in events]
    actions += [(sample[0] - 0.3, None, sample) for sample in samples]
    start = time.monotonic() + 0.3
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for delay, name, sample in sorted(actions, key=lambda action: action[0]):
            time.sleep(max(0.0, start + delay - time.monotonic()))
            if name is not None:
                print(name, flush=True)
            else:
                t, roll, pitch, yaw = sample
                line = f"{start + t:.9f},{roll:.6f},{pitch:.6f},{yaw:.6f}\n"
                sock.sendto(line.encode(), ("127.0.0.1", args.port))
                probe("head_pose_udp_demo", [float(roll), float(pitch), float(yaw)])
        time.sleep(max(0.0, start + duration - time.monotonic()))


if __name__ == "__main__":
    main()
