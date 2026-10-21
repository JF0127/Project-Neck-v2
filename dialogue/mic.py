"""PulseAudio capture adapted from Project-Neck/runtime/audio/runtime/audio_capture.py."""

import os
import queue
import subprocess
import threading

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_BYTES = 640


class MicrophoneCapture:
    def __init__(self, source=None, queue_size=250):
        self.source = source or os.environ.get("AUDIO_MODULE_PULSE_SOURCE")
        self.port = os.environ.get("AUDIO_MODULE_PULSE_SOURCE_PORT")
        self.queue_size = queue_size
        self.frames = queue.Queue(maxsize=queue_size)
        self.process = self.thread = None

    def _read(self, process):
        pending = bytearray()
        while chunk := process.stdout.read(FRAME_BYTES):
            pending.extend(chunk)
            while len(pending) >= FRAME_BYTES:
                frame = bytes(pending[:FRAME_BYTES])
                del pending[:FRAME_BYTES]
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    pass  # Never block the capture pipe on slow inference.

    def start(self):
        if self.process is not None:
            return
        if self.port:
            subprocess.run(["pactl", "set-source-port",
                            self.source or "@DEFAULT_SOURCE@", self.port], check=True)
        command = ["parec", "--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1",
                   f"--latency-msec={FRAME_MS}", f"--process-time-msec={FRAME_MS}", "--raw"]
        if self.source:
            command.append(f"--device={self.source}")
        # A restarted microphone must not replay audio from before the robot's reply.
        self.frames = queue.Queue(maxsize=self.queue_size)
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)
        self.thread = threading.Thread(target=self._read, args=(self.process,),
                                       name="dialogue-microphone")
        self.thread.start()

    def read_frame(self, timeout=0.1):
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("parec microphone capture is not running") from None
            raise

    def stop(self):
        if self.process is None:
            return
        process = self.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join()
        process.stdout.close()
        self.process = self.thread = None
