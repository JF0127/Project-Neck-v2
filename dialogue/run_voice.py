"""Half-duplex voice dialogue: uv run --python 3.10 python -m dialogue.run_voice."""

import argparse
from contextlib import ExitStack
import queue
import threading
import time

from .asr import QwenASR
from .mic import MicrophoneCapture, SAMPLE_RATE
from .motion import Motion
from .run_text import new_log, run_turn, trigger_action, trigger_expr, write_log
from .vad import SileroVAD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asr-model", help="Qwen model directory; overrides QWEN_ASR_MODEL")
    parser.add_argument("--mic-source", help="PulseAudio source; defaults to the system microphone")
    args = parser.parse_args()
    active_log = last_log = None
    idle_at = None
    thinking_timer = None
    pause_nodded = False
    last_backchannel = last_nod_end = float("-inf")
    try:
        with ExitStack() as resources:
            print("正在加载 Silero VAD 和 Qwen3-ASR……", flush=True)
            vad = SileroVAD()
            asr = QwenASR(args.asr_model)
            resources.callback(asr.close)
            motion = Motion()
            resources.callback(motion.close)
            motion.start()  # Models are loaded; finish the startup ramp before capture.
            mic = MicrophoneCapture(args.mic_source)
            resources.callback(mic.stop)

            def think_if_waiting(log):
                if not log["segments"] or log["segments"][0]["playback_start_at"] is None:
                    trigger_expr(motion, log, time.monotonic(), "thinking", hold=float("inf"))

            def cancel_thinking():
                nonlocal thinking_timer
                if thinking_timer is not None:
                    thinking_timer.cancel()
                    thinking_timer.join()
                    thinking_timer = None

            resources.callback(cancel_thinking)

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
                if "playback_aborted_at" not in log:
                    time.sleep(max(0.0, log.get("playback_safe_after", 0.0) - time.monotonic()))
                vad.reset()
                mic.start()
                now = time.monotonic()
                ends = [segment["playback_end_at"] for segment in log["segments"]
                        if segment["playback_end_at"] is not None]
                # Successful replies already looked back during the last segment.
                # Only empty/failed replies need a fallback listening pose here.
                if not ends or log["errors"]:
                    trigger_expr(motion, log, now, "attentive", hold=float("inf"))
                idle_at = (max(ends) if ends else now) + 8.0
                last_log = log

            mic.start()
            print("等待说话（Ctrl+C 退出）", flush=True)
            while True:
                try:
                    frame = mic.read_frame(timeout=0.1)
                except queue.Empty:
                    frame = None
                event, pcm = vad.push(frame) if frame is not None else (None, b"")
                stamp = time.monotonic()
                if event == "start":
                    idle_at = None
                    active_log = new_log("", stamp)
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
                    nod(active_log, stamp, "ack")
                    thinking_timer = threading.Timer(
                        max(0.0, stamp + motion.config["thinking_delay"] - time.monotonic()),
                        think_if_waiting, args=(active_log,))
                    thinking_timer.start()
                    mic.stop()
                    asr.push(pcm)
                    text = asr.finish()
                    final_at = time.monotonic()
                    active_log.update(user_text=text, input_at=final_at,
                                      asr_final_text=text, asr_final_at=final_at)
                    print(f"ASR：{text or '（空）'}", flush=True)
                    if text:
                        # The shared pipeline owns logging and cancellation from here.
                        log, active_log = active_log, None
                        run_turn(text, final_at, motion, log=log, thinking=False,
                                 on_complete=resume)
                    else:
                        resume(active_log)
                        write_log(active_log)
                        active_log = None
                elif pcm:
                    asr.push(pcm)  # The start event's PCM includes the 400 ms pre-roll.
                if idle_at is not None and time.monotonic() >= idle_at:
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
