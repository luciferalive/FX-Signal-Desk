"""The three ways the bot looks at a market. Each strategy declares which of these it supports."""
MODES = {
    "scalp": dict(interval="15m", htf="1h", htf_name="1H", label="Scalp", tf="15m"),
    "swing": dict(interval="4h", htf="1d", htf_name="1D", label="Swing", tf="4H"),
    "daily": dict(interval="1d", htf=None, htf_name="", label="Daily", tf="1D"),
}
