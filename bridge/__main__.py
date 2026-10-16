import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
import wave

from . import Bridge


def main():
    parser = argparse.ArgumentParser(description="Receive BoardBridge events and save per-turn WAVs.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", type=Path, default=Path("bridge_wavs"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    files = {}

    def callback(event, turn, *values):
        record = {"event": event, "turn": turn, "t": time.monotonic()}
        if event == "turn_start":
            path = args.output / f"turn_{turn}_{time.time_ns()}.wav"
            wav = files[turn] = wave.open(str(path), "wb")
            wav.setparams((1, 2, values[0], 0, "NONE", "not compressed"))
            record.update(rate=values[0], wav=str(path))
        elif event == "audio":
            files[turn].writeframesraw(values[0])
            record.update(bytes=len(values[0]), tau_start=values[1])
        elif event == "text":
            record.update(text=values[0], tau=values[1])
        else:
            wav = files.pop(turn, None)
            if wav:
                wav.close()
            if event == "turn_end":
                record["tau_end"] = values[0]
        print(json.dumps(record, ensure_ascii=False), flush=True)

    print(f"bridge: listening on {args.host}:{args.port}; WAVs: {args.output}", file=sys.stderr)
    try:
        asyncio.run(Bridge(callback).serve(args.host, args.port))
    except KeyboardInterrupt:
        pass
    finally:
        for wav in files.values():
            wav.close()


if __name__ == "__main__":
    main()
