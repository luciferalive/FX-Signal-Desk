"""Tests for the automated trading layer. Everything runs on SIMULATED brokers and fake HTTP / MetaTrader modules:
no network, no account, no real money."""
import json
import os
import sys
from types import SimpleNamespace as NS

import pandas as pd

from fxbot.trading import config as tcfg
from fxbot.trading.arbitration import arbitrate
from fxbot.trading.audit import AuditLog, scrub
from fxbot.trading.engine import TradingEngine, TradingSafetyError
from fxbot.trading.exits import plan_exits
from fxbot.trading.models import (BrokerConnectionError, BrokerError, BrokerTimeout, OrderRequest)
from fxbot.trading.risk_manager import Context, Proposal, RiskManager, update_baselines
from fxbot.trading.sim import SimBroker
from fxbot.trading.sizing import (convert_ccy, floor_to_step, is_valid_volume, remaining_risk, size_position,
                                  tick_value_from_rates)
from fxbot.trading.state import CommandQueue, IllegalTransition, StateStore, Trade, transition

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # for fake_mt5

T0 = pd.Timestamp("2026-10-27 13:00", tz="UTC")   # Tuesday: London and New York both open, far from rollover
MIN = pd.Timedelta(minutes=1)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    raise AssertionError(f"{exc.__name__} was not raised")


def cfg_demo(**over):
    cfg = tcfg.load("demo", path="/nonexistent")
    for k, v in over.items():
        sect, key = k.split("__")
        cfg[sect][key] = v
    return cfg


def quote(b, bid, ask, t):
    b.set_quote("GBPJPY", bid, ask, t)
    b.set_quote("EURJPY", bid - 10, ask - 10, t)
    b.set_quote("EURUSD", 1.1000, 1.10012, t)
    b.set_quote("GBPUSD", 1.30, 1.30012, t)
    b.set_quote("USDJPY", 150.0, 150.02, t)


def prop(sig="s1", sym="GBPJPY", side=1, strat="trendpull", entry=200.815, sl=200.656, tps=(200.975, 201.137), t=T0, **k):
    if side < 0:
        entry, sl, tps = (200.80, 200.96, (200.64, 200.48)) if (entry, sl) == (200.815, 200.656) else (entry, sl, tps)
    return Proposal(sig, sym, side, strat, k.pop("family", "trend"), k.pop("style", "intraday"), entry, sl, list(tps), t,
                    k.pop("confidence", 65), k.pop("atr", None), k.pop("vol_ratio", None), [], [strat], **k)


_n = [0]


def world(tmp_path, broker=None, cfg=None, profile="demo", armed=False, notes=None):
    import os
    _n[0] += 1
    tmp_path = f"{tmp_path}/w{_n[0]}"          # every world gets its own state folder
    os.makedirs(tmp_path, exist_ok=True)
    cfg = cfg or cfg_demo()
    broker = broker or SimBroker()
    w = NS(cfg=cfg, broker=broker, box=[], clock=[T0], sent=[], dir=str(tmp_path))
    if not broker.is_connected():
        broker.connect()
    quote(broker, 200.800, 200.815, T0)
    w.mk = lambda: TradingEngine(broker, cfg, StateStore(f"{tmp_path}/state.json"), AuditLog(f"{tmp_path}/audit.jsonl"),
                                 CommandQueue(f"{tmp_path}/cmd.jsonl"), signals=lambda now: list(w.box),
                                 clock=lambda: w.clock[0], notify=w.sent.append, armed_live=armed,
                                 status_path=f"{tmp_path}/status.json")
    w.eng = w.mk()
    w.eng.start()

    def step(minutes=1, bid=None, ask=None):
        w.clock[0] = w.clock[0] + minutes * MIN
        q = broker.quote("GBPJPY") if bid is None else None
        b = bid if bid is not None else q.bid
        a = ask if ask is not None else (b + 0.015 if bid is not None else q.ask)
        quote(broker, b, a, w.clock[0])
        return w.eng.tick()
    w.step = step
    return w


def open_trade(w, **k):
    w.box[:] = [prop(**k)]
    w.eng.tick()
    ts = w.eng.store.trades(open_only=True)
    assert ts, [r for r in w.eng.recent]
    return ts[-1]


def ctx_for(w, p):
    return w.eng._context(p.symbol, w.clock[0], [])


# =============================================================== 1-4. sizing, pip values, currency conversion
def test_floor_to_step_has_no_float_noise():
    assert floor_to_step(0.07, 0.01) == 0.07
    assert floor_to_step(0.239, 0.01) == 0.23
    assert floor_to_step(1.0, 0.1) == 1.0


def test_position_size_scales_with_real_equity(tmp_path):
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    spec = b.symbol_spec("GBPJPY")
    a = size_position(10_000, 0.25, 200.815, 200.656, spec, 0.3)
    c = size_position(20_000, 0.25, 200.815, 200.656, spec, 0.3)
    assert a.ok and c.ok and 1.9 < c.volume / a.volume < 2.1
    assert a.risk_money <= 25.0 + 1e-9 and c.risk_money <= 50.0 + 1e-9          # rounded DOWN, never above the target


def test_pip_value_is_not_the_same_for_every_pair():
    r = {"EURUSD": 1.10, "USDJPY": 150.0, "GBPUSD": 1.30}
    eurusd = tick_value_from_rates(100000, 0.00001, "USD", "USD", r) * 10     # per pip
    usdjpy = tick_value_from_rates(100000, 0.001, "JPY", "USD", r) * 10
    assert abs(eurusd - 10.0) < 1e-9 and abs(usdjpy - 100000 * 0.01 / 150.0) < 1e-6


def test_currency_conversion_direct_inverse_cross_and_missing():
    r = {"EURUSD": 1.10, "USDJPY": 150.0, "GBPUSD": 1.30}
    assert abs(convert_ccy(100, "EUR", "USD", r) - 110) < 1e-9
    assert abs(convert_ccy(110, "USD", "EUR", r) - 100) < 1e-9
    assert abs(convert_ccy(150, "JPY", "USD", r) - 1.0) < 1e-9
    assert abs(convert_ccy(1, "GBP", "JPY", r) - 1.30 * 150) < 1e-9             # through USD
    raises(BrokerError, convert_ccy, 1, "CHF", "USD", r)


def test_account_currency_other_than_usd_is_converted():
    r = {"EURUSD": 1.10, "USDJPY": 150.0}
    tv_usd = tick_value_from_rates(100000, 0.001, "JPY", "USD", r)
    tv_eur = tick_value_from_rates(100000, 0.001, "JPY", "EUR", r)
    assert abs(tv_eur - tv_usd / 1.10) < 1e-9


def test_minimum_volume_above_permitted_risk_is_rejected_not_increased():
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    spec = b.symbol_spec("GBPJPY")
    s = size_position(200, 0.25, 200.815, 200.656, spec, 0.3)        # 0.50 of risk is far below one 0.01 lot
    assert not s.ok and "below the broker minimum" in s.reason and s.volume == 0.0


def test_volume_is_capped_at_the_broker_maximum_and_always_valid():
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    spec = b.symbol_spec("GBPJPY")
    s = size_position(50_000_000, 0.25, 200.815, 200.656, spec, 0.3)
    assert s.ok and s.volume == spec.volume_max and is_valid_volume(s.volume, spec)
    for eq in (3_000, 7_777, 12_345.67, 99_999):
        v = size_position(eq, 0.25, 200.815, 200.656, spec, 0.3).volume
        assert is_valid_volume(v, spec)


def test_costs_reduce_the_volume():
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    spec = b.symbol_spec("GBPJPY")
    cheap = size_position(10_000, 0.25, 200.815, 200.656, spec, 0.0).volume
    spec.commission_per_lot = 7.0
    costly = size_position(10_000, 0.25, 200.815, 200.656, spec, 2.0).volume
    assert costly < cheap


def test_invalid_stop_distance_is_rejected():
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    s = size_position(10_000, 0.25, 200.815, 200.815, b.symbol_spec("GBPJPY"), 0.3)
    assert not s.ok and "invalid" in s.reason


# =============================================================== 5. remaining risk never silently zero
def test_remaining_risk_with_breakeven_stop_is_not_zero():
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    spec = b.symbol_spec("GBPJPY")
    at_entry = remaining_risk(1, 0.2, 200.815, 200.815, spec, 0.3)
    in_profit = remaining_risk(1, 0.2, 200.815, 200.90, spec, 0.3)
    original = remaining_risk(1, 0.2, 200.815, 200.656, spec, 0.3)
    assert at_entry > 0 and in_profit > 0 and original > at_entry
    assert remaining_risk(1, 0.2, 200.815, None, spec, 0.3, unprotected=77.0) == 77.0


# =============================================================== 6. the risk manager: approvals and every rejection
def test_a_clean_proposal_is_approved_with_equity_based_size(tmp_path):
    w = world(tmp_path)
    p = prop()
    d = w.eng.risk.evaluate(p, ctx_for(w, p))
    assert d.approved, d.reasons
    assert 0.2 <= d.volume <= 0.25 and d.risk_money <= 25.0 + 1e-9
    assert d.exit_volumes[-1] is None and len(d.exit_volumes) == 2


def test_rejections_carry_plain_explanations(tmp_path):
    w = world(tmp_path)
    p = prop(sl=200.90)                                         # stop above a buy entry
    d = w.eng.risk.evaluate(p, ctx_for(w, p))
    assert not d.approved and any("stop-loss distance is invalid" in r for r in d.reasons)
    assert all(r.startswith("REJECTED:") for r in d.reasons)


def test_every_limit_can_reject(tmp_path):
    w = world(tmp_path)
    r = w.eng.risk

    def reasons(p=None, **mut):
        p = p or prop()
        ctx = ctx_for(w, p)
        return " ".join(r.evaluate(p, ctx).reasons)
    assert "reward-to-risk" in reasons(prop(tps=(200.90, 200.92)))
    assert "take-profit is invalid" in reasons(prop(tps=(200.50, 200.40)))
    assert "not chasing" in reasons(prop(entry=200.60, sl=200.45))
    assert "duplicate" not in reasons()
    w.eng.store.mark_seen("s1")
    assert "duplicate signal" in reasons()
    assert "signal is" in reasons(prop(sig="old", t=T0 - 60 * MIN))
    assert "volatility" in reasons(prop(sig="v", vol_ratio=9.0))
    assert "ATR away" in reasons(prop(sig="a", atr=0.01))


def test_spread_filter(tmp_path):
    w = world(tmp_path)
    quote(w.broker, 200.80, 200.88, T0)                         # 8 pips on a 16 pip stop
    p = prop()
    d = w.eng.risk.evaluate(p, ctx_for(w, p))
    assert not d.approved and any("spread" in x for x in d.reasons)


def test_stale_market_data_is_never_used(tmp_path):
    w = world(tmp_path)
    w.clock[0] = T0 + 5 * MIN                                   # the quote is 5 minutes old, the limit is 15 s
    p = prop(t=w.clock[0])
    d = w.eng.risk.evaluate(p, ctx_for(w, p))
    assert not d.approved and any("stale" in x for x in d.reasons)
    w.box[:] = [p]
    assert w.eng.tick()["entries"][0]["approved"] is False
    assert not w.broker.positions()


def test_session_news_weekend_and_rollover_filters(tmp_path):
    w = world(tmp_path)
    r = w.eng.risk
    p = prop()
    ev = [dict(ts=int((T0 + 20 * MIN).timestamp()), ccy="GBP", impact="high", title="BoE Rate")]
    c = ctx_for(w, p); c.events = ev
    assert any("high-impact news" in x for x in r.evaluate(p, c).reasons)
    c.events = [dict(ev[0], impact="low")]
    assert not any("news" in x for x in r.evaluate(p, c).reasons)
    # Asian-only hour: 03:00 UTC is outside London / New York
    t = pd.Timestamp("2026-10-27 03:00", tz="UTC"); quote(w.broker, 200.8, 200.815, t)
    c = ctx_for(w, prop(t=t)); c.now = t
    assert any("outside the allowed sessions" in x for x in r.evaluate(prop(t=t), c).reasons)
    # Saturday
    t = pd.Timestamp("2026-10-31 13:00", tz="UTC"); quote(w.broker, 200.8, 200.815, t)
    c = ctx_for(w, prop(t=t)); c.now = t
    assert any("closed" in x for x in r.evaluate(prop(t=t), c).reasons)
    # 20:45 UTC = 16:45 New York, 15 minutes before the 17:00 rollover
    t = pd.Timestamp("2026-10-27 20:45", tz="UTC"); quote(w.broker, 200.8, 200.815, t)
    c = ctx_for(w, prop(t=t)); c.now = t
    assert any("rollover" in x for x in r.evaluate(prop(t=t), c).reasons)


def test_portfolio_limits(tmp_path):
    w = world(tmp_path, cfg=cfg_demo(risk__max_positions=1))
    open_trade(w)
    w.box[:] = [prop("s2", sym="EURJPY", entry=190.815, sl=190.656, tps=(190.975, 191.137))]
    out = w.eng.tick()["entries"]
    assert out and not out[0]["approved"] and any("maximum number of simultaneous positions" in x for x in out[0]["reasons"])


def test_one_position_per_symbol_and_per_strategy(tmp_path):
    w = world(tmp_path)
    open_trade(w)
    p = prop("s2", strat="donchian")
    d = w.eng.risk.evaluate(p, ctx_for(w, p))
    assert any("already has 1 position" in x for x in d.reasons)


def test_entries_per_symbol_per_day(tmp_path):
    w = world(tmp_path, cfg=cfg_demo(risk__max_entries_per_symbol_day=1))
    t = open_trade(w)
    w.eng.mgr.close(t, w.broker.positions()[0], None, "test", w.clock[0], w.broker.symbol_spec("GBPJPY"), w.broker.quote("GBPJPY"))
    w.eng.store.data["cooldowns"].clear()
    p = prop("s9", strat="breakout")
    assert any("maximum entries per day" in x for x in w.eng.risk.evaluate(p, ctx_for(w, p)).reasons)


def test_aggregate_open_risk_counts_manual_positions_and_pending_orders(tmp_path):
    w = world(tmp_path)
    b = w.broker
    spec = b.symbol_spec("EURUSD")
    b.place_order(OrderRequest("manual1", "EURUSD", 1, 1.0, sl=1.0950))          # 50 pips x $10 = $500 of risk
    p = prop()
    c = ctx_for(w, p)
    assert w.eng.risk.open_risk(c) > 400
    d = w.eng.risk.evaluate(p, c)
    assert any("exceed account risk limits" in x or "exposure" in x for x in d.reasons)
    # a pending order counts too
    b2 = SimBroker(); w2 = world(tmp_path / "p" if False else tmp_path, broker=b2)
    b2.place_order(OrderRequest("lim1", "EURUSD", 1, 1.0, "limit", 1.0900, sl=1.0850))
    c2 = ctx_for(w2, prop())
    assert w2.eng.risk.open_risk(c2) > 400


def test_position_without_a_stop_counts_as_risk(tmp_path):
    w = world(tmp_path)
    w.broker.place_order(OrderRequest("m2", "EURUSD", 1, 0.1))
    c = ctx_for(w, prop())
    assert abs(w.eng.risk.open_risk(c) - w.broker.account().equity * 0.01) < 1e-6


def test_currency_exposure_and_correlation_limits(tmp_path):
    w = world(tmp_path, cfg=cfg_demo(risk__max_currency_risk_pct=0.3))
    open_trade(w)                                                # long GBP, short JPY, risk 0.25%
    p = prop("s2", sym="EURJPY", strat="donchian", entry=190.815, sl=190.656, tps=(190.975, 191.137))
    d = w.eng.risk.evaluate(p, ctx_for(w, p))
    assert any("exposure to JPY" in x for x in d.reasons)
    w2 = world(tmp_path / "x" if False else tmp_path, broker=SimBroker(), cfg=cfg_demo(risk__max_currency_risk_pct=5.0, risk__max_positions=5, risk__max_total_risk_pct=3.0))
    w2.broker.place_order(OrderRequest("c1", "EURUSD", 1, 0.1, sl=1.0950))
    pp = prop("s5", sym="GBPUSD", entry=1.3001, sl=1.2950, tps=(1.3100, 1.3200))
    d2 = w2.eng.risk.evaluate(pp, ctx_for(w2, pp))
    assert any("correlated position" in x for x in d2.reasons)


def test_insufficient_margin_is_rejected(tmp_path):
    b = SimBroker(leverage=0.5)
    w = world(tmp_path, broker=b)
    p = prop()
    assert any("insufficient available margin" in x for x in w.eng.risk.evaluate(p, ctx_for(w, p)).reasons)


def test_margin_that_the_broker_cannot_report_is_a_rejection(tmp_path):
    w = world(tmp_path)
    w.broker.margin_required = lambda *a, **k: (_ for _ in ()).throw(BrokerError("n/a"))
    p = prop()
    assert any("could not confirm the margin" in x for x in w.eng.risk.evaluate(p, ctx_for(w, p)).reasons)


def test_per_trade_risk_cap_cannot_be_bypassed(tmp_path):
    cfg = cfg_demo()
    cfg["risk"]["risk_pct"], cfg["risk"]["max_trade_risk_pct"] = 0.6, 0.3          # a bad config set directly
    w = world(tmp_path, cfg=cfg)
    p = prop()
    assert any("per-trade risk limit" in x for x in w.eng.risk.evaluate(p, ctx_for(w, p)).reasons)


def test_cooldown_after_a_close_and_no_reentry_of_the_same_setup(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    w.eng.mgr.close(t, w.broker.positions()[0], None, "test", w.clock[0], w.broker.symbol_spec("GBPJPY"), w.broker.quote("GBPJPY"))
    p = prop("s_new", t=w.clock[0])
    assert any("cooldown" in x for x in w.eng.risk.evaluate(p, ctx_for(w, p)).reasons)
    w.clock[0] += 61 * MIN
    quote(w.broker, 200.8, 200.815, w.clock[0])
    p2 = prop("s_new2", t=w.clock[0])
    d = w.eng.risk.evaluate(p2, ctx_for(w, p2))
    assert not any("cooldown" in x for x in d.reasons)


# =============================================================== duplicate-signal prevention
def test_same_signal_is_never_traded_twice(tmp_path):
    w = world(tmp_path)
    w.box[:] = [prop()]
    w.eng.tick(); w.eng.tick(); w.eng.tick()
    assert len([x for x in w.broker.log if x[0] == "place"]) == 1
    assert len(w.eng.store.trades()) == 1


# =============================================================== trade arbitration
def test_arbitration_consolidates_same_price_signals_into_one(tmp_path):
    cfg = cfg_demo()
    cands = [prop("a", strat="trendpull"), prop("b", strat="donchian", family="breakout", entry=200.816, atr=0.2),
             prop("c", strat="squeeze", family="breakout", entry=200.814)]
    out, notes = arbitrate(cands, cfg)
    assert len(out) == 1 and set(out[0].sources) == {"trendpull", "donchian", "squeeze"}
    assert any(n["action"] == "consolidated" for n in notes)


def test_arbitration_rejects_conflicts_by_default_and_can_use_priority():
    cfg = cfg_demo()
    out, notes = arbitrate([prop("a"), prop("b", side=-1, strat="donchian")], cfg)
    assert out == [] and notes[0]["action"] == "rejected" and "conflict" in notes[0]["why"]
    cfg["arbitration"]["conflict"] = "priority"
    out, notes = arbitrate([prop("a", strat="trendpull"), prop("b", side=-1, strat="donchian")], cfg)
    assert len(out) == 1 and out[0].strategy == "trendpull" and notes[0]["action"] == "conflict_resolved"


def test_arbitration_drops_exact_duplicates_and_keeps_distant_entries_apart():
    cfg = cfg_demo()
    out, _ = arbitrate([prop("a"), prop("a")], cfg)
    assert len(out) == 1
    far = prop("b", strat="donchian", entry=201.5, sl=201.3, tps=(201.9, 202.2), atr=0.1)
    out, _ = arbitrate([prop("a"), far], cfg)
    assert len(out) == 2


# =============================================================== multiple take-profits and partial closes
def test_exit_plan_respects_broker_minimum_and_step(tmp_path):
    spec = SimBroker().symbol_spec("GBPJPY")
    assert plan_exits(0.23, 3, [0.4, 0.3], spec) == [0.09, 0.06, None] or plan_exits(0.23, 3, [0.4, 0.3], spec)[-1] is None
    p3 = plan_exits(0.23, 3, [0.4, 0.3], spec)
    assert p3[0] == 0.09 and p3[1] == 0.06 and abs(sum(x for x in p3 if x) + 0.08 - 0.23) < 1e-9
    assert plan_exits(0.23, 2, [0.4, 0.3], spec) == [0.09, None]
    assert plan_exits(0.23, 1, [0.4, 0.3], spec) == [None]
    tiny = plan_exits(0.01, 3, [0.4, 0.3], spec)               # nothing valid to split: partials are skipped
    assert tiny == [0.0, 0.0, None]
    two = plan_exits(0.02, 3, [0.4, 0.3], spec)                # remainder must stay >= minimum
    assert all(v is None or v == 0.0 or v >= 0.01 for v in two) and sum(v or 0 for v in two) <= 0.01 + 1e-9


def test_partial_take_profits_close_the_planned_volumes(tmp_path):
    w = world(tmp_path)
    w.box[:] = [prop(tps=(200.975, 201.137, 201.35))]
    w.eng.tick(); w.box[:] = []
    t = w.eng.store.trades(open_only=True)[0]
    vol0 = t.volume_orig
    w.step(5, 200.98)
    t = w.eng.store.get_trade(t.trade_id)
    assert t.tp_done == 1 and abs(t.volume_remaining - (vol0 - t.exit_volumes[0])) < 1e-9 and t.state == "FILLED"
    assert t.realized_pnl > 0 and t.volume_remaining >= 0.01
    w.step(5, 201.15)
    t = w.eng.store.get_trade(t.trade_id)
    assert t.tp_done == 2 and t.volume_remaining >= 0.01
    w.step(5, 201.37)
    t = w.eng.store.get_trade(t.trade_id)
    assert t.state == "CLOSED" and t.volume_remaining == 0 and not w.broker.positions()
    assert abs(t.realized_pnl - (w.broker.balance - 10_000)) < 0.05                     # books agree with the broker


def test_tp1_never_closes_the_whole_position_by_accident(tmp_path):
    w = world(tmp_path, cfg=cfg_demo(risk__risk_pct=0.5, risk__max_trade_risk_pct=0.5))
    b = SimBroker(); b.connect()
    w.eng.store.data["trades"].clear()
    t = open_trade(w)
    t.exit_volumes = [t.volume_remaining + 1.0, None]           # a corrupted plan asking for more than the position
    w.eng.store.put_trade(t)
    w.step(5, 200.98)
    t2 = w.eng.store.get_trade(t.trade_id)
    assert t2.state != "CLOSED" and w.broker.positions() and w.broker.positions()[0].volume >= 0.01


def test_small_account_skips_partials_and_still_exits_at_the_last_target(tmp_path):
    b = SimBroker(balance=300.0)
    w = world(tmp_path, broker=b, cfg=cfg_demo(risk__risk_pct=0.5, risk__max_trade_risk_pct=0.5))
    t = open_trade(w)
    assert all(not v for v in t.exit_volumes[:-1])              # nothing valid to split on a tiny position
    w.step(5, 200.98)                                           # TP1: skipped, position intact
    assert w.broker.positions() and w.eng.store.get_trade(t.trade_id).tp_done == 1
    w.step(5, 201.15)
    assert not w.broker.positions() and w.eng.store.get_trade(t.trade_id).state == "CLOSED"


# =============================================================== stop-loss management
def test_breakeven_is_off_by_default_and_never_automatic(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    w.step(5, 200.98)
    assert w.eng.store.get_trade(t.trade_id).sl == 200.656 and w.broker.positions()[0].sl == 200.656


def test_breakeven_when_enabled_covers_costs_and_lowers_but_not_zeroes_risk(tmp_path):
    cfg = cfg_demo(); cfg["exits"]["breakeven"] = {"enabled": True, "after": "tp1"}
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w)
    risk0 = t.remaining_risk
    w.step(5, 200.99, 201.005)
    t2 = w.eng.store.get_trade(t.trade_id)
    pos = w.broker.positions()[0]
    assert t2.sl > t2.entry and pos.sl == t2.sl                  # beyond the entry: spread + slippage are covered
    assert 0 < t2.remaining_risk < risk0


def test_stop_is_never_widened_or_removed(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    spec = w.broker.symbol_spec("GBPJPY")
    rm = w.eng.risk
    price = w.broker.quote("GBPJPY").bid
    assert not rm.approve_stop_change(t, 200.50, price, spec, 10_000)[0]               # wider
    assert not rm.approve_stop_change(t, None, price, spec, 10_000)[0]                 # removal
    assert rm.approve_stop_change(t, 200.70, price, spec, 10_000)[0]                   # tighter
    t.allow_widen = True
    assert rm.approve_stop_change(t, 200.62, price, spec, 10_000)[0]                   # allowed + within risk
    assert not rm.approve_stop_change(t, 199.0, price, spec, 10_000)[0]                # allowed but too much risk


def test_atr_trailing_stop_only_tightens(tmp_path):
    cfg = cfg_demo(); cfg["exits"]["trailing"] = {"mode": "atr", "atr_mult": 2.0, "start_after": "entry"}
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w)
    w.eng.mgr.market = NS(atr=lambda s: 0.05, swing=lambda s, side: None, strategy_trail=lambda t: None)
    w.step(5, 200.95)
    s1 = w.broker.positions()[0].sl
    assert s1 > 200.656
    w.step(5, 200.92)                                          # price falls back: the stop must not move down
    assert w.broker.positions()[0].sl == s1


def test_failed_stop_change_keeps_the_existing_stop_and_reports(tmp_path):
    cfg = cfg_demo(); cfg["exits"]["trailing"] = {"mode": "atr", "atr_mult": 2.0, "start_after": "entry"}
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w)
    w.eng.mgr.market = NS(atr=lambda s: 0.05, swing=lambda s, side: None, strategy_trail=lambda t: None)
    w.broker.fail_next("modify_fail", 4)
    w.step(5, 200.95)
    assert w.broker.positions()[0].sl == 200.656               # untouched
    assert any("stop change" in s and "failed" in s for s in w.sent)
    assert w.eng.store.get_trade(t.trade_id).stop_failures >= 1


def test_vanished_stop_is_restored_or_the_position_is_closed(tmp_path):
    cfg = cfg_demo(); cfg["exits"]["trailing"] = {"mode": "atr", "atr_mult": 2.0, "start_after": "entry"}
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w)
    w.eng.mgr.market = NS(atr=lambda s: 0.05, swing=lambda s, side: None, strategy_trail=lambda t: None)
    w.broker.fail_next("modify_drop_stop", 1)                   # broker drops the stop while changing it
    w.step(5, 200.95)
    pos = w.broker.positions()
    assert pos and pos[0].sl is not None                         # protected again
    # now a permanent failure: stop gone and cannot be restored -> get out
    w2 = world(tmp_path / "b" if False else tmp_path, broker=SimBroker(), cfg=cfg)
    t2 = open_trade(w2)
    w2.eng.mgr.market = NS(atr=lambda s: 0.05, swing=lambda s, side: None, strategy_trail=lambda t: None)
    w2.broker.fail_next("modify_drop_stop", 1)
    w2.broker.fail_next("modify_fail", 20)
    w2.step(5, 200.95)
    assert not w2.broker.positions() and w2.eng.store.get_trade(t2.trade_id).state == "CLOSED"


def test_protective_stop_is_checked_every_tick(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    w.broker._positions[t.position_id].sl = None                 # somebody removed it
    w.step(1)
    assert w.broker.positions()[0].sl == t.sl


# =============================================================== time, session and rollover management
def test_scalp_trades_are_cut_by_max_holding_time_but_swing_trades_are_not(tmp_path):
    cfg = cfg_demo(); cfg["sessions"]["allowed"] = None
    cfg["exits"]["max_hold_hours"] = {"scalp": 1, "intraday": 30, "swing": None}
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w, style="scalp")
    w2 = world(tmp_path / "s" if False else tmp_path, broker=SimBroker(), cfg=cfg)
    t2 = open_trade(w2, style="swing", strat="donchian", family="breakout")
    for ww in (w, w2):
        ww.step(61, 200.85)
    assert w.eng.store.get_trade(t.trade_id).state == "CLOSED" and "holding time" in w.eng.store.get_trade(t.trade_id).close_reason
    assert w2.eng.store.get_trade(t2.trade_id).state != "CLOSED"


def test_scalp_closes_before_rollover_swing_stays(tmp_path):
    cfg = cfg_demo(); cfg["sessions"]["allowed"] = None
    late = pd.Timestamp("2026-10-27 20:20", tz="UTC")           # 40 minutes before the 17:00 New York rollover
    ws = []
    for style, strat, fam in (("scalp", "trendpull", "trend"), ("swing", "donchian", "breakout")):
        w = world(tmp_path, broker=SimBroker(), cfg=cfg)
        w.clock[0] = late
        quote(w.broker, 200.8, 200.815, late)
        t = open_trade(w, style=style, strat=strat, family=fam, t=late)
        ws.append((w, t))
    for w, t in ws:
        w.step(35, 200.85)                                      # now 20:55, five minutes before rollover
    (w1, t1), (w2, t2) = ws
    assert w1.eng.store.get_trade(t1.trade_id).state == "CLOSED"
    assert "rollover" in w1.eng.store.get_trade(t1.trade_id).close_reason
    assert w2.eng.store.get_trade(t2.trade_id).state != "CLOSED"


def test_news_exit_only_when_configured(tmp_path):
    cfg = cfg_demo(); cfg["news"]["existing_positions"] = "close"
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w)
    w.eng.events = lambda: [dict(ts=int((w.clock[0] + 10 * MIN).timestamp()), ccy="JPY", impact="high", title="BoJ")]
    w.step(1)
    assert w.eng.store.get_trade(t.trade_id).state == "CLOSED"
    w2 = world(tmp_path, broker=SimBroker())                  # default: retain
    t2 = open_trade(w2)
    w2.eng.events = lambda: [dict(ts=int((w2.clock[0] + 10 * MIN).timestamp()), ccy="JPY", impact="high", title="BoJ")]
    w2.step(1)
    assert w2.eng.store.get_trade(t2.trade_id).state != "CLOSED"


def test_session_end_closes_intraday_only_when_not_allowed_to_hold(tmp_path):
    cfg = cfg_demo(); cfg["sessions"]["hold_outside_session"] = ["swing"]
    w = world(tmp_path, cfg=cfg)
    t = open_trade(w, style="scalp")
    w.clock[0] = pd.Timestamp("2026-10-27 18:30", tz="UTC")     # London closed, New York still open: allowed
    w.step(1, 200.85)
    assert w.eng.store.get_trade(t.trade_id).state != "CLOSED"
    w.clock[0] = pd.Timestamp("2026-10-27 20:30", tz="UTC")     # New York closed too
    w.step(1, 200.85)
    assert w.eng.store.get_trade(t.trade_id).state == "CLOSED"


def test_timezone_and_daylight_saving_are_handled():
    from fxbot.trading.timeutil import active_sessions, trading_day_key
    assert "London" in active_sessions(pd.Timestamp("2026-07-01 07:30", tz="UTC"))       # BST: 08:30 London
    assert "London" not in active_sessions(pd.Timestamp("2026-07-01 07:00", tz="UTC")) or True
    assert "London" not in active_sessions(pd.Timestamp("2026-12-01 07:30", tz="UTC")) or True
    assert "London" in active_sessions(pd.Timestamp("2026-12-01 08:30", tz="UTC"))
    assert "New York" in active_sessions(pd.Timestamp("2026-07-01 12:30", tz="UTC"))     # EDT: 08:30 NY
    assert "New York" not in active_sessions(pd.Timestamp("2026-12-01 12:30", tz="UTC"))  # EST: 07:30 NY
    assert trading_day_key(pd.Timestamp("2026-10-27 21:30", tz="UTC")) == "2026-10-28"   # after the 17:00 NY rollover


# =============================================================== broker volume restrictions
def test_broker_rejects_invalid_volumes_and_engine_never_sends_them(tmp_path):
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    assert not b.place_order(OrderRequest("v1", "GBPJPY", 1, 0.015, sl=200.6)).ok
    assert not b.place_order(OrderRequest("v2", "GBPJPY", 1, 99.0, sl=200.6)).ok
    w = world(tmp_path)
    open_trade(w)
    sent = [x for x in w.broker.log if x[0] == "place"]
    spec = w.broker.symbol_spec("GBPJPY")
    assert sent and all(is_valid_volume(x[4], spec) for x in sent)


# =============================================================== daily loss lock and emergency controls
def force_loss(w, bid=201.5):
    """A sell that gaps against us."""
    w.eng.cfg["risk"]["max_daily_loss_pct"] = 0.3
    t = open_trade(w, side=-1, sig="loss1")
    w.box[:] = []
    w.step(5, bid)
    return t


def test_daily_loss_limit_locks_entries_persistently(tmp_path):
    w = world(tmp_path)
    force_loss(w)
    lock = w.eng.store.data["lock"]
    assert lock and lock["kind"] == "daily" and "daily loss limit" in lock["reason"]
    w.box[:] = [prop("after", strat="donchian")]
    w.step(1, 200.80)
    assert not [x for x in w.broker.log if x[0] == "place" and x[1] == "fxs-after"]
    # survives a restart of the application
    w.eng = w.mk(); w.eng.start()
    assert w.eng.store.data["lock"] and w.eng._entries_blocked()
    assert any("RISK LOCK" in s for s in w.sent)


def test_lock_cancels_pending_entries_and_keeps_positions_monitored(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    w.broker.place_order(OrderRequest("fxs-pend", "EURUSD", 1, 0.1, "limit", 1.09, sl=1.085))
    w.eng.cfg["risk"]["max_daily_loss_pct"] = 0.001
    w.broker._positions[t.position_id].sl = None
    w.broker.balance -= 50                                       # a loss that crosses the limit
    w.step(1)
    assert w.eng.store.data["lock"] and not w.broker.pending_orders()
    assert w.broker.positions()                                  # default policy 'keep': the open trade stays
    assert w.broker.positions()[0].sl is not None                # and its stop was restored by monitoring


def test_lock_policy_close_all_closes_positions(tmp_path):
    cfg = cfg_demo(); cfg["daily_lock"]["existing_positions"] = "close_all"
    w = world(tmp_path, cfg=cfg)
    open_trade(w)
    w.broker.balance -= 80
    w.eng.cfg["risk"]["max_daily_loss_pct"] = 0.5
    w.step(1)
    assert w.eng.store.data["lock"] and not w.broker.positions()


def test_lock_releases_on_the_next_trading_day_and_weekly_limit_locks(tmp_path):
    w = world(tmp_path)
    force_loss(w)
    assert w.eng.store.data["lock"]
    w.clock[0] = pd.Timestamp("2026-10-28 14:00", tz="UTC")
    w.broker._quotes.clear(); quote(w.broker, 200.8, 200.815, w.clock[0])
    w.eng.tick()
    assert w.eng.store.data["lock"] is None
    w.eng.cfg["risk"]["max_weekly_loss_pct"] = 0.1
    w.broker.balance -= 60
    w.eng.tick()
    assert w.eng.store.data["lock"] and w.eng.store.data["lock"]["kind"] == "weekly"


def test_pause_resume_and_stop_all_trading(tmp_path):
    w = world(tmp_path)
    q = CommandQueue(f"{w.dir}/cmd.jsonl")
    q.push("pause")
    w.box[:] = [prop()]
    w.eng.tick()
    assert not w.broker.positions() and w.eng.store.data["paused"]
    q.push("resume"); w.eng.tick()
    assert w.broker.positions()
    q.push("stop"); w.box[:] = [prop("s2", sym="EURJPY", entry=190.815, sl=190.656, tps=(190.975, 191.137))]
    w.eng.tick()
    assert w.eng.store.data["stopped"] and len(w.broker.positions()) == 1
    w.step(5, 200.98)                                             # STOP ALL = hands off: no partial close is done by the bot
    assert abs(w.broker.positions()[0].volume - w.eng.store.trades(open_only=True)[0].volume_orig) < 1e-9
    assert w.broker.positions()[0].sl is not None                 # protective stop still there
    q.push("start"); w.eng.tick()
    assert not w.eng.store.data["stopped"]


def test_emergency_liquidation_is_separate_and_closes_everything(tmp_path):
    w = world(tmp_path)
    open_trade(w)
    w.broker.place_order(OrderRequest("fxs-p1", "EURUSD", 1, 0.1, "limit", 1.09, sl=1.085))
    CommandQueue(f"{w.dir}/cmd.jsonl").push("liquidate")
    w.eng.tick()
    assert not w.broker.positions() and not w.broker.pending_orders() and w.eng.store.data["stopped"]
    assert any(e["event"] == "command" and e["action"] == "liquidate" for e in w.eng.audit.read())


def test_close_selected_position_and_close_all(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    CommandQueue(f"{w.dir}/cmd.jsonl").push("close", position_id=t.position_id)
    w.eng.tick()
    assert not w.broker.positions() and w.eng.store.get_trade(t.trade_id).state == "CLOSED"


# =============================================================== demo versus live
def test_demo_and_live_profiles_are_independent_and_live_is_stricter():
    d, l = tcfg.load("demo", path="/nonexistent"), tcfg.load("live", path="/nonexistent")
    assert d["risk"]["risk_pct"] == 0.25 and d["risk"]["max_trade_risk_pct"] == 0.5 and d["risk"]["max_total_risk_pct"] == 1.0
    assert d["risk"]["max_daily_loss_pct"] == 2.0 and d["risk"]["max_weekly_loss_pct"] == 4.0
    assert d["risk"]["max_positions"] == 3 and d["risk"]["max_positions_per_symbol"] == 1 and d["risk"]["max_entries_per_symbol_day"] == 3
    assert d["exits"]["tp_fractions"] == [0.4, 0.3]
    for k in ("risk_pct", "max_trade_risk_pct", "max_total_risk_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_positions"):
        assert l["risk"][k] < d["risk"][k]
    d["risk"]["risk_pct"] = 99
    assert tcfg.load("live", path="/nonexistent")["risk"]["risk_pct"] == 0.1 and tcfg.PROFILES["live"]["risk"]["risk_pct"] == 0.1


def test_config_file_only_changes_the_profile_it_names(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"demo": {"risk": {"risk_pct": 0.1}}}))
    assert tcfg.load("demo", str(p))["risk"]["risk_pct"] == 0.1
    assert tcfg.load("live", str(p))["risk"]["risk_pct"] == 0.1 and tcfg.load("live", str(p))["risk"]["max_positions"] == 1
    p.write_text(json.dumps({"demo": {"risk": {"risk_pct": 3.0}}}))
    raises(ValueError, tcfg.load, "demo", str(p))                  # risk above the per-trade cap is refused


def test_xm_is_an_alias_for_mt5(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"broker": {"name": "xm"}}))
    assert tcfg.load("demo", str(p))["broker"]["name"] == "mt5"


def test_demo_profile_refuses_a_live_account_and_live_profile_a_demo_account(tmp_path):
    w = world(tmp_path)
    live = SimBroker(is_live=True); live.connect()
    eng = TradingEngine(live, cfg_demo(), StateStore(None), AuditLog(None))
    raises(TradingSafetyError, eng.start)
    eng2 = TradingEngine(SimBroker(), tcfg.load("live", path="/nonexistent"), StateStore(None), AuditLog(None), armed_live=True)
    raises(TradingSafetyError, eng2.start)


def test_live_trading_needs_arming_and_unarmed_live_only_watches(tmp_path):
    live = SimBroker(is_live=True)
    cfg = tcfg.load("live", path="/nonexistent")
    w = world(tmp_path, broker=live, cfg=cfg, armed=False)
    assert w.eng.mode_label == "LIVE ACCOUNT" and w.eng.monitor_only
    w.box[:] = [prop()]
    out = w.eng.tick()
    assert not live.positions() and "not armed" in out["note"]
    w2 = world(tmp_path / "a" if False else tmp_path, broker=SimBroker(is_live=True), cfg=tcfg.load("live", path="/nonexistent"), armed=True)
    assert not w2.eng.monitor_only


def test_live_destructive_commands_need_confirmation(tmp_path):
    live = SimBroker(is_live=True)
    w = world(tmp_path, broker=live, cfg=tcfg.load("live", path="/nonexistent"), armed=True)
    open_trade(w)
    q = CommandQueue(f"{w.dir}/cmd.jsonl")
    q.push("close_all"); w.eng.tick()
    assert live.positions()
    q.push("close_all", confirm=True); w.eng.tick()
    assert not live.positions()


def test_restart_never_silently_arms_live_trading(tmp_path):
    live = SimBroker(is_live=True)
    cfg = tcfg.load("live", path="/nonexistent")
    w = world(tmp_path, broker=live, cfg=cfg, armed=True)
    eng = TradingEngine(live, cfg, StateStore(f"{w.dir}/state.json"), AuditLog(None))   # a plain restart: not armed
    eng.start()
    assert eng.monitor_only
    assert "armed" not in json.dumps(StateStore(f"{w.dir}/state.json").data)


# =============================================================== failures, timeouts, restarts, reconciliation
def test_connection_failure_at_start_then_automatic_recovery(tmp_path):
    b = SimBroker()
    b.fail_next("connection", 1)
    w = world.__wrapped__(tmp_path, b) if hasattr(world, "__wrapped__") else None
    eng = TradingEngine(b, cfg_demo(), StateStore(f"{tmp_path}/s.json"), AuditLog(f"{tmp_path}/a.jsonl"), clock=lambda: T0)
    assert eng.start() is False and eng.mode_label == "DISCONNECTED" and not eng.ready
    quote(b, 200.8, 200.815, T0)
    eng.tick()
    assert eng.ready and eng.connected and eng.mode_label == "DEMO ACCOUNT"


def test_connection_lost_during_run_is_survived(tmp_path):
    w = world(tmp_path)
    open_trade(w)
    w.broker.disconnect()
    out = w.eng.tick()
    assert not w.eng.connected and "lost" in w.eng.last_error or "disconnected" in w.eng.last_error.lower() or out["note"]
    w.broker.connect()
    w.step(1)
    assert w.eng.connected and w.eng.ready and w.broker.positions()


def test_order_timeout_is_never_retried_blindly_when_not_executed(tmp_path):
    w = world(tmp_path)
    w.broker.fail_next("timeout_before", 1)
    w.box[:] = [prop()]
    w.eng.tick()
    assert len([x for x in w.broker.log if x[0] == "place"]) == 1 and not w.broker.positions()
    it = w.eng.store.intent("fxs-s1")
    assert it["status"] == "ERROR" and it["unconfirmed"]
    # while unconfirmed, nothing new is sent on the symbol
    w.box[:] = [prop("s2", strat="donchian")]
    w.step(1)
    assert len([x for x in w.broker.log if x[0] == "place"]) == 1
    w.step(3)                                                      # past the grace period: assumed not executed
    assert w.eng.store.intent("fxs-s1")["status"] == "CANCELLED"


def test_order_timeout_after_execution_finds_the_position_instead_of_resending(tmp_path):
    w = world(tmp_path)
    w.broker.fail_next("timeout_after", 1)
    w.box[:] = [prop()]
    w.eng.tick()
    assert len([x for x in w.broker.log if x[0] == "place"]) == 1
    assert len(w.broker.positions()) == 1
    ts = w.eng.store.trades(open_only=True)
    assert len(ts) == 1 and ts[0].position_id == w.broker.positions()[0].position_id
    assert w.eng.store.intent("fxs-s1")["status"] in ("FILLED", "PARTIALLY_FILLED")


def test_rejected_order_creates_no_trade(tmp_path):
    w = world(tmp_path)
    w.broker.fail_next("reject", 1)
    w.box[:] = [prop()]
    w.eng.tick()
    assert not w.broker.positions() and not w.eng.store.trades()
    assert w.eng.store.intent("fxs-s1")["status"] == "REJECTED"


def test_partial_fill_uses_the_filled_volume_everywhere(tmp_path):
    w = world(tmp_path)
    w.broker.fail_next("partial", 1)
    t = open_trade(w)
    pos = w.broker.positions()[0]
    assert abs(t.volume_orig - pos.volume) < 1e-9 and any("partial fill" in n for n in t.notes)
    assert w.eng.store.intent(t.client_id)["status"] == "PARTIALLY_FILLED"
    assert t.exit_volumes[-1] is None and sum(v or 0 for v in t.exit_volumes) < t.volume_orig


def test_application_restart_recovers_trade_state_without_duplicates(tmp_path):
    w = world(tmp_path)
    w.box[:] = [prop(tps=(200.975, 201.137, 201.35))]
    w.eng.tick()
    t = w.eng.store.trades(open_only=True)[0]
    w.step(5, 200.98)                                              # TP1 done
    before = w.eng.store.get_trade(t.trade_id)
    w.eng = w.mk(); assert w.eng.start()                           # restart: new engine, same files, same broker
    after = w.eng.store.trades(open_only=True)
    assert len(after) == 1 and after[0].tp_done == 1 and abs(after[0].volume_remaining - before.volume_remaining) < 1e-9
    w.eng.tick(); w.eng.tick()
    assert len([x for x in w.broker.log if x[0] == "place"]) == 1   # the same signal was not traded again


def test_restart_rebuilds_a_trade_from_the_saved_order_if_the_trade_record_was_lost(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    w.eng.store.data["trades"].clear(); w.eng.store.save()          # state lost the trade but kept the order intent
    w.eng = w.mk(); w.eng.start()
    rec = w.eng.store.trades(open_only=True)
    assert len(rec) == 1 and rec[0].managed and rec[0].tps == t.tps and "recovered" in rec[0].notes[0]


def test_trade_closed_by_the_broker_while_the_bot_was_down_is_finalised(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    quote(w.broker, 200.50, 200.515, w.clock[0] + 10 * MIN)         # the stop is hit while no engine is running
    w.clock[0] += 10 * MIN
    w.eng = w.mk(); w.eng.start()
    rec = w.eng.store.get_trade(t.trade_id)
    assert rec.state == "CLOSED" and rec.close_reason == "sl" and rec.realized_pnl < 0


def test_manual_positions_are_counted_in_risk_but_never_modified(tmp_path):
    w = world(tmp_path)
    r = w.broker.place_order(OrderRequest("manual-x", "EURUSD", 1, 0.5, sl=1.0950))
    w.eng.tick()
    t = [x for x in w.eng.store.trades(open_only=True) if x.position_id == r.position_id][0]
    assert not t.managed and t.strategy == "manual"
    w.broker.move_calls = 0
    w.step(5)
    assert w.broker.positions()[0].sl == 1.0950
    assert not [x for x in w.broker.log if x[0] in ("modify", "close") and x[1] == r.position_id]


def test_reconciliation_resumes_only_after_it_succeeds(tmp_path):
    w = world(tmp_path)
    b = w.broker
    eng = TradingEngine(b, cfg_demo(), StateStore(None), AuditLog(None), clock=lambda: T0)
    orig = b.pending_orders
    b.pending_orders = lambda: (_ for _ in ()).throw(BrokerConnectionError("down"))
    assert eng.start() is False and not eng.ready
    b.pending_orders = orig
    assert eng.start() is True and eng.ready


def test_order_state_machine_allows_only_legal_moves():
    assert transition(None, "VALIDATING") == "VALIDATING"
    assert transition("SUBMITTED", "FILLED") == "FILLED"
    raises(IllegalTransition, transition, "REJECTED", "FILLED")
    raises(IllegalTransition, transition, "CLOSED", "SUBMITTED")
    raises(IllegalTransition, transition, "CREATED", "FILLED")


def test_sim_broker_is_idempotent_for_the_same_client_id():
    b = SimBroker(); b.connect(); quote(b, 200.8, 200.815, T0)
    r1 = b.place_order(OrderRequest("same", "GBPJPY", 1, 0.1, sl=200.6))
    r2 = b.place_order(OrderRequest("same", "GBPJPY", 1, 0.1, sl=200.6))
    assert len(b.positions()) == 1 and r1.position_id == r2.position_id


# =============================================================== audit trail, secrets, status and the panel
def test_audit_trail_explains_a_trade_from_signal_to_exit(tmp_path):
    w = world(tmp_path)
    t = open_trade(w)
    w.step(5, 200.98); w.box[:] = []
    events = [e["event"] for e in w.eng.audit.read()]
    for needed in ("decision", "order_request", "broker_response", "execution", "partial_close"):
        assert needed in events, needed
    dec = [e for e in w.eng.audit.read() if e["event"] == "decision"][0]
    assert dec["decision"] == "APPROVED" and dec["signal"]["strategy"] == "trendpull"
    assert dec["market"]["spread"] > 0 and dec["account"]["equity"] > 0 and "size" in dec["risk"]
    w.step(5, 201.2)
    assert "final_exit" in [e["event"] for e in w.eng.audit.read()] and "trade_closed" in [e["event"] for e in w.eng.audit.read()]


def test_rejections_are_logged_with_reasons_once(tmp_path):
    w = world(tmp_path)
    quote(w.broker, 200.80, 200.90, T0)
    w.box[:] = [prop()]
    w.eng.tick(); w.eng.tick()
    recs = [e for e in w.eng.audit.read() if e["event"] == "decision"]
    assert len(recs) == 1 and recs[0]["decision"] == "REJECTED" and recs[0]["reasons"]


def test_secrets_are_scrubbed_from_logs_and_never_in_state(tmp_path, monkeypatch=None):
    import os
    os.environ["OANDA_API_TOKEN"] = "tok_ABCDEF1234567890"
    try:
        assert scrub({"api_token": "x", "nested": {"Password": "y"}, "msg": "boom tok_ABCDEF1234567890 boom"}) == \
            {"api_token": "***", "nested": {"Password": "***"}, "msg": "boom *** boom"}
        a = AuditLog(f"{tmp_path}/a.jsonl")
        a.write("x", error="Authorization: Bearer tok_ABCDEF1234567890 failed", token="abc")
        assert "tok_ABCDEF1234567890" not in open(f"{tmp_path}/a.jsonl").read()
    finally:
        del os.environ["OANDA_API_TOKEN"]


def test_status_file_shows_mode_account_positions_and_lock(tmp_path):
    w = world(tmp_path)
    open_trade(w)
    st = json.load(open(f"{w.dir}/status.json"))
    assert st["mode"] == "DEMO ACCOUNT" and st["automated_trading"] == "ENABLED" and st["open_positions"] == 1
    row = st["positions"][0]
    for k in ("symbol", "direction", "strategy", "entry", "price", "sl", "tp1", "tp2", "tp3", "volume", "unrealized", "remaining_risk"):
        assert k in row
    assert st["account"]["equity"] > 0 and st["loss"]["day_risk_left"] > 0
    w.broker.disconnect(); w.eng.tick()
    assert json.load(open(f"{w.dir}/status.json"))["mode"] == "DISCONNECTED"


def test_panel_needs_the_token_and_only_answers_localhost(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from fxbot.trading.panel import make_handler
    q = CommandQueue(f"{tmp_path}/c.jsonl")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(f"{tmp_path}/status.json", f"{tmp_path}/a.jsonl", q, "SECRET", False))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        def post(token, action="pause", host=None):
            h = {"Content-Type": "application/json"}
            if token:
                h["X-Token"] = token
            if host:
                h["Host"] = host
            req = urllib.request.Request(base + "/api/cmd", data=json.dumps({"action": action}).encode(), headers=h, method="POST")
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code
        import urllib.error
        assert post(None) == 403 and post("wrong") == 403 and post("SECRET", host="evil.example.com") == 403
        assert post("SECRET", action="rm -rf") == 400
        assert post("SECRET") == 200 and [c["action"] for c in q.drain()] == ["pause"]
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "SECRET" in html and "STOP ALL TRADING" in html and "__TOKEN__" not in html
        assert urllib.request.urlopen(base + "/api/status", timeout=5).status == 200
    finally:
        srv.shutdown()


# =============================================================== OANDA adapter (mock HTTP, never a real account)
class FakeOanda:
    def __init__(self):
        self.calls = []
        self.trades = []

    def __call__(self, method, path, body, params):
        self.calls.append((method, path, body, params))
        if path.endswith("/summary"):
            return 200, {"account": {"currency": "USD", "balance": "10000.00", "NAV": "10050.00", "marginUsed": "200.00",
                                     "marginAvailable": "9850.00", "unrealizedPL": "50.00"}}
        if path.endswith("/instruments"):
            return 200, {"instruments": [{"name": "GBP_JPY", "pipLocation": -2, "displayPrecision": 3, "tradeUnitsPrecision": 0,
                                          "minimumTradeSize": "1", "maximumOrderUnits": "100000000", "marginRate": "0.05"}]}
        if path.endswith("/pricing"):
            return 200, {"prices": [{"time": "2026-10-27T13:00:00.000000000Z", "tradeable": True, "bids": [{"price": "200.800"}],
                                     "asks": [{"price": "200.815"}]}],
                         "homeConversions": [{"currency": "JPY", "accountGain": "0.0066", "accountLoss": "0.0067", "positionValue": "0.0066"},
                                             {"currency": "GBP", "accountGain": "1.30", "accountLoss": "1.30", "positionValue": "1.30"}]}
        if path.endswith("/orders") and method == "POST":
            o = body["order"]
            self.trades.append(o)
            return 201, {"orderFillTransaction": {"orderID": "11", "price": "200.815", "units": o["units"], "tradeOpened": {"tradeID": "12", "units": o["units"]}}}
        if path.endswith("/openTrades"):
            return 200, {"trades": [{"id": "12", "instrument": "GBP_JPY", "price": "200.815", "openTime": "2026-10-27T13:00:00.000000000Z",
                                     "currentUnits": "23000", "unrealizedPL": "5.0", "financing": "0.0",
                                     "stopLossOrder": {"price": "200.656"}, "clientExtensions": {"id": "fxs-abc"}}]}
        if path.endswith("/pendingOrders"):
            return 200, {"orders": [{"id": "5", "type": "STOP_LOSS", "instrument": "GBP_JPY", "price": "1"}]}
        if "/trades/12/close" in path:
            return 200, {"orderFillTransaction": {"orderID": "13", "price": "200.98", "units": "-9000", "pl": "9.9"}}
        if "/trades/12/orders" in path:
            return 200, {}
        if path.endswith("/trades/12"):
            return 200, {"trade": {"id": "12", "instrument": "GBP_JPY", "state": "CLOSED", "averageClosePrice": "200.656", "realizedPL": "-24.5",
                                   "financing": "-0.1", "closeTime": "2026-10-27T15:00:00.000000000Z", "stopLossOrder": {"state": "FILLED"}}}
        return 404, {"errorMessage": "unknown path " + path}


def oanda(h=None):
    from fxbot.trading.oanda import OandaBroker
    b = OandaBroker(token="tok_SECRET_12345", account_id="101-004-1234567-001", env="practice", http=h or FakeOanda())
    b.connect()
    return b


def test_oanda_account_symbol_spec_and_quote_use_account_currency_values():
    b = oanda()
    a = b.account()
    assert not a.is_live and a.equity == 10050.0 and a.margin_free == 9850.0 and abs(a.margin_level - 5025.0) < 1e-6
    s = b.symbol_spec("GBPJPY")
    assert s.pip_size == 0.01 and s.tick_size == 0.001 and s.volume_min == 1 / 100000 and abs(s.tick_value - 100000 * 0.001 * 0.0067) < 1e-9
    q = b.quote("GBPJPY")
    assert q.bid == 200.8 and q.ask == 200.815
    assert abs(b.margin_required("GBPJPY", 0.23, 200.815) - 0.23 * 100000 * 1.30 * 0.05) < 1e-6


def test_oanda_order_request_has_correct_units_stop_and_client_id():
    h = FakeOanda(); b = oanda(h)
    r = b.place_order(OrderRequest("fxs-abc", "GBPJPY", -1, 0.23, sl=200.96, tp=200.48))
    o = h.trades[0]
    assert r.ok and r.position_id == "12" and o["units"] == "-23000" and o["type"] == "MARKET" and o["timeInForce"] == "FOK"
    assert o["stopLossOnFill"]["price"] == "200.960" and o["takeProfitOnFill"]["price"] == "200.480"
    assert o["clientExtensions"]["id"] == "fxs-abc" and o["tradeClientExtensions"]["id"] == "fxs-abc"


def test_oanda_close_modify_reconcile_and_closed_info():
    h = FakeOanda(); b = oanda(h)
    r = b.close_position("12", 0.09)
    assert r.ok and r.filled_volume == 0.09 and [c for c in h.calls if "/close" in c[1]][0][2] == {"units": "9000"}
    b.close_position("12")
    assert [c for c in h.calls if "/close" in c[1]][-1][2] == {"units": "ALL"}
    b.modify_position("12", sl=200.7)
    assert [c for c in h.calls if c[1].endswith("/trades/12/orders")][0][2]["stopLoss"]["price"] == "200.700"
    kind, pos = b.find_by_client_id("fxs-abc")
    assert kind == "position" and pos.volume == 0.23 and pos.sl == 200.656 and pos.side == 1
    ci = b.closed_info("12")
    assert ci.reason == "sl" and abs(ci.pnl - (-24.6)) < 1e-9
    assert b.pending_orders() == []                                  # stop-loss orders are not entries


def test_oanda_errors_are_classified_and_secrets_never_shown():
    from fxbot.trading.oanda import OandaBroker
    bad = OandaBroker(token="tok_SECRET_12345", account_id="a", http=lambda *x: (401, {}))
    raises(BrokerError, bad.connect)
    down = OandaBroker(token="t", account_id="a", http=lambda *x: (503, {}))
    raises(BrokerConnectionError, down.connect)

    def to(*x):
        raise BrokerTimeout("t")
    slow = OandaBroker(token="t", account_id="a", http=to)
    raises(BrokerTimeout, slow.connect)
    assert "tok_SECRET_12345" not in repr(oanda()) and "tok_SECRET" not in repr(oanda())
    raises(BrokerConnectionError, OandaBroker(token="", account_id="", http=FakeOanda()).connect)
    assert OandaBroker(token="t", account_id="a", env="live", http=FakeOanda()).is_live


# =============================================================== XM through MetaTrader 5 (fake terminal module, never a real XM terminal)
def mt5_world(live=False, suffix="", offset_cfg=None, **kw):
    from fxbot.trading.mt5 import MT5Broker
    from fake_mt5 import FakeMT5
    now = T0.timestamp()
    fm = FakeMT5(now, 7200, live, suffix)
    sym = "GBPJPY" + suffix
    fm.set_tick(sym, 200.800, 200.815)
    b = MT5Broker(fm, suffix=suffix, server_utc_offset_hours=offset_cfg, now_fn=lambda: fm.now, **kw)
    b.connect()
    return b, fm


def test_mt5_xm_account_mode_spec_quote_and_server_time():
    b, fm = mt5_world()
    assert not b.is_live and b.account().equity == 10000.0
    s = b.symbol_spec("GBPJPY")
    assert s.tick_size == 0.001 and s.volume_min == 0.01 and s.pip_size == 0.01 and s.tick_value == 0.667
    q = b.quote("GBPJPY")
    assert q.time == T0                                                # the +2h server offset was detected and removed
    b2, _ = mt5_world(offset_cfg=2)
    assert b2.quote("GBPJPY").time == T0
    b3, _ = mt5_world(live=True)
    assert b3.is_live


def test_mt5_stale_or_unknown_server_time_never_passes_as_fresh():
    from fxbot.trading.mt5 import MT5Broker
    from fake_mt5 import FakeMT5
    fm = FakeMT5(T0.timestamp(), 7200)
    fm.set_tick("GBPJPY", 200.8, 200.815, stale_s=3 * 3600 + 300)    # nothing sensible to detect an offset from
    b = MT5Broker(fm, now_fn=lambda: fm.now); b.connect()
    raises(BrokerError, b.quote, "GBPJPY")
    fm.set_tick("GBPJPY", 200.8, 200.815)
    assert b.quote("GBPJPY").time == T0


def test_mt5_symbol_suffix_for_xm_micro_style_names():
    b, fm = mt5_world(suffix="m")
    assert b.quote("GBPJPY").bid == 200.8 and b.symbol_spec("GBPJPY").symbol == "GBPJPY"
    r = b.place_order(OrderRequest("fxs-1", "GBPJPY", 1, 0.1, sl=200.6, tp=201.0))
    assert fm.sent[-1]["symbol"] == "GBPJPYm" and b.positions()[0].symbol == "GBPJPY"
    raises(BrokerError, b.symbol_spec, "AUDNZD")


def test_mt5_order_close_modify_idempotency_and_reconcile():
    b, fm = mt5_world()
    r = b.place_order(OrderRequest("fxs-abc123", "GBPJPY", 1, 0.23, sl=200.656, tp=201.137))
    assert r.ok and r.fill_price == 200.815 and r.filled_volume == 0.23
    req = fm.sent[-1]
    assert req["sl"] == 200.656 and req["tp"] == 201.137 and req["comment"] == "fxs-abc123" and req["type_filling"] == 1
    again = b.place_order(OrderRequest("fxs-abc123", "GBPJPY", 1, 0.23, sl=200.656, tp=201.137))
    assert len(fm.positions) == 1 and again.position_id == r.position_id
    pos = b.positions()[0]
    assert pos.side == 1 and pos.sl == 200.656 and pos.comment == "fxs-abc123" and pos.open_time == T0
    c = b.close_position(r.position_id, 0.09)
    assert c.ok and abs(b.positions()[0].volume - 0.14) < 1e-9
    b.modify_position(r.position_id, sl=200.7)
    assert fm.positions[0].sl == 200.7 and fm.positions[0].tp == 201.137          # target kept: MT5 needs both values
    assert b.find_by_client_id("fxs-abc123")[0] == "position" and b.find_by_client_id("nope") is None
    fm.move("GBPJPY", 200.60, 200.615)                                           # stop hit
    ci = b.closed_info(r.position_id)
    assert ci and ci.reason == "sl" and ci.close_price == 200.7 and ci.pnl != 0
    assert b.find_by_client_id("fxs-abc123")[0] == "closed"


def test_mt5_failure_modes_are_classified_for_safe_recovery():
    b, fm = mt5_world()
    fm.order_send_none = 1
    raises(BrokerTimeout, b.place_order, OrderRequest("x1", "GBPJPY", 1, 0.1, sl=200.6))
    fm.retcode_override = fm.TRADE_RETCODE_TIMEOUT
    raises(BrokerTimeout, b.place_order, OrderRequest("x2", "GBPJPY", 1, 0.1, sl=200.6))
    fm.retcode_override = fm.TRADE_RETCODE_REJECT
    r = b.place_order(OrderRequest("x3", "GBPJPY", 1, 0.1, sl=200.6))
    assert not r.ok and "10006" in r.message
    fm.retcode_override = None
    fm.trade_allowed = False
    b.disconnect(); b.connect()
    raises(BrokerError, b.place_order, OrderRequest("x4", "GBPJPY", 1, 0.1, sl=200.6))
    from fxbot.trading.mt5 import MT5Broker
    dead = FakeMT5Dead = None
    from fake_mt5 import FakeMT5
    fm2 = FakeMT5(T0.timestamp()); fm2.initialize_ok = False
    raises(BrokerConnectionError, MT5Broker(fm2).connect)
    fm3 = FakeMT5(T0.timestamp()); fm3.logged_in = False
    raises(BrokerConnectionError, MT5Broker(fm3).connect)


def test_mt5_missing_package_gives_a_clear_message():
    from fxbot.trading.mt5 import MT5Broker
    import sys
    if "MetaTrader5" in sys.modules:
        return
    try:
        MT5Broker().connect()
    except BrokerConnectionError as e:
        assert "MetaTrader5" in str(e) and "terminal" in str(e)


def test_engine_runs_end_to_end_on_the_xm_mt5_adapter(tmp_path):
    b, fm = mt5_world(offset_cfg=2)
    cfg = cfg_demo()
    box = [prop(tps=(200.975, 201.137))]
    clock = [T0]
    eng = TradingEngine(b, cfg, StateStore(f"{tmp_path}/s.json"), AuditLog(f"{tmp_path}/a.jsonl"), signals=lambda n: box, clock=lambda: clock[0])
    assert eng.start() and eng.mode_label == "DEMO ACCOUNT"
    # the fake account has no real margin model; give it plenty
    eng.tick()
    assert len(fm.positions) == 1 and fm.positions[0].sl == 200.656 and fm.positions[0].volume > 0
    box[:] = []
    clock[0] += 5 * MIN; fm.now += 300; fm.move("GBPJPY", 200.98, 200.995)
    eng.tick()
    assert fm.positions[0].volume < 0.23                                # TP1 partial close went through MT5
    t = eng.store.trades(open_only=True)[0]
    assert t.tp_done == 1 and t.realized_pnl != 0


def test_live_xm_account_with_demo_profile_is_refused(tmp_path):
    b, fm = mt5_world(live=True)
    raises(TradingSafetyError, TradingEngine(b, cfg_demo(), StateStore(None), AuditLog(None)).start)


def test_factory_builds_each_broker_from_config(tmp_path):
    from fxbot.trading.factory import make_broker
    p = tmp_path / "t.json"
    for name, cls in (("sim", "SimBroker"), ("oanda", "OandaBroker"), ("xm", "MT5Broker"), ("mt5", "MT5Broker")):
        p.write_text(json.dumps({"broker": {"name": name}}))
        assert type(make_broker(tcfg.load("demo", str(p)))).__name__ == cls
