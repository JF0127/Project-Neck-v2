"""Doubao V3 PCM streaming, copied from Project-Neck/runtime/doubao_streaming_v1.py."""

import asyncio
import json
import os
import struct
import uuid

import websockets

URL = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"
DOUBAO_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_DOUBAO_SPEAKER = "zh_male_yuanboxiaoshu_uranus_bigtts"
SAMPLE_RATE = 16_000


def parse_frame(message):
    if not isinstance(message, bytes) or len(message) < 16 or message[0] != 0x11:
        raise ValueError("invalid Doubao V3 WebSocket frame")
    header_len = (message[0] & 15) * 4
    if header_len < 4 or message[1] & 15 != 4 or len(message) < header_len + 12:
        raise ValueError("invalid Doubao V3 event header")
    event = struct.unpack_from(">I", message, header_len)[0]
    sid_len = struct.unpack_from(">I", message, header_len + 4)[0]
    pos = header_len + 8 + sid_len
    if pos + 4 > len(message):
        raise ValueError("truncated Doubao V3 frame")
    size = struct.unpack_from(">I", message, pos)[0]
    end = pos + 4 + size
    if end > len(message):
        raise ValueError("invalid Doubao V3 payload size")
    payload = message[pos + 4:end]
    if message[1] >> 4 == 15:
        raise RuntimeError(f"Doubao V3 error: {payload[:300]!r}")
    return event, payload, end


async def pcm_chunks(reply_text):
    """Yield 16 kHz mono, little-endian PCM16 byte chunks."""
    key = os.environ.get("VOLCENGINE_TTS_API_KEY", "").strip()
    if not key:
        raise ValueError("VOLCENGINE_TTS_API_KEY is required")
    speaker = os.environ.get("VOLCENGINE_TTS_SPEAKER", "").strip() or DEFAULT_DOUBAO_SPEAKER
    headers = {"X-Api-Key": key, "X-Api-Resource-Id": DOUBAO_RESOURCE_ID,
               "X-Api-Request-Id": str(uuid.uuid4())}
    request = {"user": {"uid": "project-neck"}, "req_params": {
        "text": reply_text, "speaker": speaker,
        "audio_params": {"format": "pcm", "sample_rate": SAMPLE_RATE,
                         "speech_rate": 0, "loudness_rate": 0},
        "additions": json.dumps({"disable_markdown_filter": True}, ensure_ascii=False),
    }}
    data = json.dumps(request, ensure_ascii=False).encode()
    async with websockets.connect(URL, additional_headers=headers, open_timeout=15,
                                  close_timeout=1, max_size=None) as ws:
        await ws.send(b"\x11\x10\x10\x00" + struct.pack(">I", len(data)) + data)
        completed = False
        while not completed:
            message = await asyncio.wait_for(ws.recv(), 60)
            while message:
                event, payload, consumed = parse_frame(message)
                message = message[consumed:]
                if event == 352:
                    if not payload or len(payload) % 2:
                        raise ValueError("invalid Doubao PCM chunk")
                    yield payload
                elif event == 152:
                    # Finish this entire batch before leaving the receive loop.
                    completed = True
