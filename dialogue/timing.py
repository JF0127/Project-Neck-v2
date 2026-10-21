"""Per-turn timing logs and process-lifetime summaries."""

import atexit
import csv
from datetime import datetime
from pathlib import Path
from statistics import mean, median

CSV_PATH = (Path(__file__).resolve().parents[1] / "logs/timing"
            / datetime.now().strftime("%Y%m%d_%H%M%S_%f.csv"))
_rounds = []


def _duration(start, end):
    return None if start is None or end is None else round(end - start, 3)


def _format(value):
    return "" if value is None else f"{value:.3f}"


def record_timing(log):
    # Idle pose updates rewrite the same JSON, but must not count as new turns.
    if "timing" in log:
        return
    segments = log["segments"]
    first, last = (segments[0], segments[-1]) if segments else ({}, {})
    request = log.get("llm_request_at")
    tts_request = first.get("tts_request_at")
    first_packet = first.get("tts_first_packet_at")
    playback = first.get("playback_start_at")
    pairs = {
        "speech_duration": (log.get("speech_start_at"), log.get("speech_end_at")),
        "asr_finalize": (log.get("speech_end_at"), log.get("asr_final_at")),
        "llm_ttft": (request, log.get("llm_first_token_at")),
        "llm_first_segment": (request, log.get("first_segment_at")),
        "llm_total": (request, log.get("llm_done_at")),
        "tts_first_ttfb": (tts_request, first_packet),
        "tts_first_total": (tts_request, first.get("tts_done_at")),
        "playback_buffer": (first_packet, playback),
        "response_latency": (log.get("speech_end_at", log["input_at"]), playback),
        "reply_audio_duration": (playback, last.get("playback_end_at")),
    }
    timing = log["timing"] = {key: _duration(*pair) for key, pair in pairs.items()}
    timing["search_total"] = round(sum(item["duration_s"] for item in log.get("searches", [])), 3)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if not _rounds:
            writer.writerow(("turn", "user_text", *timing, "segment_count", "thinking_triggered"))
        writer.writerow((len(_rounds) + 1, log["user_text"],
                         *(_format(value) for value in timing.values()),
                         len(segments), log["thinking_triggered"]))
    _rounds.append(timing)
    labels = (("asr", "asr_finalize"), ("llm_ttft", "llm_ttft"),
              ("llm_seg1", "llm_first_segment"), ("tts_ttfb", "tts_first_ttfb"),
              ("buffer", "playback_buffer"), ("total", "response_latency"))
    if log.get("search_used"):
        labels += (("search", "search_total"),)
    print("[timing] " + " | ".join(f"{label} {_format(timing[key]) or '-'}"
                                  for label, key in labels), flush=True)


@atexit.register
def print_summary():
    if not _rounds:
        return
    print(f"\n[timing summary] turns={len(_rounds)} (seconds)", flush=True)
    for key in _rounds[0]:
        values = [row[key] for row in _rounds if row[key] is not None]
        stats = (mean(values), median(values), max(values)) if values else (None, None, None)
        print(f"  {key}: " + " | ".join(f"{label} {_format(value) or '-'}"
                                       for label, value in zip(("mean", "median", "max"), stats)),
              flush=True)
