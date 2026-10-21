"""Qwen3-ASR streaming adapted from Project-Neck/runtime/qwen_streaming_runtime.py."""

import os
from pathlib import Path

import numpy as np

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
        self.state = self.model.init_streaming_state(
            language="Chinese", chunk_size_sec=1.0,
            unfixed_chunk_num=4, unfixed_token_num=5,
        )

    def push(self, pcm_frame):
        if self.state is None:
            raise RuntimeError("ASR speech segment has not been started")
        if pcm_frame:
            waveform = np.frombuffer(pcm_frame, dtype="<i2").astype(np.float32) / 32768.0
            self.model.streaming_transcribe(waveform, self.state)

    def finish(self):
        state, self.state = self.state, None
        if state is None:
            return ""
        self.model.finish_streaming_transcribe(state)
        return state.text.strip()

    def close(self):
        self.state = None
        # qwen-asr 0.0.6 wraps vLLM 0.14.0; stop its engine processes and I/O threads.
        self.model.model.llm_engine.engine_core.shutdown()
