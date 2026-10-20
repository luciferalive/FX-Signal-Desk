"""The three tuned views the bot generates signals on, and the nine timeframes the dashboard can show."""
MODES = {
    "scalp": dict(interval="15m", htf="1h", htf_name="1H", label="Scalp", tf="15m"),
    "swing": dict(interval="4h", htf="1d", htf_name="1D", label="Swing", tf="4H"),
    "daily": dict(interval="1d", htf=None, htf_name="", label="Daily", tf="1D"),
}

# Every timeframe the dashboard shows (same list as TradingView). `mode` is the tuned view that produces
# signals on that timeframe; None means analysis only (levels, zones, averages, patterns, trend) with no signal.
# Signals exist only where the rules were designed and can be validated: 15m, 4H and 1D.
TIMEFRAMES = {
    "1m":  dict(label="1m",  htf="15m", htf_name="15m", mode=None,    intraday=True,  tv="1"),
    "5m":  dict(label="5m",  htf="1h",  htf_name="1H",  mode=None,    intraday=True,  tv="5"),
    "15m": dict(label="15m", htf="1h",  htf_name="1H",  mode="scalp", intraday=True,  tv="15"),
    "30m": dict(label="30m", htf="4h",  htf_name="4H",  mode=None,    intraday=True,  tv="30"),
    "1h":  dict(label="1h",  htf="4h",  htf_name="4H",  mode=None,    intraday=True,  tv="60"),
    "4h":  dict(label="4H",  htf="1d",  htf_name="1D",  mode="swing", intraday=True,  tv="240"),
    "1d":  dict(label="1D",  htf=None,  htf_name="",    mode="daily", intraday=False, tv="D"),
    "1w":  dict(label="1W",  htf=None,  htf_name="",    mode=None,    intraday=False, tv="W"),
    "1M":  dict(label="1M",  htf=None,  htf_name="",    mode=None,    intraday=False, tv="M"),
}
