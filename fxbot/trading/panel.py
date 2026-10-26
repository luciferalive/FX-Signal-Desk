"""Local control panel for the trading engine: account, positions, bot status, history and the controls.

It is deliberately NOT part of the public GitHub Pages dashboard: a public page must never hold broker credentials or
send orders. This panel runs on YOUR computer, listens on 127.0.0.1 only, never touches the broker itself (it reads the
status file the engine writes and sends commands through the command file) and every action needs the session token.
"""
from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .audit import AuditLog
from .state import CommandQueue

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "trading.html")
ACTIONS = {"pause", "resume", "stop", "start", "close", "close_all", "liquidate", "unlock"}
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def make_handler(status_path: str, audit_path: str, commands: CommandQueue, token: str, live: bool):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):   # quiet
            pass

        def _host_ok(self) -> bool:  # blocks DNS-rebinding: only requests addressed to localhost are served
            return self.headers.get("Host", "").split(":")[0] in LOCAL_HOSTS

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"{}")
            path = self.path.split("?")[0]
            if path == "/":
                html = open(TEMPLATE, encoding="utf-8").read().replace("__TOKEN__", token).replace("__LIVE__", "true" if live else "false")
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if path == "/api/status":
                try:
                    return self._send(200, open(status_path, "rb").read())
                except OSError:
                    return self._send(200, json.dumps({"mode": "DISCONNECTED", "error": "the engine is not running (start: python -m fxbot trade run)"}).encode())
            if path == "/api/audit":
                rows = AuditLog(audit_path).read(last=200)
                return self._send(200, json.dumps(rows[::-1]).encode())
            self._send(404, b"{}")

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Token") != token:
                return self._send(403, b'{"error":"forbidden"}')
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, b'{"error":"bad json"}')
            action = body.get("action")
            if action not in ACTIONS:
                return self._send(400, b'{"error":"unknown action"}')
            args = {k: v for k, v in (body.get("args") or {}).items() if k in ("position_id", "confirm")}
            cid = commands.push(action, **args)
            self._send(200, json.dumps({"queued": cid, "note": "the engine applies it on its next tick (within about 30 seconds)"}).encode())
    return H


def serve(profile: str, state_dir: str = ".state", port: int = 8765, live: bool = False) -> None:
    status = os.path.join(state_dir, f"status_{profile}.json")
    audit = os.path.join(state_dir, f"audit_{profile}.jsonl")
    cmds = CommandQueue(os.path.join(state_dir, f"commands_{profile}.jsonl"))
    token = secrets.token_hex(16)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(status, audit, cmds, token, live))
    print(f"Trading panel: http://127.0.0.1:{port}/   (this computer only, Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
