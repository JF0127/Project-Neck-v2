"""Local Silero VAD adapted from Project-Neck/runtime/vad.py."""

from collections import deque
import math
from pathlib import Path

import numpy as np

from .mic import FRAME_BYTES, FRAME_MS, SAMPLE_RATE

MODEL_PATH = Path(__file__).with_name("models") / "silero_vad.jit"
WINDOW_SAMPLES = 512
WINDOW_BYTES = WINDOW_SAMPLES * 2


class SileroVAD:
    def __init__(self, model_path=MODEL_PATH, threshold=0.5,
                 min_speech_ms=250, min_silence_ms=500):
        import torch

        self.torch = torch
        self.model = torch.jit.load(str(Path(model_path).expanduser()), map_location="cpu")
        self.model.eval()
        self.threshold = threshold
        self.min_speech = math.ceil(min_speech_ms * SAMPLE_RATE / 1000)
        self.min_silence = math.ceil(min_silence_ms * SAMPLE_RATE / 1000)
        self.pending = bytearray()
        self.pre_roll = deque(maxlen=400 // FRAME_MS)
        self.reset()

    def reset(self):
        self.pending.clear()
        self.pre_roll.clear()
        self.speech_samples = self.silence_samples = 0
        self.in_speech = False
        self.model.reset_states()

    def push(self, frame):
        """Return (start/end/None, PCM to push to ASR), including pre-roll once."""
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"VAD input must be {FRAME_BYTES} bytes")
        if not self.in_speech:
            self.pre_roll.append(frame)
        self.pending.extend(frame)
        event = None
        while len(self.pending) >= WINDOW_BYTES:
            window = bytes(self.pending[:WINDOW_BYTES])
            del self.pending[:WINDOW_BYTES]
            waveform = np.frombuffer(window, dtype="<i2").astype(np.float32) / 32768.0
            with self.torch.inference_mode():
                score = self.model(self.torch.from_numpy(waveform), SAMPLE_RATE).item()
            is_speech = score >= self.threshold
            if not self.in_speech:
                self.speech_samples = self.speech_samples + WINDOW_SAMPLES if is_speech else 0
                if self.speech_samples >= self.min_speech:
                    self.in_speech = True
                    self.speech_samples = self.silence_samples = 0
                    event = "start"
            else:
                self.silence_samples = 0 if is_speech else self.silence_samples + WINDOW_SAMPLES
                if self.silence_samples >= self.min_silence:
                    self.in_speech = False
                    self.silence_samples = 0
                    event = "end"
        if event == "start":
            pcm = b"".join(self.pre_roll)
            self.pre_roll.clear()
        else:
            pcm = frame if self.in_speech or event == "end" else b""
        return event, pcm
