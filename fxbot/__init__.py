"""fxbot - free forex signal scanner / backtester (ICT + Set & Forget)."""
import os

__version__ = "0.3.0"

_FALLBACK = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDUSD", "USDCAD", "XAUUSD"]


def load_symbols(path: str = "pairs.txt") -> list[str]:
    """Pairs come from pairs.txt (one per line, # for comments) so adding a pair is a one-line edit."""
    try:
        with open(path, encoding="utf-8") as f:
            rows = [ln.split("#")[0].strip().upper().replace("/", "") for ln in f]
        syms = [r for r in rows if r]
        return syms or list(_FALLBACK)
    except OSError:
        return list(_FALLBACK)


DEFAULT_SYMBOLS = load_symbols()
