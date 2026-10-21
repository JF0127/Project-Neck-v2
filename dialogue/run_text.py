"""Run with python -m dialogue.run_text from the repository root."""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from .llm import stream_segments
from .motion import Motion
from .tts import SAMPLE_RATE, pcm_chunks

LOGS = Path(__file__).resolve().parents[1] / "logs/dialogue"


def new_log(user_text, input_at):
    return {"user_text": user_text, "input_at": input_at, "first_segment_at": None,
            "segments": [], "expr_events": [], "action_events": [], "errors": [],
            "thinking_triggered": False}


def write_log(log, filename=None):
    filename = log.setdefault("log_file", filename or datetime.now().strftime("%Y%m%d_%H%M%S_%f.json"))
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / filename).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")


def trigger_expr(motion, log, stamp, name, intensity=1.0, hold=None, playback_at=None):
    called_at = time.monotonic()
    actual = motion.add_expr(stamp, name, intensity, hold=hold, playback_at=playback_at)
    log["expr_events"].append({"trigger_at": stamp, "called_at": called_at,
                               "name": name, "intensity": intensity, **actual})
    if name == "thinking":
        log["thinking_triggered"] = True
    return actual


def trigger_action(motion, log, stamp, kind, playback_at=None):
    called_at = time.monotonic()
    if kind == "look_back":
        actual = trigger_expr(motion, log, stamp, "attentive", hold=float("inf"),
                              playback_at=playback_at)
    elif kind == "speak_gaze_away":
        actual = motion.add_gaze_away(stamp, playback_at=playback_at)
    elif kind == "phrase_shift":
        actual = motion.add_phrase_shift(stamp, playback_at=playback_at)
    elif kind == "emphasis":
        actual = motion.add_emphasis(stamp, log["input_at"])
    else:
        actual = motion.add_nod(stamp, kind)
        if actual["duration"] == 0:
            return actual
    log["action_events"].append({"trigger_at": stamp, "called_at": called_at,
                                 "name": kind, **actual})
    return actual


def _run_async(work, stop):
    """Give each network worker a cancellable event loop in its own thread."""
    async def run():
        task = asyncio.create_task(work())
        while not task.done() and not stop.is_set():
            await asyncio.sleep(0.02)
        if stop.is_set():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def _playback(audio, stop, motion, log):
    events = queue.Queue()
    finished = threading.Event()
    exhausted = threading.Event()
    current, used, started = None, 0, False
    segment_samples, look_back_sent = 0, False
    tail_remaining, tail_written = None, 0
    clock_offset = 0.0
    safe_to_close = False

    def callback(out, frames, timing, status):
        nonlocal current, used, started, segment_samples, look_back_sent
        nonlocal tail_remaining, tail_written
        out.fill(0)
        if stop.is_set():
            raise sd.CallbackAbort
        if exhausted.is_set():
            return
        dac = timing.outputBufferDacTime + clock_offset
        pos = 0
        while pos < frames:
            if tail_remaining is not None:
                # out is already zeroed: append 0.3 s of silence on the same stream.
                count = min(frames - pos, tail_remaining)
                pos += count
                tail_remaining -= count
                tail_written += count
                log["tail_silence_written_s"] = tail_written / SAMPLE_RATE
                if count:
                    log["playback_dac_end_at"] = dac + pos / SAMPLE_RATE
                if tail_remaining == 0:
                    log["playback_safe_after"] = log.get("playback_dac_end_at", time.monotonic()) + 0.2
                    exhausted.set()
                return
            if current is None:
                try:
                    current = audio.get_nowait()
                except queue.Empty:
                    return  # Silence on network starvation; never drop queued PCM.
                if current is None:
                    tail_remaining = round(0.3 * SAMPLE_RATE) if started else 0
                    continue
                used = 0
            segment, pcm = current
            # Know whether this is the last segment before starting it. Only that
            # segment needs all PCM buffered so its look-back point is known in time.
            if segment["is_last"] is None or (segment["is_last"] and segment["tts_end_at"] is None):
                return
            if not started:
                ready = max(segment["tts_first_packet_at"], segment["boundary_known_at"],
                            segment["tts_end_at"] if segment["is_last"] else 0) + 0.3
                pos = max(pos, round((ready - dac) * SAMPLE_RATE))
                if pos >= frames:
                    return
            count = min(frames - pos, len(pcm) - used)
            if segment["playback_start_at"] is None:
                stamp = dac + pos / SAMPLE_RATE
                segment["playback_start_at"] = stamp
                segment_samples = 0
                if segment["expr"] != "none":
                    events.put((stamp - 0.25, "expr", segment["expr"], segment["intensity"], stamp))
                elif not started:
                    events.put((stamp, "speak_gaze_away", "", 1.0, stamp))
                else:
                    events.put((stamp - motion.config["phrase_shift"]["lead"],
                                "phrase_shift", "", 1.0, stamp))
                if segment.get("emphasis", False):
                    events.put((stamp, "emphasis", "", 1.0, stamp))
            if segment["is_last"] and not look_back_sent:
                target = segment["tts_samples"] * motion.config["look_back_ratio"]
                if segment_samples <= target <= segment_samples + count:
                    stamp = dac + (pos + target - segment_samples) / SAMPLE_RATE
                    events.put((stamp, "look_back", "", 1.0, segment["playback_start_at"]))
                    look_back_sent = True
            out[pos:pos + count, 0] = pcm[used:used + count]
            started = True
            pos += count
            used += count
            segment_samples += count
            segment["written_pcm_duration_s"] = segment_samples / SAMPLE_RATE
            segment["playback_end_at"] = dac + pos / SAMPLE_RATE
            log["playback_dac_end_at"] = segment["playback_end_at"]
            if used == len(pcm):
                current = None

    # A 300 ms output buffer lets the callback report DAC times before the
    # corresponding expression deadlines (250 ms before the first sample).
    requested_latency = 0.3
    stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                             blocksize=160, latency=requested_latency, callback=callback,
                             finished_callback=finished.set)
    try:
        device = sd.query_devices(stream.device, "output")
        log["audio_output"] = {
            "host_api": sd.query_hostapis(device["hostapi"])["name"],
            "device": device["name"], "requested_latency": requested_latency,
            "actual_latency": stream.latency,
        }
        # Clock mapping copied from the archived speech_motion/player.py.
        before = time.monotonic()
        audio_clock = stream.time
        clock_offset = (before + time.monotonic()) / 2 - audio_clock
        stream.start()
        while not stop.is_set():
            while True:
                try:
                    stamp, kind, name, intensity, playback_at = events.get_nowait()
                except queue.Empty:
                    break
                # Submit immediately; the motion process keeps the effective deadline.
                if kind == "expr":
                    trigger_expr(motion, log, stamp, name, intensity, playback_at=playback_at)
                else:
                    trigger_action(motion, log, stamp, kind, playback_at=playback_at)
            now = time.monotonic()
            if (exhausted.is_set() and now >= log["playback_safe_after"]
                    and events.empty()):
                safe_to_close = True
                break
            if finished.is_set():
                raise RuntimeError("audio stream stopped before the DAC drain deadline")
            stop.wait(0.005)
    finally:
        if safe_to_close and not stop.is_set():
            stream.stop()
            log["playback_drained_at"] = time.monotonic()
        else:
            stream.abort()
            aborted_at = time.monotonic()
            log["playback_aborted_at"] = aborted_at
            for segment in log["segments"]:
                start = segment["playback_start_at"]
                if start is not None:
                    if start >= aborted_at:
                        segment["playback_start_at"] = segment["playback_end_at"] = None
                    else:
                        segment["playback_end_at"] = min(segment["playback_end_at"], aborted_at)
        stream.close()


def run_turn(user_text, input_at, motion, *, log=None, thinking=True, on_complete=None):
    """Shared text/voice reply pipeline; on_complete runs after playback is drained."""
    if log is None:
        log = new_log(user_text, input_at)
    log.update(user_text=user_text, input_at=input_at)
    if thinking:
        trigger_expr(motion, log, input_at, "thinking")
    filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f.json")
    text, audio = queue.Queue(), queue.Queue()
    stop = threading.Event()

    async def generate():
        async for value in stream_segments(user_text):
            stamp = time.monotonic()
            if log["first_segment_at"] is None:
                log["first_segment_at"] = stamp
            if log["segments"]:
                log["segments"][-1]["boundary_known_at"] = stamp
                log["segments"][-1]["is_last"] = False
            segment = {**value, "arrived_at": stamp, "tts_first_packet_at": None,
                       "tts_end_at": None, "tts_samples": 0, "is_last": None,
                       "tts_pcm_duration_s": 0.0, "written_pcm_duration_s": 0.0,
                       "boundary_known_at": None, "playback_start_at": None,
                       "playback_end_at": None}
            log["segments"].append(segment)
            text.put(segment)
            print(segment["text"], end="", flush=True)
        if log["segments"]:
            log["segments"][-1]["boundary_known_at"] = time.monotonic()
            log["segments"][-1]["is_last"] = True
        text.put(None)

    async def synthesize():
        while True:
            try:
                segment = text.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if segment is None:
                audio.put(None)
                return
            async for chunk in pcm_chunks(segment["text"]):
                if segment["tts_first_packet_at"] is None:
                    segment["tts_first_packet_at"] = time.monotonic()
                segment["tts_samples"] += len(chunk) // 2
                segment["tts_pcm_duration_s"] = segment["tts_samples"] / SAMPLE_RATE
                audio.put((segment, np.frombuffer(chunk, dtype="<i2")))
            if segment["tts_first_packet_at"] is None:
                raise RuntimeError("Doubao returned no PCM for a segment")
            segment["tts_end_at"] = time.monotonic()
            # Immediately synthesize the next segment, without waiting for playback.

    def worker(work):
        try:
            work()
        except Exception as exc:
            log["errors"].append(f"{type(exc).__name__}: {exc}")
            stop.set()

    workers = [
        threading.Thread(target=worker, args=(lambda: _run_async(generate, stop),),
                         name="dialogue-llm"),
        threading.Thread(target=worker, args=(lambda: _run_async(synthesize, stop),),
                         name="dialogue-tts"),
        threading.Thread(target=worker, args=(lambda: _playback(audio, stop, motion, log),),
                         name="dialogue-playback"),
    ]
    started = []
    print("机器人：", end="", flush=True)
    try:
        for thread in workers:
            thread.start()
            started.append(thread)
        while any(thread.is_alive() for thread in started):
            time.sleep(0.05)
    except KeyboardInterrupt:
        log["interrupted"] = True
        raise
    finally:
        stop.set()
        for thread in started:
            thread.join()
        try:
            if on_complete is not None and not log.get("interrupted"):
                on_complete(log)
        finally:
            write_log(log, filename)
            print()
    for error in log["errors"]:
        print(error, file=sys.stderr)
    return log


def main():
    motion = Motion()
    try:
        motion.start()  # Text mode has no local models; finish the ramp before input.
        while True:
            user_text = input("你：")
            input_at = time.monotonic()
            if user_text.strip():
                run_turn(user_text.strip(), input_at, motion)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        motion.close()


if __name__ == "__main__":
    main()
