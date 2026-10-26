"""Audit trail: every decision, order and change is one JSON line, so you can always see WHY the bot entered or rejected.
Secrets are scrubbed before anything is written or printed."""
from __future__ import annotations

import json
import os
import re
import threading

import pandas as pd

_SECRET_KEY = re.compile(r"token|password|passwd|secret|api[_-]?key|authorization|credential|bearer", re.I)
_ENV_SECRETS = ("OANDA_API_TOKEN", "TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "MT5_PASSWORD")


def scrub(obj):
    """Mask secret-looking keys and any known secret value found inside strings."""
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        for name in _ENV_SECRETS:
            v = os.getenv(name)
            if v and len(v) >= 6:
                obj = obj.replace(v, "***")
        return obj
    return obj


def _default(o):
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:
            pass
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


class AuditLog:
    def __init__(self, path: str | None):
        self.path = path
        self._lock = threading.Lock()
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def write(self, event: str, now=None, **fields) -> dict:
        rec = {"time": pd.Timestamp(now if now is not None else pd.Timestamp.now(tz="UTC")).isoformat(), "event": event}
        rec.update(fields)
        rec = scrub(json.loads(json.dumps(rec, default=_default)))
        if self.path:
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return rec

    def read(self, last: int | None = None, event: str | None = None) -> list[dict]:
        if not self.path or not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if event is None or r.get("event") == event:
                    out.append(r)
        return out[-last:] if last else out
