"""通过 UDP 发送观测数据，支持 PROBE_HOST、PROBE_PORT、PROBE_MAX_HZ 和 PROBE_OFF。"""

from collections import deque
import json
import os
import socket
import threading
import time


_socket = None
_destination = None
_initialized = False
_max_hz = 100
_lock = threading.Lock()
_recent = {}


def _initialize():
    # 仅在持锁时调用；初始化失败也不重复创建 socket。
    global _socket, _destination, _initialized, _max_hz
    _initialized = True
    try:
        _max_hz = max(0, int(os.environ.get("PROBE_MAX_HZ", "100")))
    except ValueError:
        _max_hz = 100

    if _max_hz == 0:
        return

    try:
        _destination = (
            os.environ.get("PROBE_HOST", "127.0.0.1"),
            int(os.environ.get("PROBE_PORT", "8766")),
        )
        _socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _socket.setblocking(False)
    except (OSError, ValueError):
        if _socket is not None:
            _socket.close()
        _socket = None


def probe(name, value):
    """按名称限制滚动一秒内的调用次数；序列化或发送失败时静默忽略。"""
    if os.environ.get("PROBE_OFF") == "1":
        return

    try:
        with _lock:
            if not _initialized:
                _initialize()
            if _socket is None or _max_hz == 0:
                return

            now = time.monotonic()
            recent = _recent.get(name)
            if recent is None:
                recent = deque()
                _recent[name] = recent
            while recent and now - recent[0] >= 1.0:
                recent.popleft()
            if len(recent) >= _max_hz:
                return
            recent.append(now)

        payload = {"name": name, "t": time.time(), "value": value}
        data = json.dumps(payload).encode("utf-8")
        _socket.sendto(data, _destination)
    except Exception:
        pass
