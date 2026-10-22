"""Tests for the multi-strategy engine: signal standardisation, regime, risk, confluence, the 1h/15m/5m workflow,
deduplication, look-ahead safety and the dashboard output."""
import numpy as np
import pandas as pd
import pytest

from fxbot import config
from fxbot.backtest import breakdown, run_backtest, split_backtest, trade_outcome
from fxbot.confluence import combine, group
from fxbot.data import resample, synthetic
from fxbot.regime import current_regime, regime_series
from fxbot.risk import atr_stop, dedupe, invalidated, position_size, validate
from fxbot.strategies import FAMILIES, STRATEGIES, Signal
from fxbot.workflow import evaluate, plan, setups

CASES = [(n, m) for n, c in STRATEGIES.items() for m in c.PRESETS]
_cache = {}


def frames(days=300):
    if days not in _cache:
        raw = synthetic("15m", days=days)
        _cache[days] = {"scalp": raw, "swing": resample(raw, "4h"), "daily": resample(raw, "1d")}
    return _cache[days]


def test_all_thirteen_strategies_registered_with_families():
    assert len(STRATEGIES) >= 13
    for name, cls in STRATEGIES.items():
        d = cls.describe()
        assert d["family"] in FAMILIES and d["label"] and d["summary"] and d["modes"], name


@pytest.mark.parametrize("name,mode", CASES)
def test_signals_are_standard_and_sane(name, mode):
    df = frames()[mode]
    sigs = STRATEGIES[name](mode).generate(df, "EURUSD")
    for s in sigs:
        assert isinstance(s, Signal) and s.direction in ("BUY", "SELL")
        assert s.family == STRATEGIES[name].family and s.timeframe and s.regime and s.invalidation and s.reason
        assert (s.entry - s.sl) * s.side > 0, "stop must be on the losing side"
        assert (s.tp - s.entry) * s.side > 0, "target must be on the winning side"
        assert s.rr > 0 and len(s.targets) == 2
        assert pd.Timestamp(s.time) == df.index[s.idx]


@pytest.mark.parametrize("name,mode", CASES)
def test_no_lookahead_truncation(name, mode):
    """Signals found on the first 80% of the data must be identical when computed with only that 80%."""
    df = frames()[mode]
    k = int(len(df) * 0.8)
    full = {(s.idx, s.side, round(s.entry, 6), round(s.sl, 6)) for s in STRATEGIES[name](mode).generate(df, "EURUSD") if s.idx < k - 1}
    part = {(s.idx, s.side, round(s.entry, 6), round(s.sl, 6)) for s in STRATEGIES[name](mode).generate(df.iloc[:k], "EURUSD") if s.idx < k - 1}
    assert full == part


@pytest.mark.parametrize("name,mode", CASES)
def test_flat_market_gives_no_signal(name, mode):
    idx = pd.date_range("2025-01-06", periods=1500, freq="15min", tz="UTC")
    df = pd.DataFrame(dict(open=1.1, high=1.1, low=1.1, close=1.1, volume=0.0), index=idx)
    if mode != "scalp":
        df = resample(df, "4h" if mode == "swing" else "1d")
    assert STRATEGIES[name](mode).generate(df, "EURUSD") == []


def test_regime_labels_and_causality():
    df = frames()["swing"]
    lab = regime_series(df)
    assert set(lab) <= {"trending", "ranging", "volatile", "transition", "unknown"} and len(lab) == len(df)
    k = len(df) // 2
    assert (regime_series(df.iloc[:k]) == lab[:k]).all()  # a bar's label never depends on later bars
    cr = current_regime(df)
    assert cr["label"] == lab[-1]


def test_volatility_spike_is_volatile():
    df = frames()["scalp"].copy()
    df.iloc[-3:, df.columns.get_loc("high")] += 0.05
    df.iloc[-3:, df.columns.get_loc("low")] -= 0.05
    assert current_regime(df)["label"] == "volatile"


# ---------------------------------------------------------------- risk
def test_position_size_math():
    p = position_size(10_000, 1.0, 1.1000, 1.0950, "EURUSD")  # 50 pip stop, $100 risk, $10/pip/lot -> 0.2 lots
    assert p["stop_pips"] == 50.0 and p["risk_amount"] == 100.0 and p["lots"] == 0.2 and p["exact"]
    j = position_size(10_000, 1.0, 150.00, 149.50, "USDJPY")  # 50 pips
    assert j["stop_pips"] == 50.0 and 0.2 < j["lots"] < 0.4 and j["exact"]
    assert position_size(10_000, 1.0, 1.1, 1.1, "EURUSD")["lots"] == 0.0


def test_atr_stop_direction():
    assert atr_stop(1.1, 0.001, 1, 1.5) < 1.1 and atr_stop(1.1, 0.001, -1, 1.5) > 1.1


def test_validate_rejects_bad_trades():
    cfg = config.load("does-not-exist.json")
    good = dict(side=1, entry=1.1000, sl=1.0970, tp=1.1090)  # 30 pips risk, RR 3
    assert validate(good, 0.0015, "EURUSD", cfg)[0]
    assert not validate(dict(good, sl=1.1010), 0.0015, "EURUSD", cfg)[0]  # stop on the wrong side
    assert not validate(dict(good, tp=1.1010), 0.0015, "EURUSD", cfg)[0]  # RR 0.33
    assert not validate(dict(good, sl=1.0999), 0.0015, "EURUSD", cfg)[0]  # stop inside the noise / spread
    assert not validate(dict(good, sl=1.0800, tp=1.2000), 0.0015, "EURUSD", cfg)[0]  # stop far too wide
    assert not validate(good, 0.0015, "EURUSD", cfg, vol_ratio=3.5)[0]  # too volatile
    assert not validate(good, 0.0015, "EURUSD", cfg, vol_ratio=0.1)[0]  # dead market
    cfg["risk"]["sessions"] = ["London KZ"]
    assert not validate(good, 0.0015, "EURUSD", cfg, session="Asia")[0]
    assert validate(good, 0.0015, "EURUSD", cfg, session="London KZ")[0]
    cfg["risk"]["risk_pct"] = 5.0
    assert not validate(good, 0.0015, "EURUSD", cfg, session="London KZ")[0]  # above the maximum risk per trade


def test_family_specific_minimum_rr():
    cfg = config.load("does-not-exist.json")
    t = dict(side=1, entry=1.1000, sl=1.0970, tp=1.1033)  # RR 1.1
    assert not validate(t, 0.0015, "EURUSD", cfg)[0]
    assert validate(t, 0.0015, "EURUSD", cfg, family="mean_reversion")[0]


def test_config_override(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"risk": {"min_rr": 3.0}, "strategies": {"enabled": ["donchian"]}}')
    cfg = config.load(str(p))
    assert cfg["risk"]["min_rr"] == 3.0 and cfg["risk"]["risk_pct"] == 1.0  # others keep their defaults
    assert config.enabled_strategies(cfg) == ["donchian"]


# ---------------------------------------------------------------- dedupe / confluence
def _sig(idx, side=1, strat="trendpull", fam="trend"):
    return Signal(idx, pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(minutes=15 * idx), "EURUSD", strat, "scalp", side,
                  1.1, 1.099 if side == 1 else 1.101, 1.102 if side == 1 else 1.098, 2.0, "market", 0, "t", "London KZ", fam, "15m", "trending", "x")


def test_dedupe_removes_repeats_but_keeps_other_sides_and_strategies():
    sigs = [_sig(10), _sig(12), _sig(30), _sig(11, side=-1), _sig(13, strat="donchian", fam="breakout")]
    out = dedupe(sigs, 8)
    assert [(s.idx, s.strategy, s.side) for s in out] == [(10, "trendpull", 1), (11, "trendpull", -1), (13, "donchian", 1), (30, "trendpull", 1)]


def test_confluence_counts_independent_families_only():
    two_trend = [_sig(5, strat="trendpull"), _sig(5, strat="emacross")]
    mixed = [_sig(5, strat="trendpull"), _sig(5, strat="donchian", fam="breakout")]
    a = combine(two_trend, "trending", "BUY", True)
    b = combine(mixed, "trending", "BUY", True)
    assert a["families"] == ["trend"] and len(a["signals"]) == 2
    assert b["score"] > a["score"], "a second independent idea must add evidence, a second trend indicator must not"
    assert set(group(mixed)[1]) == {"trend", "breakout"}


def test_opposing_evidence_lowers_score_and_none_gives_no_side():
    alone = combine([_sig(5)], "trending", "BUY", True)
    fought = combine([_sig(5), _sig(5, side=-1, strat="rsi2", fam="mean_reversion")], "trending", "BUY", True)
    assert fought["score"] < alone["score"] or fought["side"] != alone["side"]
    assert combine([], "trending", "BUY", True)["side"] == 0


def test_invalidated_and_outcome():
    df = frames()["scalp"]
    s = _sig(100)
    s.entry, s.sl, s.tp = float(df["close"].iloc[100]), float(df["close"].iloc[100]) - 100, float(df["close"].iloc[100]) + 100
    assert invalidated(s, df) is None
    s.sl = float(df["close"].iloc[100]) + 0.5 * 0  # stop at entry on the long side: touched immediately
    s.sl = float(df["low"].iloc[101:].min()) + 1e-6
    assert invalidated(s, df) is not None
    s.sl, s.tp = float(df["close"].iloc[100]) - 100, float(df["close"].iloc[100]) + 100
    assert trade_outcome(df, s) == "open"


# ---------------------------------------------------------------- backtester honesty
def test_same_candle_conflict_counts_as_loss_and_costs_reduce_r():
    idx = pd.date_range("2025-01-06", periods=50, freq="15min", tz="UTC")
    p = np.full(50, 1.1000)
    df = pd.DataFrame(dict(open=p, high=p + 0.0002, low=p - 0.0002, close=p, volume=0.0), index=idx)
    df.iloc[11, df.columns.get_loc("high")] = 1.1105  # one candle that reaches both the stop and the target
    df.iloc[11, df.columns.get_loc("low")] = 1.0900
    s = Signal(10, idx[10], "EURUSD", "t", "scalp", 1, 1.1000, 1.0950, 1.1100, 2.0, "market", 0, "", "Asia", "trend", "15m", "trending")
    r0 = run_backtest(df, [s], "EURUSD", spread_pips=0)
    r1 = run_backtest(df, [s], "EURUSD", spread_pips=1, slippage_pips=0.5, commission_pips=0.5)
    assert r0.stats["same_candle_conflicts"] == 1 and r0.trades["outcome"].iloc[0] == "SL" and r0.trades["r"].iloc[0] == -1.0
    assert r1.trades["r"].iloc[0] < r0.trades["r"].iloc[0], "costs must make the result worse"


def test_split_backtest_separates_periods_and_breakdown_groups():
    df = frames()["swing"]
    sigs = STRATEGIES["donchian"]("swing").generate(df, "EURUSD")
    dev, ev, cut = split_backtest(df, sigs, "EURUSD", 0.7)
    if dev.stats["trades"]:
        assert dev.trades["exit_time"].max() <= df.index[-1] and (dev.trades["signal_time"] < cut).all()
    if ev.stats["trades"]:
        assert (ev.trades["signal_time"] >= cut).all()
        assert set(breakdown(ev.trades, "regime").index) <= {"trending", "ranging", "volatile", "transition", "unknown"}


# ---------------------------------------------------------------- workflow
def _wf_frames(days=120):
    raw = synthetic("5m", days=days, step_min=5)
    return raw, {"5m": raw, "15m": resample(raw, "15m"), "1h": resample(raw, "1h")}


def test_workflow_missing_data_is_no_trade():
    r = evaluate("EURUSD", {"1h": frames()["scalp"].iloc[:50]})
    assert r["state"] == "NO TRADE" and r["signal"] is None


def test_workflow_answer_is_always_one_of_four_and_shape_is_standard():
    raw, fr = _wf_frames()
    states = set()
    for cut in range(len(raw) - 2400, len(raw), 240):
        r5 = raw.iloc[:cut]
        r = evaluate("EURUSD", {"5m": r5, "15m": resample(r5, "15m"), "1h": resample(r5, "1h")}, now=r5.index[-1])
        states.add(r["state"])
        assert r["state"] in ("BUY", "SELL", "WAIT", "NO TRADE")
        if r["state"] in ("BUY", "SELL"):
            s = r["signal"]
            for k in ("instrument", "direction", "strategy", "timeframe", "entry", "entry_zone", "stop_loss", "take_profit",
                      "risk_reward", "confidence", "regime", "timestamp", "invalidation", "explanation", "rejection_reasons"):
                assert k in s, k
            long = s["direction"] == "BUY"
            assert (s["entry"] - s["stop_loss"]) * (1 if long else -1) > 0
            assert all((t - s["entry"]) * (1 if long else -1) > 0 for t in s["take_profit"])
            assert s["risk_reward"] >= 1.0 and 0 <= s["confidence"] <= 100 and s["explanation"]
        else:
            assert r["signal"] is None
    assert states


def test_workflow_bias_blocks_trades_against_it():
    raw, fr = _wf_frames()
    pl = plan(fr["1h"], "EURUSD")
    assert pl["bias"] in ("BUY", "SELL", "NEUTRAL", "NO TRADE")
    for bias in ("BUY", "SELL"):
        found, rejected = setups(fr["15m"], "EURUSD", bias, config.load("none.json"))
        assert all(s.side == (1 if bias == "BUY" else -1) for s in found), "a setup against the 1h bias must never pass"
    found, _ = setups(fr["15m"], "EURUSD", "NO TRADE", config.load("none.json"))
    assert found == []
    found, _ = setups(fr["15m"], "EURUSD", "NEUTRAL", config.load("none.json"))
    assert all(s.family in ("mean_reversion", "price_action", "ict") for s in found)


def test_workflow_news_blackout_forces_no_trade():
    raw, fr = _wf_frames()
    now = raw.index[-1]
    ev = [dict(ccy="EUR", title="CPI", impact="high", ts=int(now.timestamp()) + 20 * 60, forecast="", previous="")]
    r = evaluate("EURUSD", fr, events=ev, now=now)
    assert r["state"] == "NO TRADE" and "news" in r["stages"]


# ---------------------------------------------------------------- dashboard output
def test_dashboard_json_has_strategies_history_workflow_and_no_duplicate_markers(tmp_path):
    import json
    from fxbot.dashboard import build, demo_frames
    from fxbot.news import sample_news
    build(["EURUSD"], str(tmp_path), frames=demo_frames(["EURUSD"]), news=sample_news(), demo=True)
    idx = json.load(open(tmp_path / "data" / "index.json"))
    assert len(idx["strategies"]) >= 13 and "families" in idx and "workflow" in idx
    d = json.load(open(tmp_path / "data" / "EURUSD.json"))
    assert d["workflow"]["state"] in ("BUY", "SELL", "WAIT", "NO TRADE")
    for tfk in ("15m", "4h", "1d"):
        an = d["tfs"][tfk]["an"]
        assert an["strategies"] and {"key", "label", "verdict", "reason"} <= set(an["strategies"][0])
        keys = [(h["key"], h["side"], h["time"]) for h in an["history"]]
        assert len(keys) == len(set(keys)), "the chart must never get two markers for the same strategy, side and candle"
        gap = {}
        for h in an["history"]:
            gap.setdefault((h["key"], h["side"]), []).append(h["t"])
        assert all(b > a for ts in gap.values() for a, b in zip(ts, ts[1:]))
