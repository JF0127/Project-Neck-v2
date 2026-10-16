"""Send min-jerk posture changes and speech gestures over UDP."""

from bisect import insort
import math
import random
import socket
import threading
import time
import unicodedata


def _min_jerk(x):
    x = min(1.0, max(0.0, x))
    return x * x * x * (10 + x * (-15 + 6 * x))


class Motion:
    def __init__(self, config):
        self.config = config
        self.random = random.Random()
        self.turn = self.base = self.end = self.home_at = None
        self.gestures, self.switches = [], []
        self.next_stamp = time.monotonic()
        self.idle_at = self.next_stamp + self.random.uniform(*config["idle"]["interval"])
        # Absolute start, initial (pitch, yaw), target, duration. Keep history for abort rewrites.
        self.moves = [(-math.inf, (0.0, 0.0), (0.0, 0.0), 1.0)]
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.thread = threading.Thread(target=self._run, name="speech-motion", daemon=True)
        self.thread.start()

    def on_timeline(self, turn, wall_base):
        with self.lock:
            if turn == self.turn:
                self.base = wall_base

    def on_event(self, event, turn, *args):
        with self.lock:
            now = time.monotonic()
            if event == "turn_start":
                self.moves = [move for move in self.moves if move[0] <= now]
                self.next_stamp = min(self.next_stamp, now)
                self.turn, self.rate = turn, args[0]
                self.last_speaking = -math.inf
                self.base = self.end = self.home_at = None
                self.audio_end = 0.0
                self.gestures, self.switches = [], []
            elif turn == self.turn:
                if event == "audio":
                    self.audio_end = max(self.audio_end, args[1] + len(args[0]) / (2 * self.rate))
                elif event == "text" and self.home_at is None:
                    self._text(*args)
                elif event == "turn_end" and self.home_at is None:
                    self.end = args[0]
                    if self.base is not None:
                        self.next_stamp = min(self.next_stamp, max(now, self.base + self.end - 0.003))
                elif event == "abort" and (self.home_at is None or now < self.home_at):
                    self._home(now)
                    self.next_stamp = min(self.next_stamp, now)

    def _base_pose(self, stamp):
        for start, origin, target, duration in reversed(self.moves):
            if start <= stamp:
                gain = _min_jerk((stamp - start) / duration)
                return tuple(a + (b - a) * gain for a, b in zip(origin, target))

    def _pose(self, stamp):
        pitch, yaw = self._base_pose(stamp)
        if self.base is not None and (self.home_at is None or stamp < self.home_at):
            tau = stamp - self.base + 0.003
            if 0 <= tau <= self.audio_end + 1e-9:
                nod, shake = self._gesture_pose(tau)
                pitch += nod
                yaw += shake
        return pitch, yaw

    def _move(self, stamp, target, duration=None, origin=None):
        origin = self._base_pose(stamp) if origin is None else origin
        if duration is None:
            cfg = self.config["transition"]
            duration = cfg["base_time"] + max(abs(b - a) for a, b in zip(origin, target)) / cfg["degrees_per_second"]
        self.moves = [move for move in self.moves if move[0] < stamp]
        self.moves.append((stamp, origin, target, duration))

    def _log_switch(self, stamp, reason, skipped=False):
        idle = reason == "idle" or self.turn is None or (
            self.home_at is not None and
            stamp >= self.home_at + self.config["transition"]["return_time"])
        turn = "idle" if idle else self.turn
        tau = "" if idle or self.base is None else f" tau={stamp - self.base + 0.003:.6f}"
        if skipped:
            fields = (f"yaw=None pitch=None duration=None skipped "
                      f"gap={stamp - self.last_speaking:.6f}")
        else:
            _, _, (pitch, yaw), duration = self.moves[-1]
            fields = f"yaw={yaw:.6f} pitch={pitch:.6f} duration={duration:.6f}"
        print(f"motion: switch turn={turn} reason={reason} t_mono={stamp:.6f}{tau} {fields}",
              flush=True)

    def _switch(self, stamp, mode, reason="idle"):
        cfg = self.config[mode]
        previous = next(move[2][1] for move in reversed(self.moves) if move[0] <= stamp)
        while True:
            yaw = self.random.uniform(*cfg["yaw"])
            if abs(yaw - previous) >= self.config["transition"]["min_yaw_change"]:
                break
        self._move(stamp, (self.random.uniform(*cfg["pitch"]), yaw))
        if mode == "speaking":
            self.last_speaking = stamp
        self._log_switch(stamp, reason)

    def _home(self, stamp):
        duration = self.config["transition"]["return_time"]
        # Ending a turn takes priority over gestures; return the full current pose smoothly.
        self._move(stamp, (0.0, 0.0), duration, self._pose(stamp))
        self._log_switch(stamp, "回中")
        self.home_at = stamp
        self.switches.clear()
        self.idle_at = stamp + duration + self.random.uniform(*self.config["idle"]["interval"])

    def _text(self, text, tau):
        cfg = self.config["gestures"]
        chars = [i for i, ch in enumerate(text)
                 if not ch.isspace() and not unicodedata.category(ch).startswith("P")]
        # Keep the initial sentence first when deferred cues land at the same time.
        insort(self.switches, (tau, "句首", tau == 0), key=lambda item: (item[0], not item[2]))
        for i, ch in enumerate(text):
            if ch in ",，":
                insort(self.switches,
                       (tau + sum(j < i for j in chars) * cfg["seconds_per_char"], "逗号", False),
                       key=lambda item: (item[0], not item[2]))
        if text.rstrip().endswith(("？", "?")):
            return
        head = chars[0] if chars else len(text)
        for kind in ("shake", "nod"):
            spec = cfg[kind]
            matches = [(text.find(word), -len(word), word)
                       for word in spec["keywords"] if word in text]
            matches += [(head, -len(word), word) for word in spec.get("prefixes", [])
                        if text.startswith(word, head)]
            if not matches:
                continue
            index, _, keyword = min(matches)
            tau_k = tau + sum(i < index for i in chars) * cfg["seconds_per_char"]
            start = max(0.0, tau_k - cfg["lead_time"])
            if self.gestures and start - self.gestures[-1][0] < cfg["min_interval"]:
                return
            self.gestures.append((start, kind))
            print(f"motion: gesture turn={self.turn} text={text!r} keyword={keyword!r} "
                  f"type={kind} tau_k={tau_k:.6f}", flush=True)
            return

    def _after_gestures(self, tau):
        for start, kind in self.gestures:
            spec = self.config["gestures"][kind]
            end = start + (len(spec["amplitudes"]) - 1) * spec["segment_duration"]
            if start <= tau < end:
                tau = end
        return tau

    def _gesture_pose(self, tau):
        pitch = yaw = 0.0
        for start, kind in self.gestures:
            spec = self.config["gestures"][kind]
            values = spec["amplitudes"]
            u = (tau - start) / spec["segment_duration"]
            segment = math.floor(u)
            if not 0 <= segment < len(values) - 1:
                continue
            x = u - segment
            blend = (_min_jerk(x) if kind == "shake" and segment in (0, len(values) - 2)
                     else (1 - math.cos(math.pi * x)) / 2)
            value = values[segment] + (values[segment + 1] - values[segment]) * blend
            if kind == "nod":
                pitch += value
            else:
                yaw += value
        return pitch, yaw

    def _send(self, now):
        lines = []
        while self.next_stamp <= now + 0.15:
            stamp = self.next_stamp
            if self.base is not None and self.home_at is None:
                tau = stamp - self.base + 0.003
                if self.end is not None and tau >= self.end:
                    self._home(self.base + self.end - 0.003)
                else:
                    while self.switches and self.switches[0][0] <= min(tau, self.audio_end):
                        start, reason, initial = self.switches.pop(0)
                        when = self.base + start - 0.003
                        if when - self.last_speaking < self.config["speaking"]["min_interval"]:
                            self._log_switch(when, reason, skipped=True)
                            continue
                        deferred = self._after_gestures(start)
                        if deferred > start:
                            insort(self.switches, (deferred, reason, initial),
                                   key=lambda item: (item[0], not item[2]))
                        else:
                            self._switch(when, "speaking", reason)
            if self.turn is None or self.home_at is not None:
                while self.idle_at <= stamp:
                    self._switch(self.idle_at, "idle")
                    self.idle_at += self.random.uniform(*self.config["idle"]["interval"])
            pitch, yaw = self._pose(stamp)
            lines.append(f"{stamp:.9f},0.000000,{pitch:.6f},{yaw:.6f}\n")
            self.next_stamp += 1 / 30
        if lines:
            self.socket.sendto("".join(lines).encode(), ("127.0.0.1", 9870))

    def _run(self):
        deadline = time.monotonic()
        while not self.closed.is_set():
            with self.lock:
                self._send(time.monotonic())
            deadline += 1 / 30
            self.closed.wait(max(0.0, deadline - time.monotonic()))

    def close(self):
        self.closed.set()
        self.thread.join()
        self.socket.close()
