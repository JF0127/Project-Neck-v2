"""HTTP and single-key controls for voice dialogue."""

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import select
import sys
import termios
import threading
import time
import tty


class Controls:
    def __init__(self, host, port, log_dir, on_change, on_quit):
        self.lock = threading.RLock()
        self.state = "listening"
        self.revision = 0  # Manual changes invalidate even a quickly resumed input/turn.
        self.stopped = threading.Event()
        self.on_change, self.on_quit = on_change, on_quit
        self.terminal = self.key_thread = None
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / datetime.now().strftime("states_%Y%m%d_%H%M%S_%f.jsonl")
        control = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, body):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path != "/state":
                    self.reply(404, {"error": "not found"})
                    return
                with control.lock:
                    state = control.state
                self.reply(200, {"state": state})

            def do_POST(self):
                if self.path == "/toggle":
                    state = control.change(None, "api")
                elif self.path == "/state":
                    try:
                        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    except (ValueError, UnicodeDecodeError):
                        self.reply(400, {"error": "invalid JSON"})
                        return
                    if not isinstance(body, dict) or body.get("state") not in ("waiting", "listening"):
                        self.reply(400, {"error": "state must be waiting or listening"})
                        return
                    state = control.change(body["state"], "api")
                else:
                    self.reply(404, {"error": "not found"})
                    return
                self.reply(200, {"state": state})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.http_thread = threading.Thread(target=self.server.serve_forever,
                                            name="dialogue-http", daemon=True)

    def change(self, state, source="auto"):
        with self.lock:
            if self.stopped.is_set():
                return self.state
            if state is None:
                state = "listening" if self.state == "waiting" else "waiting"
            previous = self.state
            if previous == state:
                return state
            stamp, mono = datetime.now().astimezone().isoformat(), time.monotonic()
            self.state = state
            if source != "auto":
                self.revision += 1
            interrupted = self.on_change(previous, state, source)
            event = {"at": stamp, "at_mono": mono, "from": previous, "to": state,
                     "source": source, "interrupted_reply": interrupted}
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
            print(f"\n[状态] {previous} → {state}（来源：{source}）", flush=True)
            return state

    def quit(self):
        with self.lock:
            if not self.stopped.is_set():
                self.stopped.set()
                self.on_quit()

    def _keys(self):
        while not self.stopped.is_set():
            if not select.select([sys.stdin], [], [], 0.1)[0]:
                continue
            key = os.read(sys.stdin.fileno(), 1)
            if not key:
                return
            if key == b" ":
                self.change(None, "key")
            elif key == b"q":
                self.quit()

    def start(self):
        self.http_thread.start()
        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
            self.terminal = (fd, termios.tcgetattr(fd))
            tty.setcbreak(fd)
            self.key_thread = threading.Thread(target=self._keys, name="dialogue-keys", daemon=True)
            self.key_thread.start()
        host, port = self.server.server_address
        print(f"状态接口：http://{host}:{port}；状态日志：{self.log_path}", flush=True)

    def close(self):
        try:
            self.quit()
            if self.http_thread.ident is not None:
                self.server.shutdown()
                self.http_thread.join()
            self.server.server_close()
            if self.key_thread is not None and self.key_thread.ident is not None:
                self.key_thread.join()
        finally:
            if self.terminal is not None:
                fd, settings = self.terminal
                termios.tcsetattr(fd, termios.TCSADRAIN, settings)
