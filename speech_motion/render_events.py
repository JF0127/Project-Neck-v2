"""Render an event JSON file to a stream_sender_sim-compatible CSV."""

import argparse
import json
from pathlib import Path

import numpy as np

if __package__:
    from .render import render
else:
    from render import render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Event JSON file")
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--out", required=True, help="Output trajectory CSV")
    args = parser.parse_args()
    data = json.loads(Path(args.file).read_text())
    samples = render(data["events"], data["duration"], fps=args.fps)
    np.savetxt(args.out, samples, delimiter=",", fmt=["%.9f", "%.6f", "%.6f", "%.6f"],
               header="t_s,roll_deg,pitch_deg,yaw_deg", comments="")


if __name__ == "__main__":
    main()
