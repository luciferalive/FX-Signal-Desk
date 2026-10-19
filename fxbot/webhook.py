"""Tiny webhook receiver for TradingView alerts -> Telegram/Discord.
NOTE: TradingView only sends webhooks on a paid plan. The scanner works without it."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from .notify import send


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        secret = os.getenv("WEBHOOK_SECRET")
        if secret:
            try:
                ok = json.loads(body).get("secret") == secret
            except Exception:
                ok = False
            if not ok and self.path.rstrip("/").split("?secret=")[-1] != secret:
                self.send_response(403)
                self.end_headers()
                return
        send(f"📈 TradingView alert\n{body[:1500]}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def serve(port: int = 8080) -> None:
    print(f"Webhook listening on :{port}  (expose with a free tunnel, e.g. cloudflared / ngrok)")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
