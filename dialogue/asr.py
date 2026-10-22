"""Qwen3-ASR streaming adapted from Project-Neck/runtime/qwen_streaming_runtime.py."""

import os
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "third_party/probe"))
from probe import probe

DEFAULT_MODEL_PATH = "~/projects/Project-Neck/dataset/models/qwen"


class QwenASR:
    def __init__(self, model_path=None):
        from qwen_asr import Qwen3ASRModel

        path = Path(model_path or os.environ.get("QWEN_ASR_MODEL", DEFAULT_MODEL_PATH))
        self.model = Qwen3ASRModel.LLM(
            model=str(path.expanduser().resolve()), gpu_memory_utilization=0.7,
            max_inference_batch_size=1, max_new_tokens=4096,
        )
        self.state = None

    def start(self):
        probe("recognition_lifecycle", "start")
        self.state = self.model.init_streaming_state(
            language="Chinese", chunk_size_sec=1.0,
            unfixed_chunk_num=4, unfixed_token_num=5,
        )

    def push(self, pcm_frame):
        if self.state is None:
            raise RuntimeError("ASR speech segment has not been started")
        if pcm_frame:
            waveform = np.frombuffer(pcm_frame, dtype="<i2").astype(np.float32) / 32768.0
            probe("speech_pcm_forwarded", float(np.sqrt(np.mean(waveform.astype(np.float64) ** 2))))
            self.model.streaming_transcribe(waveform, self.state)

    def finish(self):
        probe("recognition_lifecycle", "finish")
        state, self.state = self.state, None
        if state is None:
            return ""
        self.model.finish_streaming_transcribe(state)
        probe("recognized_text_final", state.text.strip())
        return state.text.strip()

    def close(self):
        probe("recognition_lifecycle", "close")
        self.state = None
        # qwen-asr 0.0.6 wraps vLLM 0.14.0; stop its engine processes and I/O threads.
        self.model.model.llm_engine.engine_core.shutdown()
