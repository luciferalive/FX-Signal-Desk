"""Symbol helpers shared by the signal engine, the news playbook and the trading layer."""
from __future__ import annotations

OIL_NAMES = {"USOIL", "WTI", "WTIUSD", "XTIUSD"}   # US crude oil (WTI). Free data: Yahoo's CL=F futures


def clean(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("=X", "")


def is_oil(symbol: str) -> bool:
    return clean(symbol) in OIL_NAMES


def is_metal(symbol: str) -> bool:
    return clean(symbol).startswith(("XAU", "XAG"))


def split(symbol: str) -> tuple[str, str]:
    """(base, quote). Oil is priced in dollars, like gold: ('OIL', 'USD')."""
    s = clean(symbol)
    if is_oil(s):
        return "OIL", "USD"
    return s[:3], s[3:6]
