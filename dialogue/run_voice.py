"""Half-duplex voice dialogue: uv run --python 3.10 python -m dialogue.run_voice."""

import argparse
from contextlib import ExitStack
import queue
import threading
import time

from .asr import QwenASR
from .control import Controls
from .mic import MicrophoneCapture, SAMPLE_RATE
from .motion import Motion
from .run_text import LOGS, TTSWorker, new_log, run_turn, trigger_action, trigger_expr, write_log
from .vad import SileroVAD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asr-model", help="Qwen model directory; overrides QWEN_ASR_MODEL")
    parser.add_argument("--mic-source", help="PulseAudio source; defaults to the system microphone")
    parser.add_argument("--control-host", default="127.0.0.1", help="HTTP control bind address")
    parser.add_argument("--control-port", type=int, default=8770, help="HTTP control port")
    args = parser.parse_args()
    active_log = last_log = None
    idle_at = None
    thinking_timer = None
    tts = None
    pause_nodded = False
    last_backchannel = last_nod_end = float("-inf")
    try:
        with ExitStack() as resources:
            print("正在加载 Silero VAD 和 Qwen3-ASR……", flush=True)
            motion = Motion()
            resources.callback(motion.close)
            vad = SileroVAD(min_silence_ms=motion.config["vad_end_silence"] * 1000)
            asr = QwenASR(args.asr_model)
            resources.callback(asr.close)
            motion.start()
            mic = MicrophoneCapture(args.mic_source)
            resources.callback(mic.stop)

            def cancel_tts():
                nonlocal tts
                worker, tts = tts, None
                if worker is not None:
                    worker.close()

            resources.callback(cancel_tts)

            def cancel_thinking():
                nonlocal thinking_timer
                timer, thinking_timer = thinking_timer, None
                if timer is not None:
                    timer.cancel()
                    timer.join()

            resources.callback(cancel_thinking)

            def interrupt():
                nonlocal idle_at
                idle_at = None
                if active_log is not None:
                    active_log["interrupted"] = True
                if thinking_timer is not None:
                    thinking_timer.cancel()
                if tts is not None:
                    tts.log["interrupted"] = True
                    tts.cancel()

            def state_changed(previous, state, source):
                if source != "auto":
                    interrupt()
                if state in ("waiting", "listening"):
                    motion.reset(time.monotonic(), "neutral" if state == "waiting" else "attentive")
                return source != "auto" and previous == "speaking"

            controls = Controls(args.control_host, args.control_port, LOGS, state_changed, interrupt)
            resources.callback(controls.close)
            controls.start()
            capture_revision = 0

            def is_current(revision):
                with controls.lock:
                    return (not controls.stopped.is_set() and controls.revision == revision
                            and controls.state != "waiting")

            def think_if_waiting(log, revision):
                with controls.lock:
                    if (is_current(revision) and active_log is log and tts is not None
                            and not tts.stop.is_set() and not log["thinking_triggered"]
                            and (not log["segments"] or log["segments"][0]["playback_start_at"] is None)):
                        trigger_expr(motion, log, time.monotonic(), "thinking", hold=float("inf"))

            def nod(log, stamp, kind):
                nonlocal last_backchannel, last_nod_end
                if motion.config[kind]["amp"] == 0:
                    return stamp
                reasons = []
                if stamp < last_nod_end:
                    reasons.append("previous_nod_not_finished")
                if kind == "ack" and stamp - last_backchannel < 1.0:
                    reasons.append("backchannel_within_1s")
                if reasons:
                    log["action_events"].append({
                        "name": kind, "attempted_at": stamp, "skipped": True,
                        "reasons": reasons, "previous_nod_end_at": last_nod_end,
                    })
                    return max(stamp, last_nod_end)
                action = trigger_action(motion, log, stamp, kind)
                last_nod_end = stamp + action["duration"]
                if kind == "backchannel":
                    last_backchannel = stamp
                return last_nod_end

            def resume(log):
                nonlocal idle_at, last_log
                cancel_thinking()
                cancel_tts()
                with controls.lock:
                    if not is_current(capture_revision):
                        return
                    vad.reset()
                    now = time.monotonic()
                    ends = [segment["playback_end_at"] for segment in log["segments"]
                            if segment["playback_end_at"] is not None]
                    # Successful replies already looked back during the last segment.
                    if not ends or log["errors"]:
                        trigger_expr(motion, log, now, "attentive", hold=float("inf"))
                    idle_at = (max(ends) if ends else now) + 8.0
                    last_log = log
                    controls.change("listening")

            print("等待说话（空格切换等待/聆听，q 或 Ctrl+C 退出）", flush=True)
            while not controls.stopped.is_set():
                with controls.lock:
                    revision = controls.revision
                    listening = controls.state == "listening"
                if revision != capture_revision:
                    cancel_thinking()
                    cancel_tts()
                    mic.stop()
                    vad.reset()
                    asr.state = None
                    if active_log is not None:
                        write_log(active_log)
                        active_log = None
                    pause_nodded = False
                    last_backchannel = last_nod_end = float("-inf")
                    capture_revision = revision
                    continue
                if not listening:
                    mic.stop()
                    controls.stopped.wait(0.05)
                    continue
                mic.start()
                try:
                    frame = mic.read_frame(timeout=0.1)
                except queue.Empty:
                    frame = None
                with controls.lock:
                    if not is_current(revision):
                        continue
                    event, pcm = vad.push(frame) if frame is not None else (None, b"")
                    stamp = time.monotonic()
                    if event == "start":
                        idle_at = None
                        active_log = new_log("", stamp, motion.config)
                        active_log.update(speech_start_at=stamp, speech_end_at=None,
                                          asr_final_text=None, asr_final_at=None)
                        trigger_expr(motion, active_log, stamp, "attentive", hold=float("inf"))
                        asr.start()
                    if vad.silence_samples == 0:
                        pause_nodded = False
                    backchannel = motion.config["backchannel"]
                    if (vad.in_speech and not pause_nodded
                            and vad.silence_samples / SAMPLE_RATE >= backchannel["pause_min"]
                            and stamp - last_backchannel >= backchannel["min_interval"]):
                        nod(active_log, stamp, "backchannel")
                        pause_nodded = True
                    if event == "end":
                        active_log["speech_end_at"] = stamp
                        tts = TTSWorker(active_log, motion.config)
                        nod(active_log, stamp, "ack")
                        thinking_timer = threading.Timer(
                            max(0.0, stamp + motion.config["thinking_delay"] - time.monotonic()),
                            think_if_waiting, args=(active_log, revision))
                        thinking_timer.start()
                if event == "end":
                    mic.stop()
                    if not is_current(revision):
                        continue
                    asr.push(pcm)
                    if not is_current(revision):
                        continue
                    text = asr.finish()
                    final_at = time.monotonic()
                    with controls.lock:
                        if not is_current(revision):
                            continue
                        active_log.update(user_text=text, input_at=final_at,
                                          asr_final_text=text, asr_final_at=final_at)
                        print(f"ASR：{text or '（空）'}", flush=True)
                        if text:
                            controls.change("speaking")
                    if text:
                        run_turn(text, final_at, motion, log=active_log, thinking=False,
                                 on_complete=resume, tts=tts)
                    else:
                        resume(active_log)
                        write_log(active_log)
                    active_log = None
                elif pcm and is_current(revision):
                    # In-flight local inference may finish after a toggle; its result is discarded.
                    asr.push(pcm)
                with controls.lock:
                    if (is_current(revision) and idle_at is not None
                            and time.monotonic() >= idle_at):
                        trigger_expr(motion, last_log, idle_at, "neutral", hold=float("inf"))
                        write_log(last_log)
                        idle_at = None
    except KeyboardInterrupt:
        if active_log is not None:
            active_log["interrupted"] = True
    except Exception as exc:
        if active_log is not None:
            active_log["errors"].append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if active_log is not None:
            write_log(active_log)


if __name__ == "__main__":
    main()
