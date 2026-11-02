import numpy as np
import pandas as pd

from fxbot import patterns as P


def mk(path):
    idx = pd.date_range("2026-01-01", periods=len(path), freq="15min", tz="UTC")
    c = np.array(path, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(dict(open=o, high=np.maximum(o, c) + 0.0004, low=np.minimum(o, c) - 0.0004, close=c), index=idx)


def zig(pts, per=12):
    out = []
    for a, b in zip(pts, pts[1:]):
        out += list(np.linspace(a, b, per, endpoint=False))
    out.append(pts[-1])
    return out


def names(df):
    return {p["name"]: p for p in P.more_patterns(df)}


RW = [1.108, 1.100, 1.110, 1.106, 1.114, 1.111, 1.1165, 1.112, 1.1085, 1.104]
TT = [1.10, 1.12, 1.105, 1.12, 1.105, 1.12, 1.100]


def test_triple_top_and_its_mirror_bottom():
    p = names(mk([1.10] * 70 + zig(TT)))["Triple Top"]
    assert p["side"] == -1 and p["status"] == "confirmed" and p["target"] < p["neckline"] < 1.12 < p["sl"]
    q = names(mk([2.2 - x for x in [1.10] * 70 + zig(TT)]))["Triple Bottom"]
    assert q["side"] == 1 and q["sl"] < q["neckline"] < q["target"]


def test_rising_wedge_forming_then_mirror_is_falling_wedge():
    df = mk([1.108] * 70 + zig(RW[:7]) + [1.1160, 1.1150])
    p = names(df)["Rising Wedge"]
    assert p["side"] == -1 and p["status"] == "forming" and len(p["lines"]) == 2
    f = names(mk([2.22 - x for x in [1.108] * 70 + zig(RW[:7]) + [1.1160, 1.1150]]))
    assert f["Falling Wedge"]["side"] == 1


def test_rising_wedge_break_is_a_sell_candidate():
    df = mk([1.108] * 70 + zig(RW[:7]) + [1.1130, 1.1090, 1.1060])
    p = names(df).get("Rising Wedge")
    assert p and p["status"] == "confirmed" and p["side"] == -1 and p["target"] < p["neckline"]


def test_bull_flag_breakout_has_target_above_entry():
    path = [1.10] * 70 + [1.10] * 8 + list(np.linspace(1.10, 1.13, 8)) + [1.128, 1.126, 1.1245, 1.123, 1.1215, 1.12, 1.1185,
            1.117, 1.1175, 1.1165, 1.116, 1.1170, 1.1260, 1.131, 1.134]
    p = names(mk(path))["Bull Flag"]
    assert p["side"] == 1 and p["status"] == "confirmed" and p["target"] > 1.134 > p["sl"]


def test_rectangle_range_and_breakout():
    base = [1.10] * 70 + zig([1.10, 1.11, 1.10, 1.11, 1.10, 1.11, 1.10], 8)
    assert names(mk(base + [1.104, 1.106, 1.105, 1.104]))["Rectangle (range)"]["side"] == 0
    p = names(mk(base + [1.1055, 1.1105, 1.1115, 1.1130]))["Rectangle Breakout Up"]
    assert p["side"] == 1 and p["target"] > 1.12


def test_channel_is_context_only_no_trade_levels():
    df = mk([1.10 + 0.0004 * i + 0.003 * np.sin(i / 3.0) for i in range(160)])
    p = names(df)["Ascending Channel"]
    assert p["side"] == 1 and "target" not in p


def test_candle_patterns():
    # red, tiny candle, green closing above the middle of the red one = morning star
    base = [1.10] * 60
    df = mk(base + [1.10, 1.09, 1.0895, 1.099])
    df.loc[df.index[-3], "open"] = 1.10
    assert any(n == "Morning Star" for n, _, _ in P.more_candles(df, 0.004))


def test_support_resistance_needs_two_separate_touches():
    df = mk([1.10] * 70 + zig([1.10, 1.11, 1.10, 1.11, 1.10, 1.11, 1.10], 8) + [1.1055])
    lv = P.sr_levels(df)
    assert lv and all(L["kind"] == "sr" and L["touches"] >= 2 for L in lv)
    assert any(L["name"].startswith("Support") for L in lv)


def test_flat_noise_gives_no_patterns():
    rng = np.random.default_rng(1)
    df = mk(1.10 + np.cumsum(rng.normal(0, 0.0002, 200)) * 0)
    assert P.more_patterns(df) == [] or all(p["side"] == 0 for p in P.more_patterns(df))


def test_telegram_card_is_line_by_line_with_both_targets_and_no_reasons():
    from fxbot.alerts import card
    t = card(symbol="EURUSD", side="BUY", strategy="ICT sweep + FVG", grade="A", score_text="score 83/100", frames="15m setup",
             ts="2026-11-02T05:15:00+00:00", bar_min=5, entry=1.1523, entry_type="limit", sl=1.1506, tp1=1.154, tp2=1.1557, dec=5,
             expiry_bars=8)
    for need in ("BUY  EURUSD", "Grade A", "IST", "UTC", "NY", "Session", "ENTRY TYPE: LIMIT", "SL ", "TP1 ", "TP2 ", "WHEN TO TAKE IT", "Valid until"):
        assert need in t, need
    assert "Why" not in t and t.count("\n") > 15
    m = card(symbol="EURUSD", side="SELL", strategy="x", grade="B", score_text="s", frames="f", ts="2026-11-02T05:15:00+00:00",
             bar_min=5, entry=1.1523, entry_type="market", sl=1.1540, tp1=1.1506, tp2=1.1489, dec=5)
    assert "MARKET" in m and "Skip it if price is already beyond 1.15145" in m
