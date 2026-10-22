#!/usr/bin/env python3
"""只读项目结构查看器：python3 tools/claude_view/server.py [--port 8765]。"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "third_party/probe"))
from probe import probe


ROOT = Path(__file__).resolve().parents[2]
ROUTES = {
    "/": ("CLAUDE.html", "text/html; charset=utf-8"),
    "/CLAUDE.html": ("CLAUDE.html", "text/html; charset=utf-8"),
    "/module.md": ("module.md", "text/markdown; charset=utf-8"),
}


class ReadOnlyHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        self._serve(send_body=True)

    def do_HEAD(self):
        self._serve(send_body=False)

    def _serve(self, send_body):
        probe("structure_http_request", f"{self.command} {self.path}")
        try:
            route = ROUTES.get(urlsplit(self.path).path)
        except ValueError:
            self.send_error(400, "Invalid request path")
            return
        if route is None:
            self.send_error(404, "Not found")
            return

        filename, content_type = route
        try:
            # 每次请求重新读取；仅允许访问明确列出的两个文件。
            content = (ROOT / filename).read_bytes()
        except FileNotFoundError:
            self.send_error(404, "File not found")
            return
        except OSError:
            self.send_error(500, "Unable to read file")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            try:
                self.wfile.write(content)
                probe("structure_html_response" if filename == "CLAUDE.html"
                      else "structure_markdown_response", content.decode("utf-8", errors="replace"))
            except (BrokenPipeError, ConnectionResetError):
                pass


def port_number(value):
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1 到 65535 之间")
    return port


def main():
    parser = argparse.ArgumentParser(description="只读展示仓库 module.md 的项目结构网页")
    parser.add_argument("--port", type=port_number, default=8765, help="监听端口（默认：8765）")
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), ReadOnlyHandler)
    except OSError as exc:
        parser.exit(1, f"启动失败：{exc}\n")
    with server:
        print(f"项目结构：http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。", flush=True)


if __name__ == "__main__":
    main()
