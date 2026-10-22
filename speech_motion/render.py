"""Offline rendering of explicit posture and gesture events."""

import heapq
import json
from pathlib import Path

import numpy as np
from numpy.polynomial.polynomial import polyder, polyval


def _min_jerk(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * x * (10 + x * (-15 + 6 * x))


def _gesture_duration(gesture):
    if gesture is None or (gesture["type"] == "nod" and gesture["amp"] == 0):
        return 0.0
    segments = 2 * gesture["count"] if gesture["type"] == "nod" else gesture["count"] + 1
    return segments * gesture["half_period"]


def render(events, duration, fps=30, seed=0):
    """Return [t, roll, pitch, yaw] on the 1/fps grid through duration.

    seed controls the side of idle events without an explicit side.
    """
    library = json.loads(Path(__file__).with_name("expressions.json").read_text())
    config = library["defaults"]
    transition, specs = config["transition"], config["gestures"]
    expressions = library["expressions"]
    idle_scale = library.get("idle_scale", 1.0)
    random = np.random.default_rng(seed)
    events = sorted(events, key=lambda event: event["t"])
    returns, next_expr = [], np.inf
    for event in reversed(events):
        if event["type"] in ("idle", "look_around"):
            next_expr = event["t"]
            continue
        if event["type"] != "expr":
            continue
        spec = expressions[event["name"]]
        stamp = (event["t"] + event.get("enter", spec["enter"]) + _gesture_duration(spec.get("gesture"))
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
        if event.get("clear_gestures", False):
            gestures = [(start, kind, values, step, min(end, stamp))
                        for start, kind, values, step, end in gestures]
        if kind == "expr":
            spec = expressions[event["name"]]
            if "gesture" not in spec:
                continue
            stamp += event.get("enter", spec["enter"])
            event = {**spec["gesture"],
                     "amp": spec["gesture"]["amp"] * event.get("intensity", 1.0)}
        elif kind == "emphasis":
            event = library["emphasis"]
        elif kind not in ("nod", "shake"):
            continue
        if event["type"] == "nod" and event.get("amp", 5) == 0:
            continue
        if kind in ("nod", "shake"):
            if not event.get("force", False) and stamp - last_gesture < specs["min_interval"]:
                continue
            last_gesture = stamp
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
        gestures.append((stamp, kind, values, step, np.inf))
    gestures.sort(key=lambda gesture: gesture[0])

    times = np.arange(int(duration * fps) + 1) / fps
    result = np.zeros((len(times), 4))
    result[:, 0] = times
    pending = [(event["t"], i, event) for i, event in enumerate(events)
               if event["type"] in ("shift", "home", "expr", "_expr_return", "idle", "look_around")]
    heapq.heapify(pending)
    last_shift = -np.inf
    look_return = None
    move_start, move_duration = 0.0, np.ones(3)
    origin = target = np.zeros(3)
    coefficients = np.zeros((6, 3))
    while pending:
        stamp, index, event = heapq.heappop(pending)
        if event["type"] == "_look_return" and index != look_return:
            continue
        if event["type"] == "shift":
            # Check both before deferring and when the deferred shift is retried.
            if stamp - last_shift < config["speaking"]["min_interval"]:
                continue
            deferred = stamp
            for start, kind, values, step, end in gestures:
                end = min(end, start + (len(values) - 1) * step)
                if start <= deferred < end:
                    deferred = end
            if deferred > stamp:
                heapq.heappush(pending, (deferred, index, event))
                continue
            last_shift = stamp

        look_return = index if event["type"] == "look_around" else None
        u = (stamp - move_start) / move_duration
        moving = (u >= 0.0) & (u < 1.0)
        u = np.clip(u, 0.0, 1.0)
        origin = np.where(u >= 1.0, target, polyval(u, coefficients, tensor=False))
        velocity = np.where(moving, polyval(u, polyder(coefficients), tensor=False)
                            / move_duration, 0.0)
        acceleration = np.where(moving, polyval(u, polyder(coefficients, 2), tensor=False)
                                / move_duration ** 2, 0.0)
        if event["type"] == "shift":
            target = np.array([event["pitch"], event["yaw"], event.get("roll", 0)], dtype=float)
            move_duration = (transition["base_time"]
                             + np.max(np.abs(target - origin)) / transition["degrees_per_second"])
        elif event["type"] in ("expr", "_expr_return"):
            spec = expressions[event["name"]]
            side = event.get("side", 1)
            target = (np.array(event.get("pose", spec["pose"]), dtype=float)[[1, 2, 0]]
                      * event.get("intensity", 1.0) * [1, side, side])
            if spec.get("relative", False):
                target += origin
            move_duration = (event.get("enter", spec["enter"]) if event["type"] == "expr"
                             else library["return_duration"])
        elif event["type"] in ("idle", "look_around"):
            side = event["side"] if "side" in event else random.choice((-1, 1))
            factor = idle_scale * np.clip(event.get("intensity", 1.0), 0.3, 1.0)
            if event["type"] == "idle":
                spec = library["idle_poses"][event["name"]]
                target = np.array(spec["pose"], dtype=float)
                target[spec.get("mirror_axes", [])] *= side
                target = target[[1, 2, 0]] * factor
                move_duration = event.get("enter", spec["enter"])
            else:
                target = origin.copy()
                target[1] = library["look_around"]["yaw"] * side * factor
                move_duration = transition["return_time"]
        elif event["type"] == "_look_return":
            target = event["pose"]
            move_duration = transition["return_time"]
        else:
            target = np.zeros(3)
            move_duration = transition["return_time"]
        yaw_delta = abs(target[1] - origin[1])
        move_duration = np.array([move_duration, max(0.5, yaw_delta / 6), move_duration])
        if yaw_delta < 2:
            target[1] = origin[1]
            velocity[1] = acceleration[1] = 0.0
        if event["type"] == "look_around":
            heapq.heappush(pending, (stamp + move_duration[1] + library["look_around"]["hold"],
                                    index, {"type": "_look_return", "pose": origin.copy()}))
        # Quintic in normalized time u: preserve initial p/v/a, end at target
        # with zero v/a. For zero initial v/a this is the usual min-jerk curve.
        delta = target - origin
        v = velocity * move_duration
        a = acceleration * move_duration ** 2 / 2
        coefficients = np.array([origin, v, a, 10 * delta - 6 * v - 3 * a,
                                 -15 * delta + 8 * v + 3 * a, 6 * delta - 3 * v - a])
        move_start = stamp
        active = times >= stamp
        u = np.clip((times[active, None] - stamp) / move_duration, 0.0, 1.0)
        poses = np.where(u >= 1.0, target, polyval(u, coefficients, tensor=False))
        result[active, 1:] = poses[:, [2, 0, 1]]

    for start, kind, values, step, end in gestures:
        u = (times - start) / step
        for segment, (a, b) in enumerate(zip(values, values[1:])):
            active = (u >= segment) & (u < segment + 1) & (times < end)
            x = u[active] - segment
            blend = (_min_jerk(x) if kind == "shake" and segment in (0, len(values) - 2)
                     else (1 - np.cos(np.pi * x)) / 2)
            result[active, 2 if kind == "nod" else 3] += a + (b - a) * blend
    np.clip(result[:, 1:], [-8, -8, -30], [8, max(8, 12 * idle_scale), 30], out=result[:, 1:])
    return result
