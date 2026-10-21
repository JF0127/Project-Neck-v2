"""Offline rendering of explicit posture and gesture events."""

import heapq
import json
from pathlib import Path

import numpy as np


def _min_jerk(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * x * (10 + x * (-15 + 6 * x))


def _gesture_duration(gesture):
    if gesture is None:
        return 0.0
    segments = 2 * gesture["count"] if gesture["type"] == "nod" else gesture["count"] + 1
    return segments * gesture["half_period"]


def render(events, duration, fps=30, seed=0):
    """Return [t, roll, pitch, yaw] on the 1/fps grid through duration.

    Explicit events contain no randomness, so seed does not affect the result.
    """
    library = json.loads(Path(__file__).with_name("expressions.json").read_text())
    config = library["defaults"]
    transition, specs = config["transition"], config["gestures"]
    expressions = library["expressions"]
    events = sorted(events, key=lambda event: event["t"])
    returns, next_expr = [], np.inf
    for event in reversed(events):
        if event["type"] != "expr":
            continue
        spec = expressions[event["name"]]
        stamp = (event["t"] + spec["enter"] + _gesture_duration(spec.get("gesture"))
                 + event.get("hold", library["default_hold"]))
        if stamp < next_expr:
            returns.append({"t": stamp, "type": "_expr_return", "name": library["return_to"],
                            "side": event.get("side", 1)})
        next_expr = event["t"]
    events = sorted(events + returns, key=lambda event: event["t"])
    gestures = []
    last_gesture = -np.inf
    for event in events:
        stamp, kind = event["t"], event["type"]
        if kind == "expr":
            spec = expressions[event["name"]]
            if "gesture" not in spec:
                continue
            stamp += spec["enter"]
            event = {**spec["gesture"],
                     "amp": spec["gesture"]["amp"] * event.get("intensity", 1.0)}
        elif kind == "emphasis":
            event = library["emphasis"]
        elif kind in ("nod", "shake"):
            if stamp - last_gesture < specs["min_interval"]:
                continue
            last_gesture = stamp
        else:
            continue
        kind = event["type"]
        defaults = (2, 5, 0.7, 0.3, 1) if kind == "nod" else (3, 8, 0.75, 0.5, 1)
        count, amp, decay, step, direction = (
            event.get(key, value) for key, value in
            zip(("count", "amp", "decay", "half_period", "dir"), defaults))
        values = [0.0]
        for k in range(count):
            peak = direction * amp * decay ** k
            values.extend((peak, 0.0) if kind == "nod" else ((-1) ** k * peak,))
        if kind == "shake":
            values.append(0.0)
        gestures.append((stamp, kind, values, step))
    gestures.sort(key=lambda gesture: gesture[0])

    times = np.arange(int(duration * fps) + 1) / fps
    result = np.zeros((len(times), 4))
    result[:, 0] = times
    pending = [(event["t"], i, event) for i, event in enumerate(events)
               if event["type"] in ("shift", "home", "expr", "_expr_return")]
    heapq.heapify(pending)
    last_shift = -np.inf
    move_start, move_duration = 0.0, 1.0
    origin = target = np.zeros(3)
    while pending:
        stamp, index, event = heapq.heappop(pending)
        if event["type"] == "shift":
            # Check both before deferring and when the deferred shift is retried.
            if stamp - last_shift < config["speaking"]["min_interval"]:
                continue
            deferred = stamp
            for start, kind, values, step in gestures:
                end = start + (len(values) - 1) * step
                if start <= deferred < end:
                    deferred = end
            if deferred > stamp:
                heapq.heappush(pending, (deferred, index, event))
                continue
            last_shift = stamp

        origin = origin + (target - origin) * _min_jerk((stamp - move_start) / move_duration)
        if event["type"] == "shift":
            target = np.array([event["pitch"], event["yaw"], event.get("roll", 0)])
            move_duration = (transition["base_time"]
                             + np.max(np.abs(target - origin)) / transition["degrees_per_second"])
        elif event["type"] in ("expr", "_expr_return"):
            spec = expressions[event["name"]]
            side = event.get("side", 1)
            target = (np.array(spec["pose"])[[1, 2, 0]] * event.get("intensity", 1.0)
                      * [1, side, side])
            move_duration = (spec["enter"] if event["type"] == "expr"
                             else library["return_duration"])
        else:
            target = np.zeros(3)
            move_duration = transition["return_time"]
        move_start = stamp
        active = times >= stamp
        gain = _min_jerk((times[active] - stamp) / move_duration)
        result[active, 1:] = (origin + (target - origin) * gain[:, None])[:, [2, 0, 1]]

    for start, kind, values, step in gestures:
        u = (times - start) / step
        for segment, (a, b) in enumerate(zip(values, values[1:])):
            active = (u >= segment) & (u < segment + 1)
            x = u[active] - segment
            blend = (_min_jerk(x) if kind == "shake" and segment in (0, len(values) - 2)
                     else (1 - np.cos(np.pi * x)) / 2)
            result[active, 2 if kind == "nod" else 3] += a + (b - a) * blend
    np.clip(result[:, 1:], [-8, -8, -30], [8, 8, 30], out=result[:, 1:])
    return result
