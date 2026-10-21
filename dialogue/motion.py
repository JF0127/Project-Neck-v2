"""Realtime expression scheduling on the existing UDP posture stream."""

from bisect import bisect_right
import csv
from datetime import datetime
import json
import math
import multiprocessing as mp
from pathlib import Path
import queue
import random
import signal
import socket
import threading
import time

from speech_motion.render import _min_jerk, render


class IdleBreath:
    def __init__(self, config, started_at):
        self.config = config
        self.cycles = [started_at]
        self.fades = []
        self.random = random.Random()

    def set_speaking(self, stamp, speaking):
        # A stop/abort also replaces any not-yet-effective playback state.
        self.fades = [change for change in self.fades if change[0] < stamp]
        self.fades.append((stamp, 0.0 if speaking else 1.0))

    def offset(self, stamp):
        cfg = self.config
        if not cfg["enabled"] or stamp < self.cycles[0]:
            return 0.0, 0.0
        # Cache each cycle once, including skipped cycles; sampling never resamples it.
        while len(self.cycles) < 2 or stamp >= self.cycles[-1]:
            period = cfg["period"] * self.random.uniform(
                1 - cfg["period_jitter"], 1 + cfg["period_jitter"])
            self.cycles.append(self.cycles[-1] + period)
        index = bisect_right(self.cycles, stamp) - 1
        u = (stamp - self.cycles[index]) / (self.cycles[index + 1] - self.cycles[index])
        weight = target = 1.0
        changed_at = self.cycles[0]
        fade = max(cfg["fade"], 1e-6)
        for at, next_target in self.fades:
            if at > stamp:
                break
            weight += (target - weight) * _min_jerk((at - changed_at) / fade)
            changed_at, target = at, next_target
        weight += (target - weight) * _min_jerk((stamp - changed_at) / fade)
        yaw_axis = cfg.get("axis", "pitch") == "yaw"
        wave = math.sin(2 * math.pi * u) if yaw_axis else (1 - math.cos(2 * math.pi * u)) / 2
        offset = cfg["amp"] * wave * weight
        pitch = cfg.get("pitch_offset", 0.0) * weight
        return (pitch, offset) if yaw_axis else (pitch + offset, 0.0)


def _run(commands, stopped, started_at, start_ramp, library):
    # The parent handles Ctrl+C and asks this process to close its resources.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    config = library["conversation"]
    breath = IdleBreath(config["idle_breath"], started_at) if config["idle_breath"]["enabled"] else None
    events, pending_emphasis = [], []
    emphasized_turns = set()
    last_sent_target = last_pose_at = float("-inf")
    current_pose, last_phrase_yaw = "center", 0
    directory = Path(__file__).resolve().parents[1] / "logs/motion"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / datetime.now().strftime("%Y%m%d_%H%M%S_%f.csv")
    with path.open("w", newline="", encoding="utf-8") as log_file, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        writer = csv.writer(log_file)
        # t_mono is the effective event time; requested_t_mono preserves the request.
        writer.writerow(("kind", "at_mono_s", "t_mono", "roll_deg", "pitch_deg", "yaw_deg",
                         "event", "unix_s", "requested_t_mono", "playback_t_mono",
                         "planned_lead_s", "actual_lead_s", "pose_name", "status", "reason"))
        print(f"Motion CSV: {path}", flush=True)

        def record_event(event, name, status="scheduled", reason=""):
            requested = event["requested_at"]
            pose = event["pose"] if name == "phrase_shift" else ("", "", "")
            playback_at = event.get("playback_at")
            timing = (("", "", "") if playback_at is None else
                      tuple(f"{value:.9f}" for value in
                            (playback_at, playback_at - requested, playback_at - event["t"])))
            if status == "skipped":
                timing = (*timing[:2], "")
            writer.writerow(("event", f"{time.monotonic():.9f}",
                             "" if status == "skipped" else f"{event['t']:.9f}",
                             *pose, name, "", f"{requested:.9f}", *timing,
                             event.get("pose_name", ""), status, reason))
            log_file.flush()

        def pitch_conflict(stamp):
            window = config["emphasis"]["clear_window"]
            low, high, pitch = stamp - window, stamp + window, 0.0
            for other in sorted(events, key=lambda item: item["t"]):
                start, gesture = other["t"], None
                factor = other.get("intensity", 1.0)
                if other["type"] == "expr":
                    spec = library["expressions"][other["name"]]
                    target = other.get("pose", spec["pose"])[1] * factor
                    enter = other.get("enter", spec["enter"])
                    if target != pitch and start <= high and start + enter >= low:
                        return True
                    pitch = target
                    gesture = spec.get("gesture")
                    start += enter
                elif other["type"] == "nod":
                    gesture = other
                if gesture and gesture["type"] == "nod" and gesture["amp"] * factor != 0:
                    end = start + 2 * gesture["count"] * gesture["half_period"]
                    if start <= high and end >= low:
                        return True
            return False

        def add_event(event, name):
            nonlocal last_pose_at, current_pose, last_phrase_yaw
            event["requested_at"] = event["t"]
            event["t"] = max(event["t"], last_sent_target + 1 / 30)
            if event["type"] == "reset":
                # Freeze the current pose, discarding all old gestures and future events.
                shifted = [{**old, "t": old["t"] - event["t"]} for old in events]
                pose = render(shifted, duration=0)[0][1:].tolist()
                events[:] = [{"t": event["t"] - 1.0, "type": "expr", "name": "neutral",
                              "pose": pose, "enter": 1e-6, "hold": float("inf")}]
                for pending in pending_emphasis:
                    record_event(pending, "emphasis", "skipped", "state_changed")
                pending_emphasis.clear()
                if breath is not None:
                    breath.set_speaking(event["t"], False)
                event["type"] = "expr"
            if event["type"] == "idle_breath":
                if breath is not None:
                    breath.set_speaking(event["t"], event["speaking"])
                    record_event(event, name)
                return
            if name == "emphasis":
                pending_emphasis.append(event)
                return
            cfg = config["phrase_shift"]
            if name == "phrase_shift":
                if event["t"] - last_pose_at < cfg["min_interval"]:
                    return
                choices = [key for key in cfg["poses"] if key != current_pose]
                preferred = [key for key in choices if cfg["poses"][key][2] == 0
                             or last_phrase_yaw == 0 or cfg["poses"][key][2] * last_phrase_yaw < 0]
                current_pose = random.choice(preferred or choices)
                event["pose_name"] = current_pose
                event["pose"] = [angle * random.uniform(1 - cfg["jitter"], 1 + cfg["jitter"])
                                 for angle in cfg["poses"][current_pose]]
                last_phrase_yaw = event["pose"][2]
            elif event["type"] == "expr":
                pose = event.get("pose", library["expressions"][event["name"]]["pose"])
                pose = [angle * event.get("intensity", 1.0)
                        * (1 if axis == 1 else event.get("side", 1))
                        for axis, angle in enumerate(pose)]
                current_pose = next((key for key, value in cfg["poses"].items() if value == pose), None)
            if event["type"] == "expr":
                last_pose_at = max(last_pose_at, event["t"])
            events.append(event)
            record_event(event, name)

        add_event({"t": started_at, "type": "expr", "name": "neutral",
                   "enter": max(start_ramp, 1e-6), "hold": float("inf")}, "neutral")
        deadline = started_at
        while not stopped.wait(max(0.0, deadline - time.monotonic())):
            while True:
                try:
                    event, name = commands.get_nowait()
                except queue.Empty:
                    break
                add_event(event, name)
            stamp = time.monotonic() + 0.15
            # Decide only before committing the gesture to the outgoing timeline,
            # so already scheduled future pitch changes also participate.
            for event in pending_emphasis[:]:
                if event["t"] > stamp:
                    continue
                cfg = config["emphasis"]
                reason = ("disabled" if not cfg["enabled"] else
                          "zero_amplitude" if cfg["amp"] == 0 else
                          "reply_limit" if event["reply_id"] in emphasized_turns else
                          "pitch_conflict" if pitch_conflict(event["t"]) else "")
                if not reason:
                    events.append(event)
                    emphasized_turns.add(event["reply_id"])
                record_event(event, "emphasis", "skipped" if reason else "executed", reason)
                pending_emphasis.remove(event)
            # Shift the timeline: render's sole sample at zero is our lookahead.
            shifted = [{**event, "t": event["t"] - stamp} for event in events]
            _, roll, pitch, yaw = render(shifted, duration=0)[0]
            if breath is not None:
                pitch_offset, yaw_offset = breath.offset(stamp)
                pitch += pitch_offset
                yaw = min(30.0, max(-30.0, yaw + yaw_offset))
            ramp = _min_jerk((stamp - started_at) / max(start_ramp, 1e-6))
            pitch = min(8.0, max(-8.0, pitch + config.get("base_pitch", 0.0) * ramp))
            line = f"{stamp:.9f},{roll:.6f},{pitch:.6f},{yaw:.6f}\n"
            udp.sendto(line.encode(), ("127.0.0.1", 9870))
            last_sent_target = max(last_sent_target, stamp)
            sent_at, unix_s = time.monotonic(), time.time()
            writer.writerow(("send", f"{sent_at:.9f}", f"{stamp:.9f}", f"{roll:.6f}",
                             f"{pitch:.6f}", f"{yaw:.6f}", "", f"{unix_s:.9f}",
                             "", "", "", "", "", "", ""))
            log_file.flush()
            delay = sent_at - deadline
            if delay > 0.1:
                print(f"dialogue-motion: WARNING: UDP send delayed {delay:.3f} s", flush=True)
            # Stay on the absolute 30 Hz grid; skip missed ticks instead of bursting.
            now = time.monotonic()
            deadline = started_at + (int((now - started_at) * 30) + 1) / 30
        for event in pending_emphasis:
            record_event(event, "emphasis", "skipped", "motion_stopped")


class Motion:
    def __init__(self, start_ramp=2.0):
        path = Path(__file__).resolve().parents[1] / "speech_motion/expressions.json"
        self.library = json.loads(path.read_text())
        self.config = self.library["conversation"]
        self.start_ramp = max(0.0, float(start_ramp))
        self.started_at = None
        self.next_side = 1
        self.lock = threading.Lock()
        self.context = mp.get_context("spawn")
        self.stopped = self.context.Event()
        self.commands = self.context.Queue()
        self.process = None

    def _add_event(self, event, name=None, playback_at=None):
        if playback_at is not None:
            event["playback_at"] = playback_at
        self.commands.put((event, name or event["name"]))

    def start(self):
        """Start once, then let the neutral-to-neutral min-jerk ramp finish."""
        with self.lock:
            if self.process is None:
                self.started_at = time.monotonic()
                self.process = self.context.Process(
                    target=_run, name="dialogue-motion",
                    args=(self.commands, self.stopped, self.started_at, self.start_ramp, self.library))
                self.process.start()
        self.stopped.wait(max(0.0, self.started_at + self.start_ramp - time.monotonic()))

    def _take_side(self):
        side = self.next_side
        self.next_side = -side
        return side

    def set_speaking(self, t_abs, speaking):
        cfg = self.config["idle_breath"]
        if not cfg["enabled"]:
            return None
        name = "idle_breath_fade_out" if speaking else "idle_breath_fade_in"
        with self.lock:
            self._add_event({"t": t_abs, "type": "idle_breath", "speaking": speaking}, name)
        return {"weight": 0.0 if speaking else 1.0, "fade_duration": cfg["fade"]}

    def reset(self, t_abs, name="neutral"):
        with self.lock:
            self._add_event({"t": t_abs, "type": "reset", "name": name, "hold": float("inf")})

    def add_expr(self, t_abs, name, intensity=1.0, side=None, hold=None, playback_at=None):
        cfg = self.config
        jitter = cfg["jitter"]
        with self.lock:
            side = self._take_side()
            floor = cfg["intensity_floor"]
            factor = cfg["gain"] * (floor + (1 - floor) * intensity)
            enter = (self.library["expressions"][name]["enter"] * cfg["enter_scale"]
                     * random.uniform(1 - jitter, 1 + jitter))
            # Conversation state changes and look-back, not the demo's automatic
            # return timer, decide when to leave a pose.
            self._add_event({"t": t_abs, "type": "expr", "name": name,
                             "intensity": factor, "side": side, "enter": enter,
                             "hold": float("inf") if hold is None else hold},
                            playback_at=playback_at)
        return {"side": side, "amplitude_factor": factor, "enter_duration": enter}

    def add_nod(self, t_abs, kind):
        cfg = self.config[kind]
        if cfg["amp"] == 0:
            return {"duration": 0.0}
        gesture = {"count": 1, "amp": cfg["amp"], "half_period": cfg["half_period"],
                   "decay": 1.0, "dir": 1}
        with self.lock:
            # Backchannel spacing is handled by the listener; an ack must not be
            # suppressed by the renderer's generic gesture interval.
            self._add_event({"t": t_abs, "type": "nod", "force": True, **gesture}, kind)
        return {**gesture, "duration": 2 * cfg["half_period"]}

    def add_emphasis(self, t_abs, reply_id):
        cfg = self.config["emphasis"]
        gesture = {"count": 1, "amp": cfg["amp"], "half_period": cfg["half_period"],
                   "decay": 1.0, "dir": 1}
        with self.lock:
            self._add_event({"t": t_abs, "type": "nod", "force": True,
                             "reply_id": reply_id, **gesture}, "emphasis", t_abs)
        return {**gesture, "duration": 2 * cfg["half_period"]}

    def add_phrase_shift(self, t_abs, playback_at=None):
        cfg = self.config["phrase_shift"]
        with self.lock:
            self._add_event({"t": t_abs, "type": "expr", "name": "neutral",
                             "enter": cfg["enter"], "hold": float("inf")},
                            "phrase_shift", playback_at)
        return {"enter_duration": cfg["enter"]}

    def add_gaze_away(self, t_abs, playback_at=None):
        cfg = self.config["speak_gaze_away"]
        with self.lock:
            side = self._take_side()
            self._add_event({"t": t_abs, "type": "expr", "name": "neutral",
                             "pose": [0, cfg["pitch"], cfg["yaw"]], "side": side,
                             "enter": cfg["enter"], "hold": float("inf")},
                            "speak_gaze_away", playback_at)
        return {"side": side, "yaw": side * cfg["yaw"], "pitch": cfg["pitch"],
                "amplitude_factor": 1.0, "enter_duration": cfg["enter"]}

    def close(self):
        self.stopped.set()
        if self.process is not None and self.process.pid is not None:
            self.process.join()
        # Shutdown may leave queued events; do not wait for an absent reader.
        self.commands.cancel_join_thread()
        self.commands.close()
