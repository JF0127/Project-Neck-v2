"""BoardBridge TCP receiver. Callback: callback(event, *args); tau is in seconds."""

import asyncio
import ctypes
import ctypes.util
import json
import struct
import sys

TRAILING_AUDIO_SEC = 0.35  # Control events can overtake the board's queued audio.
HEADERS = {1: "!II", 2: "!IIIHB", 3: "!IIIIHB"}


class OpusDecoder:
    def __init__(self, rate, duration, channels):
        if rate not in (8000, 12000, 16000, 24000, 48000) or channels != 1:
            raise ValueError(f"unsupported Opus format: {rate} Hz, {channels} channels")
        if not 0 < duration <= 120:
            raise ValueError(f"invalid frame duration: {duration}")
        name = ctypes.util.find_library("opus")
        if not name:
            raise RuntimeError("libopus not found; install libopus0")
        self.lib = ctypes.CDLL(name)
        self.lib.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int,
                                                ctypes.POINTER(ctypes.c_int)]
        self.lib.opus_decoder_create.restype = ctypes.c_void_p
        self.lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte),
                                        ctypes.c_int32, ctypes.POINTER(ctypes.c_int16),
                                        ctypes.c_int, ctypes.c_int]
        self.lib.opus_decode.restype = ctypes.c_int
        self.pcm = (ctypes.c_int16 * (rate * duration // 1000))()
        error = ctypes.c_int()
        self.handle = self.lib.opus_decoder_create(rate, 1, ctypes.byref(error))
        if not self.handle or error.value:
            self.close()
            raise RuntimeError(f"Opus decoder init failed: {error.value}")

    def decode(self, packet=None):
        # A NULL packet invokes Opus PLC for exactly one missing frame.
        encoded = ((ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
                   if packet is not None else None)
        count = self.lib.opus_decode(self.handle, encoded, len(packet) if packet else 0,
                                     self.pcm, len(self.pcm), 0)
        if count != len(self.pcm):
            raise RuntimeError(f"Opus decode length/error: {count}, expected {len(self.pcm)}")
        pcm = ctypes.string_at(self.pcm, count * 2)
        if sys.byteorder != "little":
            pcm = b"".join(pcm[i:i + 2][::-1] for i in range(0, len(pcm), 2))
        return pcm

    def close(self):
        if self.handle:
            self.lib.opus_decoder_destroy(self.handle)
            self.handle = None


class Bridge:
    """Emit turn_start, audio, text, turn_end and abort to a synchronous callback."""

    def __init__(self, callback):
        self.callback = callback
        self.turn = self.decoder = self.format = None
        self.first_sequence = self.next_index = 0
        self.texts = {}
        self.ends = {}
        self.audio_ends = {}
        self.closed = set()
        self.connected = False

    def _valid(self, turn):
        return type(turn) is int and turn > 0 and turn not in self.closed

    def _finish(self, turn, aborted=False):
        self.closed.add(turn)
        self.audio_ends.pop(turn, None)
        self.texts.pop(turn, None)  # No following audio means no timestamp for this text.
        timer = self.ends.pop(turn, None)
        if timer:
            timer.cancel()
        if turn == self.turn:
            tau_end = self.next_index * self.format[1] / 1000
            self.decoder.close()
            self.turn = self.decoder = self.format = None
            if not aborted:
                self.callback("turn_end", turn, tau_end)
        if aborted:
            self.callback("abort", turn)

    def _end(self, turn):
        timer = self.ends.pop(turn, None)
        if timer:
            timer.cancel()
        self.ends[turn] = asyncio.get_running_loop().call_later(
            TRAILING_AUDIO_SEC, self._finish, turn)

    def _finish_audio_end(self, turn):
        if (turn == self.turn and self.audio_ends.get(turn) ==
                ((self.first_sequence + self.next_index - 1) & 0xffffffff)):
            self._finish(turn)

    def _message(self, message):
        turn = message.get("turn_id")
        if not self._valid(turn):
            return
        kind = message.get("type")
        if kind == "robot_text":
            text = str(message.get("text", ""))
            if text.strip() and not text.lstrip().startswith("% "):
                self.texts.setdefault(turn, []).append(text)
        elif kind == "robot_audio_end":
            sequence = message.get("last_audio_sequence")
            if type(sequence) is int and 0 <= sequence <= 0xffffffff:
                self.audio_ends[turn] = sequence
                self._finish_audio_end(turn)
        elif kind == "robot_playback_end":
            self._end(turn)
        elif kind == "robot_playback_abort":
            self._finish(turn, aborted=True)

    def _audio(self, turn, sequence, payload, rate, duration, channels):
        if not self._valid(turn):
            return
        if turn != self.turn:
            if self.turn is not None:
                self._finish(self.turn, aborted=self.turn not in self.ends)
            self.decoder = OpusDecoder(rate, duration, channels)
            self.turn, self.format = turn, (rate, duration, channels)
            self.first_sequence, self.next_index = sequence, 0
            self.callback("turn_start", turn, rate)
        if self.format != (rate, duration, channels):
            self._finish(turn, aborted=True)
            return
        index = (sequence - self.first_sequence) & 0xffffffff
        if index < self.next_index or index - self.next_index >= 0x80000000:
            return  # Late/duplicate packet, including uint32 sequence wraparound.
        while self.next_index < index:
            pcm = self.decoder.decode()
            self.callback("audio", turn, pcm, self.next_index * duration / 1000)
            self.next_index += 1
        tau = index * duration / 1000
        # Text is anchored to the next accepted wire packet, not to synthetic PLC.
        for text in self.texts.pop(turn, []):
            self.callback("text", turn, text, tau)
        pcm = self.decoder.decode(payload)
        self.callback("audio", turn, pcm, tau)
        self.next_index = index + 1
        self._finish_audio_end(turn)
        if turn in self.ends:
            self._end(turn)

    def _disconnect(self):
        if self.turn is not None:
            self._finish(self.turn, aborted=True)
        for timer in self.ends.values():
            timer.cancel()
        self.ends.clear()
        self.audio_ends.clear()
        self.texts.clear()
        self.closed.clear()

    async def _client(self, reader, writer):
        if self.connected:
            writer.close()
            await writer.wait_closed()
            return
        self.connected = True
        try:
            while marker := await reader.read(1):
                if marker in (b"\n", b"\r"):
                    continue
                if marker == b"{":
                    self._message(json.loads(marker + await reader.readline()))
                    continue
                header = HEADERS.get(marker[0])
                if header is None:
                    raise ValueError(f"unknown frame type: {marker.hex()}")
                fields = struct.unpack(header, await reader.readexactly(struct.calcsize(header)))
                sequence, length = fields[:2]
                if not 0 < length <= 8192:
                    raise ValueError(f"invalid audio length: {length}")
                payload = await reader.readexactly(length)
                if marker == b"\x03":
                    self._audio(fields[2], sequence, payload, *fields[3:])
                # Consume microphone (1) and legacy untagged robot (2) packets,
                # just as v1 does; only type 3 identifies a robot turn.
        except (OSError, ValueError, RuntimeError, asyncio.IncompleteReadError) as exc:
            print(f"bridge: {exc}", file=sys.stderr, flush=True)
        finally:
            self._disconnect()
            self.connected = False
            writer.close()
            await writer.wait_closed()

    async def serve(self, host="0.0.0.0", port=8766):
        server = await asyncio.start_server(self._client, host, port)
        async with server:
            await server.serve_forever()
