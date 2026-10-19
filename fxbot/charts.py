"""Interactive HTML chart: candles, killzone shading, trade boxes (TP green / SL red), equity curve."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .sessions import window_mask

ZONES = {"London KZ": "rgba(255,165,0,0.12)", "NY AM KZ": "rgba(0,170,80,0.12)",
         "Asia": "rgba(60,100,255,0.08)"}


def _blocks(mask: np.ndarray):
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def make_chart(df: pd.DataFrame, trades: pd.DataFrame, title: str, path: str, last_n: int = 700) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    d = df.iloc[-last_n:]
    fig = make_subplots(rows=2, cols=1, row_heights=[0.75, 0.25], vertical_spacing=0.07)
    fig.add_trace(go.Candlestick(x=d.index, open=d["open"], high=d["high"], low=d["low"], close=d["close"],
                                 name="price"), row=1, col=1)
    for name, color in ZONES.items():
        for s, e in _blocks(window_mask(d.index, name)):
            x1 = d.index[e] if e < len(d) else d.index[-1]
            fig.add_vrect(x0=d.index[s], x1=x1, fillcolor=color, line_width=0, row=1, col=1,
                          annotation_text=name if name != "Asia" else "", annotation_position="top left",
                          annotation_font_size=9)
    if trades is not None and len(trades):
        t = trades[trades["fill_time"] >= d.index[0]]
        for r in t.itertuples():
            fig.add_shape(type="rect", x0=r.fill_time, x1=r.exit_time, y0=r.fill, y1=r.tp,
                          fillcolor="rgba(0,200,100,0.25)", line_width=0, row=1, col=1)
            fig.add_shape(type="rect", x0=r.fill_time, x1=r.exit_time, y0=r.fill, y1=r.sl,
                          fillcolor="rgba(230,50,50,0.25)", line_width=0, row=1, col=1)
        fig.add_trace(go.Scatter(x=t["fill_time"], y=t["fill"], mode="markers", name="entries",
                                 marker=dict(size=9, symbol=np.where(t["side"] > 0, "triangle-up", "triangle-down"),
                                             color="white", line=dict(width=1, color="black")),
                                 text=t["reason"], hoverinfo="text+x+y"), row=1, col=1)
        fig.add_trace(go.Scatter(x=trades["exit_time"], y=trades["equity"], mode="lines", name="equity"), row=2, col=1)
    fig.update_xaxes(rangeslider_visible=False, rangebreaks=[dict(bounds=["sat", "mon"])], row=1, col=1)
    fig.update_layout(title=title, height=850, template="plotly_dark")
    fig.write_html(path, include_plotlyjs="cdn")
