"""Play bridge audio on a fixed, delayed timeline using sounddevice."""

import argparse
import asyncio
from collections import deque
import json
from pathlib import Path
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from .motion import Motion


class _Audio:
    def __init__(self, turn, rate, events):
        self.turn, self.rate, self.events = turn, rate, events
        self.blocks = deque()
        self.lock = threading.Lock()
        self.base = None
        self.first_arrival = None
        self.total_frames = self.underruns = 0
        self.starved = self.played = self.ended = False
        self.stream = sd.OutputStream(
            samplerate=rate, channels=1, dtype="float32", latency="low",
            callback=self._play,
            finished_callback=lambda: events.put((time.monotonic(), "_finished", (self,))),
        )
        before = time.monotonic()
        audio_clock = self.stream.time
        self.clock_offset = (before + time.monotonic()) / 2 - audio_clock
        self.stream.start()

    def _play(self, out, frames, timing, status):
        out.fill(0)
        dac = timing.outputBufferDacTime + self.clock_offset
        pos = 0
        with self.lock:
            while pos < frames and self.blocks:
                wall, pcm, used = self.blocks[0]
                left = max(pos, round((wall - dac) * self.rate) + used)
                if left >= frames:
                    break
                count = min(frames - left, len(pcm) - used)
                out[left:left + count, 0] = pcm[used:used + count]
                pos = left + count
                if not self.played:
                    # First sample's DAC time, converted to the monotonic clock.
                    self.events.put((time.monotonic(), "_started", (
                        self, wall, dac + left / self.rate, timing.outputBufferDacTime,
                        timing.outputBufferDacTime - timing.currentTime, self.stream.latency,
                    )))
                self.played = True
                self.starved = False
                if used + count == len(pcm):
                    self.blocks.popleft()
                else:
                    self.blocks[0] = (wall, pcm, used + count)
            if not self.blocks:
                if self.ended:
                    raise sd.CallbackStop  # Drain the final output before closing.
                if self.played and pos < frames and not self.starved:
                    self.starved = True
                    self.underruns += 1
                    self.events.put((time.monotonic(), "_underrun", (self,)))

    def close(self):
        self.stream.abort()
        stopped_at = time.monotonic()
        self.stream.close()
        self.blocks.clear()
        return stopped_at


class Player:
    def __init__(self, config):
        self.delay = float(config["D"])
        self.motion = Motion(config["motion"])
        self.events = queue.Queue()
        self.audio = None
        self.thread = threading.Thread(target=self._run, name="speech-motion-player", daemon=True)
        self.thread.start()

    def on_event(self, event, *args):
        self.events.put((time.monotonic(), event, args))

    def _stop(self, finished_at=None, abort_at=None):
        audio, self.audio = self.audio, None
        if audio:
            stopped_at = audio.close()
            if abort_at is not None:
                print(f"player: abort turn={audio.turn} received_at={abort_at:.6f} "
                      f"stopped_at={stopped_at:.6f} stop_delay={stopped_at - abort_at:.6f}",
                      flush=True)
            ended_at = stopped_at if finished_at is None else finished_at
            print(f"player: end turn={audio.turn} audio_duration={audio.total_frames / audio.rate:.6f} "
                  f"underruns={audio.underruns} playback_end={ended_at:.6f}", flush=True)

    def _run(self):
        try:
            while True:
                arrival, event, args = self.events.get()
                if event is None:
                    break
                try:
                    if event == "turn_start":
                        self._stop()
                        self.audio = _Audio(args[0], args[1], self.events)
                    elif event == "_started":
                        audio, planned, started, dac_time, output_latency, stream_latency = args
                        print(f"player: start turn={audio.turn} first_arrival={audio.first_arrival:.6f} "
                              f"planned_start={planned:.6f} actual_start={started:.6f} "
                              f"outputBufferDacTime={dac_time:.6f} callback_at={arrival:.6f} "
                              f"output_latency={output_latency:.6f} stream_latency={stream_latency:.6f} "
                              f"start_delay={started - audio.first_arrival:.6f}", flush=True)
                    elif event in ("_finished", "_underrun"):
                        if args[0] is self.audio:
                            if event == "_finished":
                                self._stop(finished_at=arrival)
                            else:
                                print(f"player: underrun turn={self.audio.turn}", flush=True)
                    elif self.audio and args[0] == self.audio.turn:
                        audio = self.audio
                        if event == "audio":
                            pcm = np.frombuffer(args[1], dtype="<i2").astype(np.float32) / 32768
                            with audio.lock:
                                if audio.base is None:
                                    audio.first_arrival = arrival
                                    audio.base = arrival + self.delay
                                    self.motion.on_timeline(audio.turn, audio.base)
                                audio.total_frames += len(pcm)
                                audio.blocks.append((audio.base + args[2], pcm, 0))
                        elif event == "turn_end":
                            with audio.lock:
                                audio.ended = True
                        elif event == "abort":
                            self._stop(abort_at=arrival)
                    if event in ("turn_start", "audio", "text", "turn_end", "abort"):
                        self.motion.on_event(event, *args)
                except Exception as exc:
                    self._stop()
                    print(f"player: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            self._stop()

    def close(self):
        self.on_event(None)
        self.thread.join()
        self.motion.close()


def main():
    from bridge import Bridge

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name("config.json").read_text())
    player = Player(config)
    print(f"player: listening on {args.host}:{args.port}", file=sys.stderr)
    try:
        asyncio.run(Bridge(player.on_event).serve(args.host, args.port))
    except KeyboardInterrupt:
        pass
    finally:
        player.close()


if __name__ == "__main__":
    main()
