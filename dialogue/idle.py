"""Event-driven idle decisions and cancellable non-streaming DeepSeek requests."""

import asyncio
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import random
import threading
import time

from openai import AsyncOpenAI

from .llm import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, _completed_text, _history


PROMPT = """你是一个机器人头部的行为决策模块。你的人设是一位安静、克制的成年男性：话少，不刻意找话题，独处时自然地发呆、思考，偶尔调整姿态。

现在没有人在和你说话。根据最近的对话、沉默时长和你当前的姿态，决定接下来做什么。

可选姿态：
- rest_calm：平视静候
- rest_low：微低头放松
- ponder：低头沉思，适合严肃或沉重的话题之后
- gaze_side：侧望出神，适合长时间沉默
- gaze_up：微仰放空，适合轻松愉快的话题之后
- lean_tilt：微侧头放松

可选行为（只能从“本次允许的行为”中选）：
- keep：保持当前姿态。这应该是你最常见的选择
- pose：换一个姿态，并给出强度（0.3–1.0）
- look_around：环顾一下
- speak：说一句话

关于 speak：
- 只在确实有话可说时才用，比如接着刚才的话题补一句、表达关心
- 不超过 20 个字，语气平静，不要问“你还在吗”这类话
- 不确定时就不说

只输出一个 JSON 对象，不要输出任何其他内容。格式：
{"action": "keep"}
{"action": "pose", "pose": "姿态名", "intensity": 0.5}
{"action": "look_around"}
{"action": "speak", "text": "...", "emotion": "情绪名"}
"""


class IdleDecider:
    def __init__(self, library):
        self.library = library
        self.chatted = self.spoken = self.active = self.expired = False
        self.silence_at = self.next_at = self.last_speak = self.response_until = None
        self.first = True
        self.pose, self.intensity, self.pose_at = "rest_calm", 1.0, time.monotonic()
        self.pending = None
        self.results, self.workers = queue.Queue(), []
        directory = Path(__file__).resolve().parents[1] / "logs/idle"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / datetime.now().strftime("%Y%m%d_%H%M%S_%f.jsonl")

    def pause(self):
        self.active = False
        if self.pending is not None:
            self.pending.set()
            self.pending = None

    def user_started(self, stamp):
        self.pause()
        if self.last_speak is not None and self.last_speak["responded"] is None:
            self.last_speak["responded"] = self.response_until is None or stamp <= self.response_until
        self.silence_at = self.next_at = None
        self.spoken = self.expired = False
        self.first = True

    def reply_finished(self, log):
        self.pause()
        stamp = log["post_speech_at"]
        if not log.get("proactive"):
            self.chatted |= bool(log["user_text"])
            self.silence_at = stamp
            self.first, self.spoken, self.expired = True, False, False
            self.next_at = stamp + (60 if self.chatted else random.uniform(60, 120))
        else:
            self.response_until = stamp + self.library["conversation"].get("post_speech_wait", 4.0)
        self.pose, self.intensity, self.pose_at = "attentive", 1.0, stamp

    def enter(self, stamp, pose="rest_calm", intensity=1.0):
        self.active = True
        self.pose, self.intensity, self.pose_at = pose, intensity, stamp
        if self.last_speak is not None and self.last_speak["responded"] is None:
            self.last_speak["responded"] = False
        if self.silence_at is None:
            self.silence_at = stamp
            self.next_at = stamp + (60 if self.chatted else random.uniform(60, 120))

    def _state(self, stamp, state, allowed):
        return {"state": state, "chatted_since_start": self.chatted,
                "silence_started_at": self.silence_at, "silence_seconds": stamp - self.silence_at,
                "pose": {"name": self.pose, "intensity": self.intensity,
                         "held_seconds": max(0.0, stamp - self.pose_at)},
                "allowed_actions": allowed, "spoken_in_silence": self.spoken,
                "last_proactive": dict(self.last_speak) if self.last_speak is not None else None,
                "recent_dialogue": [{"user": user, "assistant": reply}
                                    for user, reply in list(_history)[-5:]]}

    def _parse(self, raw, allowed):
        try:
            value = json.loads(raw)
            action = value["action"]
            if action not in allowed:
                raise ValueError()
            if action == "pose":
                pose, intensity = value["pose"], float(value.get("intensity", 0.5))
                if pose not in self.library["idle_poses"] or not math.isfinite(intensity):
                    raise ValueError()
                return {"action": action, "pose": pose, "intensity": min(1.0, max(0.3, intensity))}, False
            if action == "speak":
                text = value["text"].strip()
                if not text or len(text) > 20:
                    raise ValueError()
                emotion = value.get("emotion", "neutral")
                invalid = emotion not in self.library["expressions"] and emotion != "none"
                return {"action": action, "text": text,
                        "emotion": "neutral" if invalid else emotion}, invalid
            return {"action": action}, False
        except (ValueError, TypeError, KeyError, AttributeError):
            return {"action": "keep"}, True

    async def _request(self, cancelled, state):
        if cancelled.is_set():
            return None
        async with AsyncOpenAI(api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
                               base_url=DEEPSEEK_BASE_URL, timeout=30.0, max_retries=0) as client:
            task = asyncio.create_task(client.responses.create(
                model=DEEPSEEK_MODEL, stream=False, max_output_tokens=512,
                temperature=0.7, reasoning={"effort": "none"},
                input=[{"role": "system", "content": PROMPT},
                       {"role": "user", "content": json.dumps(state, ensure_ascii=False)}]))
            while not task.done() and not cancelled.is_set():
                await asyncio.sleep(0.02)
            if cancelled.is_set():
                task.cancel()
            try:
                return _completed_text(await task)
            except asyncio.CancelledError:
                return None

    def _work(self, cancelled, state):
        raw = None
        try:
            raw = asyncio.run(self._request(cancelled, state))
            action, downgraded = self._parse(raw, state["allowed_actions"])
        except Exception:
            action, downgraded = {"action": "keep"}, True
        self.results.put((cancelled, state, raw, action, downgraded))

    def _commit(self, action, stamp):
        if action["action"] == "pose":
            self.pose, self.intensity, self.pose_at = action["pose"], action["intensity"], stamp
        elif action["action"] == "look_around":
            self.pose_at = stamp  # The transient action returns to the same resting pose.
        elif action["action"] == "speak":
            self.spoken, self.active = True, False
            self.response_until = None
            self.last_speak = {"at": datetime.now().astimezone().isoformat(), "at_mono": stamp,
                               "text": action["text"], "responded": None}

    def _record(self, state, raw, action, downgraded):
        event = {"at": datetime.now().astimezone().isoformat(), "at_mono": time.monotonic(),
                 "state": state, "raw_output": raw, "action": action, "downgraded": downgraded}
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")

    def tick(self, stamp, state="IDLE"):
        while True:
            try:
                cancelled, sent, raw, action, downgraded = self.results.get_nowait()
            except queue.Empty:
                break
            if self.pending is cancelled:
                self.pending = None
            if cancelled.is_set() or not self.active or stamp - self.silence_at >= 900:
                self._record(sent, raw, {"action": "keep"}, True)
                continue
            self._commit(action, stamp)
            self._record(sent, raw, action, downgraded)
            return action
        if not self.active or self.expired:
            return None
        allowed = ["keep", "pose", "look_around"]
        if stamp - self.silence_at >= 900:
            self.pause()
            self.expired = True
            action = {"action": "pose", "pose": "rest_low", "intensity": 1.0}
            sent = self._state(stamp, state, allowed)
            self._commit(action, stamp)
            self._record(sent, None, action, False)
            return action
        if self.pending is not None or stamp < self.next_at:
            return None
        if self.chatted and self.first and not self.spoken:
            allowed.append("speak")
        self.first = False
        self.next_at = stamp + (random.uniform(180, 300) if self.chatted else random.uniform(60, 120))
        sent = self._state(stamp, state, allowed)
        if not self.chatted:
            pose = random.choice(("rest_calm", "rest_low", "gaze_side", "look_around"))
            action = ({"action": "look_around"} if pose == "look_around" else
                      {"action": "pose", "pose": pose, "intensity": 1.0})
            self._commit(action, stamp)
            self._record(sent, None, action, False)
            return action
        cancelled = self.pending = threading.Event()
        worker = threading.Thread(target=self._work, args=(cancelled, sent), name="idle-decision")
        self.workers.append((cancelled, worker))
        worker.start()
        return None

    def close(self):
        self.pause()
        for cancelled, worker in self.workers:
            cancelled.set()
            worker.join()
        self.tick(time.monotonic())
