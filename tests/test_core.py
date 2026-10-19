import numpy as np
import pandas as pd
import pytest

from fxbot.analysis import analyze
from fxbot.backtest import run_backtest
from fxbot.data import resample, synthetic
from fxbot.modes import MODES
from fxbot.sessions import killzone_mask
from fxbot.strategies import STRATEGIES
from fxbot.zones import find_fvgs, find_obs


def test_london_killzone_handles_dst():
    summer = pd.DatetimeIndex(["2026-07-01 05:59", "2026-07-01 06:00", "2026-07-01 09:00"], tz="UTC")
    assert killzone_mask(summer).tolist() == [False, True, False]  # EDT = UTC-4
    winter = pd.DatetimeIndex(["2026-01-05 06:59", "2026-01-05 07:00"], tz="UTC")
    assert killzone_mask(winter).tolist() == [False, True]  # EST = UTC-5


CASES = [(cls, mode) for cls in STRATEGIES.values() for mode in cls.PRESETS]


def _df(interval):
    return synthetic(interval, days=900 if interval == "1d" else 400)


@pytest.mark.parametrize("cls,mode", CASES)
def test_no_lookahead(cls, mode):
    strat = cls(mode)
    df = _df(strat.preset["interval"])
    k = int(len(df) * 0.7)
    full = {(s.idx, s.side) for s in strat.generate(df, "EURUSD") if s.idx < k}
    part = {(s.idx, s.side) for s in strat.generate(df.iloc[:k], "EURUSD")}
    assert full == part


@pytest.mark.parametrize("cls,mode", CASES)
def test_backtest_sane(cls, mode):
    strat = cls(mode)
    df = _df(strat.preset["interval"])
    res = run_backtest(df, strat.generate(df, "EURUSD"), "EURUSD")
    if res.stats["trades"]:
        assert res.trades["r"].max() <= strat.preset["rr"] + 1e-9
        assert (res.equity > 0).all()


def test_zones_have_geometry():
    df = synthetic("15m", days=60)
    for z in find_fvgs(df) + find_obs(df):
        assert z["top"] > z["bottom"] and z["side"] in (1, -1) and z["t0"]


@pytest.mark.parametrize("mode", list(MODES))
def test_analyze_every_mode(mode):
    base = synthetic("15m", days=900)
    m = MODES[mode]
    df = base if mode == "scalp" else resample(base, m["interval"])
    htf = resample(base, m["htf"]) if m["htf"] else None
    an = analyze("EURUSD", mode, df, htf, m["htf_name"])
    assert an["levels"] and "zones" in an and "smas" in an
