"""Persistent state: survives application restarts so a restart can never open duplicate trades or forget a loss lock.

One JSON file per profile (.state/trading_<profile>.json), written atomically. Operator commands (pause, stop, close)
go through a tiny command file so a separate process (the CLI or the local panel) can control a running engine
without ever holding broker credentials."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field

import pandas as pd


# Order / trade lifecycle. Only these moves are legal; anything else is a bug and raises instead of corrupting state.
LEGAL = {
    "CREATED": {"VALIDATING", "ERROR"},
    "VALIDATING": {"APPROVED", "REJECTED", "ERROR"},
    "APPROVED": {"SUBMITTED", "CANCELLED", "ERROR"},
    "SUBMITTED": {"PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELLED", "ERROR"},
    "PARTIALLY_FILLED": {"FILLED", "CANCELLED", "CLOSING", "CLOSED", "ERROR"},
    "FILLED": {"CLOSING", "CLOSED", "ERROR"},
    "CLOSING": {"CLOSED", "FILLED", "ERROR"},
    "ERROR": {"FILLED", "PARTIALLY_FILLED", "CANCELLED", "REJECTED", "CLOSED"},   # an unconfirmed order that is resolved later
    "REJECTED": set(), "CANCELLED": set(), "CLOSED": set(),
}


class IllegalTransition(Exception):
    pass


def transition(current: str | None, new: str) -> str:
    if current is None:
        current = "CREATED"
    if new != current and new not in LEGAL.get(current, set()):
        raise IllegalTransition(f"{current} -> {new} is not allowed")
    return new


@dataclass
class Trade:
    trade_id: str
    position_id: str
    client_id: str
    symbol: str
    side: int
    strategy: str
    family: str
    style: str
    signal_entry: float
    entry: float                    # the price actually filled
    initial_sl: float
    sl: float
    tps: list[float]                # [TP1] | [TP1, TP2] | [TP1, TP2, TP3]
    exit_volumes: list[float]       # planned close volume per target, 0 = skipped, last = None -> "the rest"
    volume_orig: float
    volume_remaining: float
    opened_at: str
    realized_pnl: float = 0.0
    tp_done: int = 0                # how many targets are done
    be_done: bool = False
    state: str = "FILLED"           # FILLED | PARTIALLY_FILLED | CLOSING | CLOSED | ERROR
    closed_at: str | None = None
    close_reason: str | None = None
    managed: bool = True            # False = a position we did not open (manual): counted in risk, never touched
    atr: float | None = None
    remaining_risk: float = 0.0
    stop_failures: int = 0
    allow_widen: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def volume_closed(self) -> float:
        return round(self.volume_orig - self.volume_remaining, 8)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["volume_closed"] = self.volume_closed
        return d


_DEFAULT = {
    "version": 1,
    "day_key": None, "day_start_equity": None,
    "week_key": None, "week_start_equity": None,
    "lock": None,                       # {"kind": "daily"|"weekly", "reason", "since", "key"}
    "paused": False, "stopped": False,
    "trades": {},                       # trade_id -> Trade dict
    "intents": {},                      # client_id -> {status, symbol, ... , submitted_at}
    "entries": {},                      # "<day>|<symbol>" -> count
    "session_entries": {},              # "<day>|<session>" -> count
    "cooldowns": {},                    # "<strategy>|<symbol>" -> iso time when the trade closed
    "seen_signals": [],
}


class StateStore:
    def __init__(self, path: str | None):
        self.path = path
        self._lock = threading.RLock()
        self.data = json.loads(json.dumps(_DEFAULT))
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in loaded.items():
                self.data[k] = v

    # ---- persistence
    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            d = os.path.dirname(self.path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_state_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1, default=str)
            os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written file

    # ---- trades
    def trades(self, open_only: bool = False) -> list[Trade]:
        out = [Trade(**t) for t in self.data["trades"].values()]
        return [t for t in out if t.state != "CLOSED"] if open_only else out

    def put_trade(self, t: Trade) -> None:
        self.data["trades"][t.trade_id] = t.to_dict()
        self.data["trades"][t.trade_id].pop("volume_closed", None)

    def get_trade(self, trade_id: str) -> Trade | None:
        d = self.data["trades"].get(trade_id)
        return Trade(**d) if d else None

    # ---- intents (orders we decided to send), written BEFORE sending so a crash can be reconciled
    def put_intent(self, client_id: str, **fields) -> None:
        cur = self.data["intents"].setdefault(client_id, {})
        cur.update(fields)

    def intent(self, client_id: str) -> dict | None:
        return self.data["intents"].get(client_id)

    # ---- counters
    def count_entry(self, day: str, symbol: str, session: str | None) -> None:
        k = f"{day}|{symbol}"
        self.data["entries"][k] = self.data["entries"].get(k, 0) + 1
        if session:
            k2 = f"{day}|{session}"
            self.data["session_entries"][k2] = self.data["session_entries"].get(k2, 0) + 1

    def entries_today(self, day: str, symbol: str) -> int:
        return self.data["entries"].get(f"{day}|{symbol}", 0)

    def session_count(self, day: str, session: str) -> int:
        return self.data["session_entries"].get(f"{day}|{session}", 0)

    def mark_seen(self, signal_id: str, keep: int = 2000) -> None:
        if signal_id not in self.data["seen_signals"]:
            self.data["seen_signals"].append(signal_id)
            self.data["seen_signals"] = self.data["seen_signals"][-keep:]

    def seen(self, signal_id: str) -> bool:
        return signal_id in self.data["seen_signals"]

    def prune(self, keep_days: int = 14) -> None:
        """Forget old counters; trade records stay."""
        for name in ("entries", "session_entries"):
            ks = sorted(self.data[name])
            for k in ks[:-400]:
                self.data[name].pop(k, None)


# ---------------------------------------------------------------- operator commands (separate process safe)
class CommandQueue:
    def __init__(self, path: str | None):
        self.path = path

    def push(self, action: str, **args) -> str:
        cid = uuid.uuid4().hex[:8]
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": cid, "action": action, "args": args,
                                    "time": pd.Timestamp.now(tz="UTC").isoformat()}) + "\n")
        return cid

    def drain(self) -> list[dict]:
        if not self.path or not os.path.exists(self.path):
            return []
        tmp = self.path + f".{uuid.uuid4().hex[:6]}.work"
        try:
            os.replace(self.path, tmp)  # take the file; commands pushed meanwhile start a fresh one
        except OSError:
            return []
        out = []
        with open(tmp, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        os.remove(tmp)
        return out
