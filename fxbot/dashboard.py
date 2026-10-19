"""Builds one self-contained dashboard page. The chart is drawn on a canvas with no external
library, so FVGs, order blocks, levels, SMAs and the trade box all sit on the candles."""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

from . import DEFAULT_SYMBOLS
from .analysis import analyze
from .data import fetch
from .modes import MODES
from .scanner import closed_only
from .sessions import session_labels
from .zones import sma_series

IST_OFFSET = 19800  # the chart axis shows India time
N_CANDLES = 420


def _t(x) -> int:
    return int(pd.Timestamp(x).timestamp()) + IST_OFFSET


def _clean(o):
    """Plain JSON: numpy -> python, NaN -> None."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def _candles(df: pd.DataFrame, dec: int):
    d = df.iloc[-N_CANDLES:]
    sess = session_labels(d.index)
    return [[_t(t), round(float(o), dec), round(float(h), dec), round(float(l), dec), round(float(c), dec), str(s)]
            for t, o, h, l, c, s in zip(d.index, d["open"], d["high"], d["low"], d["close"], sess)]


def _stamp(an: dict) -> dict:
    """Convert every ISO time in the analysis to chart seconds (IST axis)."""
    for L in an["levels"]:
        L["t"] = _t(L["t0"]) if L.get("t0") else None
    for grp in an["zones"].values():
        for z in grp:
            z["t"] = _t(z["t0"])
    if an.get("range"):
        an["range"]["t"] = _t(an["range"]["t0"])
    for p in an["patterns"]:
        p["t"] = _t(p["time"])
    if an["signal"]:
        an["signal"]["t"] = _t(an["signal"]["time"])
    return an


def collect(symbols=None, loader=None) -> list[dict]:
    now = pd.Timestamp.now(tz="UTC")
    loader = loader or (lambda s, iv: closed_only(fetch(s, iv), iv, now))
    cache: dict = {}

    def get(sym, iv):
        if (sym, iv) not in cache:
            cache[(sym, iv)] = loader(sym, iv)
        return cache[(sym, iv)]

    pairs = []
    for sym in symbols or DEFAULT_SYMBOLS:
        modes = {}
        for mode, m in MODES.items():
            try:
                df = get(sym, m["interval"])
                htf = get(sym, m["htf"]) if m["htf"] else None
                an = _stamp(analyze(sym, mode, df, htf, m["htf_name"]))
                dec = an["price_dec"]
                modes[mode] = dict(an=an, candles=_candles(df, dec), sma=sma_series(df, N_CANDLES, dec=dec))
            except Exception as e:  # one broken feed must not kill the page
                print(f"[dashboard] {sym} {mode}: {e}")
        if modes:
            pairs.append(dict(symbol=sym, modes=modes))
    return pairs


def build(symbols=None, path="docs/index.html", loader=None, demo=False, standalone=True) -> str:
    pairs = collect(symbols, loader)
    if not pairs:
        raise RuntimeError("no data for any symbol")
    data = dict(generated=pd.Timestamp.now(tz="UTC").isoformat(), demo=demo, pairs=pairs)
    blob = json.dumps(_clean(data), separators=(",", ":")).replace("</", "<\\/")
    body = BODY.replace("__DATA__", blob)
    html = (WRAP + HEAD + "</head><body>" + body + "</body></html>") if standalone else (HEAD + body)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


WRAP = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'

HEAD = r"""<title>FX Signal Desk</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
/* Layout: one full-width column. Chart first (everything is drawn on it), signal card under it, reference panels last. */
:root {
  color-scheme: dark;
  --bg: #0b1016; --surface: #121921; --raise: #19222d; --line: #223040;
  --fg: #e8edf4; --muted: #8997a9;
  --buy: #2fc58a; --sell: #f2666c; --accent: #6aa9ff; --warn: #e6b04c;
  --c50: #4cc9f0; --c100: #b794f6; --c200: #f1f5f9; --open: #2dd4bf;
  --kz-lon: rgba(230,176,76,.15); --kz-ny: rgba(47,197,138,.13); --kz-pm: rgba(183,148,246,.12); --kz-asia: rgba(106,169,255,.08);
  --ui: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    color-scheme: light;
    --bg: #f2f4f7; --surface: #ffffff; --raise: #eef1f5; --line: #d9e0ea;
    --fg: #121923; --muted: #566375;
    --buy: #0f8a5c; --sell: #c63b42; --accent: #2a60c2; --warn: #94650b;
    --c50: #0b86b3; --c100: #7a49c9; --c200: #1b2432; --open: #0b8a7c;
    --kz-lon: rgba(200,140,20,.15); --kz-ny: rgba(15,138,92,.12); --kz-pm: rgba(122,73,201,.10); --kz-asia: rgba(42,96,194,.08);
  }
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #f2f4f7; --surface: #ffffff; --raise: #eef1f5; --line: #d9e0ea;
  --fg: #121923; --muted: #566375;
  --buy: #0f8a5c; --sell: #c63b42; --accent: #2a60c2; --warn: #94650b;
  --c50: #0b86b3; --c100: #7a49c9; --c200: #1b2432; --open: #0b8a7c;
  --kz-lon: rgba(200,140,20,.15); --kz-ny: rgba(15,138,92,.12); --kz-pm: rgba(122,73,201,.10); --kz-asia: rgba(42,96,194,.08);
}
body { background: var(--bg); color: var(--fg); font: 14px/1.5 var(--ui); padding-block: 16px 40px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.wrap { max-width: 1320px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }
h1 { font-size: 19px; font-weight: 600; letter-spacing: -.01em; margin: 0; }
.sub { color: var(--muted); font-size: 12.5px; }
.bar { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; }
.banner { background: var(--raise); border: 1px solid var(--warn); color: var(--warn); padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
button { font: inherit; color: var(--muted); background: var(--surface); border: 1px solid var(--line); padding: 5px 11px; border-radius: 6px; cursor: pointer; }
button:hover:not(:disabled) { color: var(--fg); }
button:disabled { opacity: .4; cursor: default; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button[aria-pressed="true"] { color: var(--fg); background: var(--raise); border-color: var(--accent); }
.tg { display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; font-size: 12.5px; }
.tg i { width: 10px; height: 3px; border-radius: 2px; background: var(--muted); }
.tg[aria-pressed="false"] i { opacity: .35; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-width: 0; }
.chartbox { position: relative; height: clamp(380px, 66vh, 700px); touch-action: pan-y; }
#cv { width: 100%; height: 100%; display: block; cursor: crosshair; touch-action: pan-y; }
.zoom { margin-left: auto; display: flex; gap: 4px; }
.zoom button { padding: 2px 9px; background: var(--raise); }
.tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 8px; }
details { margin-top: 8px; color: var(--muted); font-size: 12.5px; }
summary { cursor: pointer; }
details ul { margin: 6px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 3px; }
.sig { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
.sig-head { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
.badge { font-weight: 600; letter-spacing: .06em; padding: 4px 12px; border-radius: 4px; color: #fff; }
.badge.buy { background: var(--buy); } .badge.sell { background: var(--sell); } .badge.none { background: var(--raise); color: var(--muted); }
.pill { font-size: 12px; padding: 2px 8px; border-radius: 99px; border: 1px solid var(--line); color: var(--muted); }
.pill.A, .pill.B { color: var(--buy); border-color: var(--buy); } .pill.C { color: var(--warn); border-color: var(--warn); }
.levels4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
.lv { background: var(--raise); border-radius: 6px; padding: 10px 12px; min-width: 0; }
.lv .k { color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: .07em; }
.lv .v { font: 500 18px var(--mono); font-variant-numeric: tabular-nums; margin-top: 2px; }
.lv .p { color: var(--muted); font-size: 12px; }
.lv.sl .v { color: var(--sell); } .lv.tp .v { color: var(--buy); }
ul.why { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; max-width: 80ch; }
.note { color: var(--muted); font-size: 12.5px; }
.checks { display: flex; flex-wrap: wrap; gap: 6px; }
.ck { font-size: 12.5px; padding: 3px 9px; border-radius: 4px; border: 1px solid var(--line); }
.ck.ok { color: var(--buy); border-color: var(--buy); } .ck.ok::before { content: "✓ "; }
.ck.no { color: var(--muted); text-decoration: line-through; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 12px; }
h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .09em; color: var(--muted); margin: 0 0 10px; }
.kv { display: flex; justify-content: space-between; gap: 10px; padding: 5px 0; border-bottom: 1px solid var(--line); }
.kv:last-child { border-bottom: 0; }
.num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.scroll { overflow-x: auto; max-height: 330px; overflow-y: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td, th { padding: 5px 6px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 500; font-size: 11.5px; }
td.n { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
.up { color: var(--buy); } .dn { color: var(--sell); } .flat { color: var(--warn); }
.pat { padding: 7px 0; border-bottom: 1px solid var(--line); } .pat:last-child { border-bottom: 0; }
@media (max-width: 560px) { .lv .v { font-size: 16px; } }
</style>"""

BODY = r"""
<div class="wrap">
  <div class="bar">
    <div><h1>FX Signal Desk</h1><div class="sub" id="upd"></div></div>
    <div class="chips" id="pairs" role="group" aria-label="Pair"></div>
  </div>
  <div id="demo" class="banner" hidden>Demo data: synthetic prices to show the layout. The live bot uses real market data.</div>
  <div class="card">
    <div class="tools">
      <div class="chips" id="modes" role="group" aria-label="Style"></div>
      <div class="chips" id="toggles" role="group" aria-label="Chart layers"></div>
      <div class="zoom"><button id="zout" aria-label="Zoom out">−</button><button id="zin" aria-label="Zoom in">+</button><button id="zfit">Fit</button></div>
    </div>
    <div class="chartbox" id="box">
      <canvas id="cv" role="img" aria-label="Price chart with signal, zones and levels"></canvas>
    </div>
    <details>
      <summary>How to read this chart</summary>
      <ul>
        <li>Green and red boxes are unfilled fair value gaps. Dashed boxes are order blocks. They start where they formed and run right until price closes through them.</li>
        <li>Amber lines are liquidity: previous day and week highs and lows, and the Asia range. Blue lines are London / New York session highs and lows. Teal lines are the daily, midnight and weekly opens.</li>
        <li>The dashed EQ line is the middle of the current swing range. Below it is discount (buys), above it is premium (sells).</li>
        <li>Coloured curves are the 50, 100 and 200 simple moving averages. Shaded vertical bands are the Asia, London and New York sessions.</li>
        <li>The green and red box to the right of a signal is the trade: entry, stop loss, TP1 and TP2.</li>
        <li>Drag to move, scroll or pinch to zoom, press Fit to come back to the latest candles. Times are India time.</li>
      </ul>
    </details>
  </div>
  <div class="card sig" id="signal"></div>
  <div class="cols">
    <div class="card"><h2>Trend, averages and range</h2><div id="trend"></div></div>
    <div class="card"><h2>Patterns</h2><div class="scroll" id="pats"></div></div>
    <div class="card"><h2>Key levels</h2><div class="scroll" id="levels"></div></div>
  </div>
  <div class="sub">Signals are mechanical setups, not predictions or financial advice. Risk 1% or less per trade and paper trade first.</div>
</div>
<script>
const DATA = __DATA__;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const MODE_LABEL = { scalp: 'Scalp · 15m', swing: 'Swing · 4H', daily: 'Daily · 1D' };
const TOGGLES = [['sessions','Sessions','--kz-lon'],['levels','Levels','--warn'],['fvg','FVG','--buy'],['ob','Order blocks','--accent'],
  ['sma50','SMA 50','--c50'],['sma100','SMA 100','--c100'],['sma200','SMA 200','--c200'],['patterns','Patterns','--sell']];
let on = { sessions: true, levels: true, fvg: true, ob: true, sma50: true, sma100: true, sma200: true, patterns: true };
let sym = DATA.pairs[0].symbol, mode = 'scalp';
try {
  const s = JSON.parse(localStorage.getItem('fxdesk2') || 'null');
  if (s) { if (DATA.pairs.some((p) => p.symbol === s.sym)) sym = s.sym; if (MODE_LABEL[s.mode]) mode = s.mode; if (s.on) on = Object.assign(on, s.on); }
} catch (e) {}
const save = () => { try { localStorage.setItem('fxdesk2', JSON.stringify({ sym, mode, on })); } catch (e) {} };

/* ---------- theme ---------- */
let T = {};
function readTheme() {
  const cs = getComputedStyle(document.documentElement), g = (n) => cs.getPropertyValue(n).trim();
  T = { bg: g('--surface'), fg: g('--fg'), muted: g('--muted'), line: g('--line'), buy: g('--buy'), sell: g('--sell'), accent: g('--accent'),
    warn: g('--warn'), c50: g('--c50'), c100: g('--c100'), c200: g('--c200'), open: g('--open'),
    kz: { 'London KZ': g('--kz-lon'), 'NY AM KZ': g('--kz-ny'), 'NY PM KZ': g('--kz-pm'), 'Asia': g('--kz-asia') },
    mono: '11px ' + (g('--mono') || 'monospace'), ui: '11px ' + (g('--ui') || 'sans-serif') };
}
function alpha(c, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(c); if (!m) return c;
  const n = parseInt(m[1], 16); return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
}

/* ---------- chart ---------- */
const cv = $('#cv'), ctx = cv.getContext('2d');
const AX = 66, AXH = 24, RPAD = 18;
let W = 0, H = 0, plotW = 0, plotH = 0, dpr = 1;
let M = null, AN = null, C = [], TS = [], barW = 8, start = 0, cross = null, raf = 0;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const pad2 = (n) => String(n).padStart(2, '0');
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const tHM = (t) => { const d = new Date(t * 1000); return pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes()); };
const tDM = (t) => { const d = new Date(t * 1000); return d.getUTCDate() + ' ' + MON[d.getUTCMonth()]; };
function idxOf(t) { let lo = 0, hi = TS.length; while (lo < hi) { const m = (lo + hi) >> 1; if (TS[m] < t) lo = m + 1; else hi = m; } return Math.min(lo, TS.length - 1); }

function layout() {
  const r = cv.getBoundingClientRect();
  W = Math.max(200, Math.round(r.width)); H = Math.max(200, Math.round(r.height));
  dpr = Math.min(window.devicePixelRatio || 1, 2.5);
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  plotW = W - AX; plotH = H - AXH;
}
function clampView() { const vis = plotW / barW; start = clamp(start, -vis * 0.6, C.length + RPAD - vis); }
function fit() {
  const show = Math.min(C.length, plotW < 520 ? 55 : mode === 'scalp' ? 120 : 90);
  barW = clamp(plotW / (show + RPAD), 3, 30);
  start = C.length - plotW / barW + RPAD; clampView();
}
const schedule = () => { if (!raf) raf = requestAnimationFrame(draw); };

function niceStep(range, target) {
  const raw = range / target, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
}
function spreadLabels(items) {
  items.sort((a, b) => a.y - b.y);
  const placed = [];
  for (const it of items) {
    for (let k = 0; k < 20; k++) {
      const hit = placed.find((p) => Math.abs(p.y - it.y) < 13 && it.x < p.x + p.w && p.x < it.x + it.w);
      if (!hit) break;
      it.y = hit.y + 13;
    }
    placed.push(it);
  }
}
function pill(x, y, text, color, opts) {
  opts = opts || {};
  ctx.font = T.mono;
  const w = Math.ceil(ctx.measureText(text).width) + 8;
  const x0 = opts.right ? x - w : x;
  ctx.fillStyle = alpha(T.bg, 0.86); ctx.fillRect(x0, y - 8, w, 15);
  if (opts.border) { ctx.strokeStyle = alpha(color, 0.7); ctx.lineWidth = 1; ctx.strokeRect(x0 + 0.5, y - 7.5, w - 1, 14); }
  ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.textAlign = 'left'; ctx.fillText(text, x0 + 4, y);
  return w;
}

function draw() {
  raf = 0;
  if (!M) return;
  const n = C.length, vis = plotW / barW, dec = AN.price_dec;
  const x = (i) => (i - start + 0.5) * barW;
  const i0 = Math.max(0, Math.floor(start)), i1 = Math.min(n - 1, Math.ceil(start + vis));
  let lo = Infinity, hi = -Infinity;
  for (let i = i0; i <= i1; i++) { if (C[i][3] < lo) lo = C[i][3]; if (C[i][2] > hi) hi = C[i][2]; }
  if (!isFinite(lo)) { lo = C[n - 1][3]; hi = C[n - 1][2]; }
  const sg = AN.signal;
  if (sg && idxOf(sg.t) >= i0 - 5) for (const p of [sg.entry, sg.sl, sg.tp2]) { lo = Math.min(lo, p); hi = Math.max(hi, p); }
  const padP = (hi - lo) * 0.08 || hi * 0.001; lo -= padP; hi += padP;
  const y = (p) => (hi - p) / (hi - lo) * plotH;
  const f = (p) => Number(p).toFixed(dec);
  const padPx = Math.max(0, (n + RPAD - 1 - (start + vis)) * -1);
  const lastX = x(n - 1);
  const compact = plotW - lastX < 120, narrow = plotW < 520;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
  ctx.save(); ctx.beginPath(); ctx.rect(0, 0, plotW, plotH); ctx.clip();

  /* grid */
  const step = niceStep(hi - lo, 7);
  ctx.lineWidth = 1; ctx.strokeStyle = alpha(T.line, 0.7);
  ctx.beginPath();
  for (let p = Math.ceil(lo / step) * step; p < hi; p += step) { const yy = Math.round(y(p)) + 0.5; ctx.moveTo(0, yy); ctx.lineTo(plotW, yy); }
  ctx.stroke();

  /* sessions */
  if (on.sessions && mode === 'scalp') {
    let rs = -1, rn = null;
    const flush = (end) => {
      if (rn && T.kz[rn]) {
        const xa = x(rs) - barW / 2, xb = x(end) + barW / 2;
        ctx.fillStyle = T.kz[rn]; ctx.fillRect(xa, 0, xb - xa, plotH);
        if (xb - xa > 58) { ctx.font = T.ui; ctx.fillStyle = T.muted; ctx.textAlign = 'left'; ctx.textBaseline = 'top'; ctx.fillText(rn, Math.max(xa, 0) + 4, 4); }
      }
    };
    for (let i = i0; i <= i1; i++) { const nm = C[i][5]; if (nm !== rn) { flush(i - 1); rn = nm; rs = i; } }
    flush(i1);
  }

  /* zones: FVG and order blocks, from where they formed to the right edge */
  const zones = (list, label, fillA, dashed) => {
    for (const z of list) {
      const xa = Math.max(x(idxOf(z.t)) - barW / 2, 0), yt = y(z.top), yb = y(z.bottom);
      if (yb < 0 || yt > plotH) continue;
      const col = z.side > 0 ? T.buy : T.sell;
      ctx.fillStyle = alpha(col, fillA); ctx.fillRect(xa, yt, plotW - xa, Math.max(1, yb - yt));
      ctx.strokeStyle = alpha(col, 0.75); ctx.lineWidth = 1; ctx.setLineDash(dashed ? [5, 3] : []);
      ctx.strokeRect(xa + 0.5, yt + 0.5, plotW - xa, Math.max(1, yb - yt));
      ctx.setLineDash([]);
      ctx.font = T.ui; ctx.fillStyle = col; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
      if (yb - yt >= 12) ctx.fillText((z.side > 0 ? '▲ ' : '▼ ') + label + (z.status === 'tested' ? ' (tested)' : ''), xa + 4, yt + 2);
    }
  };
  if (on.fvg) zones(AN.zones.fvgs, 'FVG', 0.16, false);
  if (on.ob) zones(AN.zones.obs, 'OB', 0.12, true);

  /* levels */
  const labels = [];
  if (on.levels) {
    const line = (L, col, dash, w) => {
      const yy = Math.round(y(L.price)) + 0.5; if (yy < -2 || yy > plotH + 2) return;
      const xa = L.t ? Math.max(x(idxOf(L.t)) - barW / 2, 0) : 0;
      ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(xa, yy); ctx.lineTo(plotW, yy); ctx.stroke(); ctx.setLineDash([]);
      const txt = narrow ? L.name : L.name + ' ' + f(L.price);
      return { x: Math.max(xa, 0) + 3, y: yy - 8, w: (txt.length + 1) * 6.6, text: txt, color: col };
    };
    for (const L of AN.levels) {
      let col = T.warn, dash = [], w = 1.2;
      if (L.kind === 'session') col = T.accent;
      else if (L.kind === 'swing') { col = T.muted; dash = [2, 3]; w = 1; }
      else if (L.kind === 'open') { col = T.open; dash = [6, 3]; }
      else if (L.kind === 'day') { col = T.muted; dash = [1, 3]; w = 1; }
      else if (L.kind === 'handle') { col = alpha(T.muted, 0.55); dash = [1, 4]; w = 1; }
      if (L.kind === 'handle' && L.name === 'Half-handle') continue;
      if (narrow && (L.kind === 'day' || L.kind === 'handle')) continue;
      const lab = line(L, col, dash, w);
      if (lab && L.kind !== 'handle') labels.push(lab);
    }
    const R = AN.range;
    if (R) {
      const yy = Math.round(y(R.eq)) + 0.5;
      if (yy > 0 && yy < plotH) {
        const xa = Math.max(x(idxOf(R.t)) - barW / 2, 0);
        ctx.strokeStyle = alpha(T.fg, 0.45); ctx.lineWidth = 1; ctx.setLineDash([8, 4]);
        ctx.beginPath(); ctx.moveTo(xa, yy); ctx.lineTo(plotW, yy); ctx.stroke(); ctx.setLineDash([]);
        labels.push({ x: Math.max(xa, 0) + 3, y: yy - 8, w: 150, text: narrow ? 'EQ' : 'EQ ' + f(R.eq) + ' (' + R.zone + ' now)', color: alpha(T.fg, 0.8) });
      }
    }
  }

  /* moving averages */
  const smaDefs = [['sma50', 'sma', '50', T.c50, 1.4], ['sma100', 'sma', '100', T.c100, 1.4], ['sma200', 'sma', '200', T.c200, 2]];
  const smaTags = [];
  for (const [key, , per, col, w] of smaDefs) {
    if (!on[key]) continue;
    const arr = M.sma[per]; if (!arr) continue;
    ctx.strokeStyle = col; ctx.lineWidth = w; ctx.beginPath();
    let pen = false;
    for (let i = Math.max(0, i0 - 1); i <= Math.min(n - 1, i1 + 1); i++) {
      const v = arr[i]; if (v == null) { pen = false; continue; }
      if (!pen) { ctx.moveTo(x(i), y(v)); pen = true; } else ctx.lineTo(x(i), y(v));
    }
    ctx.stroke();
    const lv = arr[n - 1];
    if (lv != null && y(lv) > 0 && y(lv) < plotH) smaTags.push({ x: lastX + 6, y: y(lv), w: 60, text: 'SMA' + per, color: col });
  }

  /* candles */
  const bw = Math.max(1, Math.floor(barW * 0.7));
  ctx.lineWidth = 1;
  for (let i = i0; i <= i1; i++) {
    const c = C[i], xc = x(i), up = c[4] >= c[1], col = up ? T.buy : T.sell;
    ctx.strokeStyle = col; ctx.fillStyle = col;
    const xw = Math.round(xc) + 0.5;
    ctx.beginPath(); ctx.moveTo(xw, y(c[2])); ctx.lineTo(xw, y(c[3])); ctx.stroke();
    const top = y(Math.max(c[1], c[4])), bot = y(Math.min(c[1], c[4]));
    ctx.fillRect(Math.round(xc - bw / 2), top, bw, Math.max(1, bot - top));
  }

  /* the signal as a position box */
  const rightLabels = [];
  if (sg) {
    const si = idxOf(sg.t), xa = Math.max(x(si) - barW / 2, 0), long = sg.side === 'BUY';
    const yE = y(sg.entry), yS = y(sg.sl), yT1 = y(sg.tp1), yT2 = y(sg.tp2);
    ctx.fillStyle = alpha(T.buy, 0.14); ctx.fillRect(xa, Math.min(yE, yT2), plotW - xa, Math.abs(yT2 - yE));
    ctx.fillStyle = alpha(T.sell, 0.14); ctx.fillRect(xa, Math.min(yE, yS), plotW - xa, Math.abs(yS - yE));
    const ln = (yy, col, dash, w) => { ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash); ctx.beginPath(); ctx.moveTo(xa, Math.round(yy) + 0.5); ctx.lineTo(plotW, Math.round(yy) + 0.5); ctx.stroke(); ctx.setLineDash([]); };
    ln(yE, T.accent, [], 1.6); ln(yS, T.sell, [], 1.6); ln(yT1, T.buy, [5, 3], 1.2); ln(yT2, T.buy, [], 1.6);
    const rp = (txt, yy, col) => rightLabels.push({ x: plotW - 4 - (txt.length + 1) * 6.6, y: yy, w: (txt.length + 1) * 6.6, text: txt, color: col, right: true, border: true });
    rp((compact ? 'ENTRY' : 'ENTRY ' + f(sg.entry)), yE, T.accent);
    rp((compact ? 'SL' : 'SL ' + f(sg.sl) + ' · ' + sg.risk_pips + 'p'), yS, T.sell);
    rp((compact ? 'TP1' : 'TP1 ' + f(sg.tp1)), yT1, T.buy);
    rp((compact ? 'TP2' : 'TP2 ' + f(sg.tp2) + ' · RR ' + Number(sg.rr).toFixed(1)), yT2, T.buy);
    /* arrow on the signal candle */
    const sx = x(si), sy = long ? y(C[si][3]) + 14 : y(C[si][2]) - 14;
    ctx.fillStyle = long ? T.buy : T.sell; ctx.beginPath();
    if (long) { ctx.moveTo(sx, sy - 7); ctx.lineTo(sx - 6, sy + 5); ctx.lineTo(sx + 6, sy + 5); } else { ctx.moveTo(sx, sy + 7); ctx.lineTo(sx - 6, sy - 5); ctx.lineTo(sx + 6, sy - 5); }
    ctx.closePath(); ctx.fill();
    ctx.font = '600 11px ' + T.mono.split(' ').slice(1).join(' '); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(sg.side, sx, long ? sy + 17 : sy - 17);
  }

  /* pattern markers */
  if (on.patterns) {
    const used = {};
    const short = (s) => s.replace('Liquidity sweep of ', 'Sweep ').replace('Hammer / bullish pin bar', 'Pin bar').replace('Shooting star / bearish pin bar', 'Pin bar')
      .replace('Bullish Engulfing', 'Engulfing').replace('Bearish Engulfing', 'Engulfing').replace('Inverse Head & Shoulders', 'Inv H&S').replace('Head & Shoulders', 'H&S')
      .replace('Ascending Triangle', 'Asc triangle').replace('Descending Triangle', 'Desc triangle');
    for (const p of AN.patterns) {
      if (!p.side || !p.t) continue;
      const i = idxOf(p.t); if (i < i0 || i > i1) continue;
      const k = (used[i] = (used[i] || 0) + 1) - 1, up = p.side > 0, col = up ? T.buy : T.sell;
      const px = x(i), py = up ? y(C[i][3]) + 10 + k * 13 : y(C[i][2]) - 10 - k * 13;
      ctx.fillStyle = alpha(col, 0.9); ctx.beginPath();
      if (up) { ctx.moveTo(px, py - 4); ctx.lineTo(px - 4, py + 3); ctx.lineTo(px + 4, py + 3); } else { ctx.moveTo(px, py + 4); ctx.lineTo(px - 4, py - 3); ctx.lineTo(px + 4, py - 3); }
      ctx.closePath(); ctx.fill();
      ctx.font = T.ui; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillStyle = col;
      ctx.fillText(short(p.name), px + 7, py);
    }
  }

  /* labels last, nudged apart so none overlap */
  spreadLabels(labels); spreadLabels(rightLabels.concat(smaTags));
  for (const l of labels) pill(l.x, l.y, l.text, l.color);
  for (const l of rightLabels) pill(l.x + l.w, l.y, l.text, l.color, { right: true, border: true });
  for (const l of smaTags) if (!rightLabels.some((r) => Math.abs(r.y - l.y) < 12)) pill(l.x, l.y, l.text, l.color);

  ctx.restore();

  /* price axis */
  ctx.fillStyle = T.bg; ctx.fillRect(plotW, 0, AX, H);
  ctx.strokeStyle = T.line; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(plotW + 0.5, 0); ctx.lineTo(plotW + 0.5, plotH); ctx.stroke();
  ctx.font = T.mono; ctx.fillStyle = T.muted; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  for (let p = Math.ceil(lo / step) * step; p < hi; p += step) { const yy = y(p); if (yy > 8 && yy < plotH - 8) ctx.fillText(f(p), plotW + 6, yy); }
  const lc = C[n - 1], lcol = lc[4] >= lc[1] ? T.buy : T.sell, ly = y(lc[4]);
  if (ly > 0 && ly < plotH) {
    ctx.strokeStyle = alpha(lcol, 0.6); ctx.setLineDash([2, 3]); ctx.beginPath(); ctx.moveTo(0, Math.round(ly) + 0.5); ctx.lineTo(plotW, Math.round(ly) + 0.5); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = lcol; ctx.fillRect(plotW + 1, ly - 8, AX - 1, 16); ctx.fillStyle = '#fff'; ctx.fillText(f(lc[4]), plotW + 6, ly);
  }

  /* time axis (India time) */
  ctx.fillStyle = T.bg; ctx.fillRect(0, plotH, W, AXH);
  ctx.strokeStyle = T.line; ctx.beginPath(); ctx.moveTo(0, plotH + 0.5); ctx.lineTo(W, plotH + 0.5); ctx.stroke();
  const every = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 192].find((k) => k * barW >= 78) || 192;
  ctx.fillStyle = T.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  let prevDay = -1;
  const firstI = Math.max(0, Math.floor(start / every) * every - every);
  for (let i = firstI; i <= i1 + every; i += every) {
    if (i >= n) break; if (i < 0) continue;
    const xx = x(i); if (xx < 20 || xx > plotW - 20) continue;
    const d = new Date(TS[i] * 1000).getUTCDate();
    const daily = AN.interval === '1d';
    const txt = daily ? tDM(TS[i]) : (d !== prevDay && (prevDay !== -1 || tHM(TS[i]) === '00:00') ? tDM(TS[i]) : tHM(TS[i]));
    ctx.fillText(txt, xx, plotH + AXH / 2 + 1); prevDay = d;
    ctx.strokeStyle = alpha(T.line, 0.6); ctx.beginPath(); ctx.moveTo(Math.round(xx) + 0.5, plotH); ctx.lineTo(Math.round(xx) + 0.5, plotH + 4); ctx.stroke();
  }

  /* header + crosshair */
  let hov = n - 1;
  if (cross && cross.x >= 0 && cross.x <= plotW && cross.y >= 0 && cross.y <= plotH) {
    hov = clamp(Math.round(start + cross.x / barW - 0.5), 0, n - 1);
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, plotW, plotH); ctx.clip();
    ctx.strokeStyle = alpha(T.fg, 0.4); ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(x(hov)) + 0.5, 0); ctx.lineTo(Math.round(x(hov)) + 0.5, plotH); ctx.moveTo(0, Math.round(cross.y) + 0.5); ctx.lineTo(plotW, Math.round(cross.y) + 0.5); ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();
    const pv = hi - cross.y / plotH * (hi - lo);
    ctx.fillStyle = T.fg; ctx.fillRect(plotW + 1, cross.y - 8, AX - 1, 16); ctx.fillStyle = T.bg; ctx.font = T.mono; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(f(pv), plotW + 6, cross.y);
  }
  const hc = C[hov];
  const head1 = sym + ' · ' + MODE_LABEL[mode] + (mode !== 'daily' ? ' · ' + AN.session : '');
  const head2 = tDM(hc[0]) + ' ' + tHM(hc[0]) + ' IST   O ' + f(hc[1]) + '  H ' + f(hc[2]) + '  L ' + f(hc[3]) + '  C ' + f(hc[4]);
  ctx.font = '600 12px ' + T.ui.split(' ').slice(1).join(' '); ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  const w1 = Math.max(ctx.measureText(head1).width, 0);
  ctx.font = T.mono; const w2 = ctx.measureText(head2).width;
  const showOHLC = !narrow || cross;
  ctx.fillStyle = alpha(T.bg, 0.8); ctx.fillRect(4, 4, Math.max(w1, showOHLC ? w2 : 0) + 12, showOHLC ? 36 : 20);
  ctx.fillStyle = T.fg; ctx.font = '600 12px ' + T.ui.split(' ').slice(1).join(' '); ctx.fillText(head1, 10, 8);
  if (showOHLC) { ctx.fillStyle = T.muted; ctx.font = T.mono; ctx.fillText(head2, 10, 24); }
}

/* ---------- interaction ---------- */
const ptrs = new Map(); let drag = null, pinch = null, moved = false;
cv.addEventListener('pointerdown', (e) => {
  cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY }); moved = false;
  if (ptrs.size === 1) drag = { x: e.clientX, start };
  if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), barW, start, mid: (a.x + b.x) / 2 - cv.getBoundingClientRect().left }; drag = null; }
});
cv.addEventListener('pointermove', (e) => {
  const r = cv.getBoundingClientRect();
  if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pinch && ptrs.size === 2) {
    const [a, b] = [...ptrs.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
    const idxAt = pinch.start + pinch.mid / pinch.barW;
    barW = clamp(pinch.barW * d / pinch.d, 2, 40); start = idxAt - pinch.mid / barW; clampView(); moved = true;
  } else if (drag && ptrs.size === 1) {
    const dx = e.clientX - drag.x; if (Math.abs(dx) > 3) moved = true;
    start = drag.start - dx / barW; clampView();
  }
  if (e.pointerType === 'mouse') cross = { x: e.clientX - r.left, y: e.clientY - r.top };
  schedule();
});
const release = (e) => {
  if (!moved && e.pointerType === 'touch') { const r = cv.getBoundingClientRect(); cross = { x: e.clientX - r.left, y: e.clientY - r.top }; }
  ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null;
  drag = ptrs.size === 1 ? { x: [...ptrs.values()][0].x, start } : null; schedule();
};
cv.addEventListener('pointerup', release); cv.addEventListener('pointercancel', release);
cv.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') { cross = null; schedule(); } });
cv.addEventListener('wheel', (e) => {
  e.preventDefault();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { start += e.deltaX / barW; clampView(); schedule(); return; }
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, idxAt = start + px / barW;
  barW = clamp(barW * Math.exp(-e.deltaY * 0.0015), 2, 40); start = idxAt - px / barW; clampView(); schedule();
}, { passive: false });
const zoomBy = (k) => { const px = plotW / 2, idxAt = start + px / barW; barW = clamp(barW * k, 2, 40); start = idxAt - px / barW; clampView(); schedule(); };
$('#zin').addEventListener('click', () => zoomBy(1.3)); $('#zout').addEventListener('click', () => zoomBy(1 / 1.3));
$('#zfit').addEventListener('click', () => { fit(); schedule(); });

/* ---------- panels ---------- */
function setData() {
  const P = DATA.pairs.find((p) => p.symbol === sym);
  if (!P.modes[mode]) mode = ['scalp', 'swing', 'daily'].find((m) => P.modes[m]);
  M = P.modes[mode]; AN = M.an; C = M.candles; TS = C.map((c) => c[0]);
}
function renderPanels() {
  const an = AN, d = an.price_dec, f = (x) => Number(x).toFixed(d), s = an.signal;
  const P = DATA.pairs.find((p) => p.symbol === sym);
  $('#pairs').innerHTML = DATA.pairs.map((p) => `<button data-s="${esc(p.symbol)}" aria-pressed="${p.symbol === sym}">${esc(p.symbol)}</button>`).join('');
  $('#modes').innerHTML = Object.keys(MODE_LABEL).map((m) => `<button data-m="${m}" aria-pressed="${m === mode}" ${P.modes[m] ? '' : 'disabled'}>${MODE_LABEL[m]}</button>`).join('');
  $('#toggles').innerHTML = TOGGLES.filter((t) => !(mode !== 'scalp' && t[0] === 'sessions')).map(([k, label, tok]) =>
    `<button class="tg" data-k="${k}" aria-pressed="${on[k]}"><i style="background:var(${tok})"></i>${label}</button>`).join('');

  const pips = (a, b) => (Math.abs(a - b) / (d === 3 ? 0.01 : d === 2 ? 0.1 : 0.0001)).toFixed(1);
  if (s) {
    $('#signal').innerHTML = `
      <div class="sig-head"><span class="badge ${s.side === 'BUY' ? 'buy' : 'sell'}">${s.side}</span>
        <strong>${esc(an.symbol)}</strong><span class="pill">${esc(s.strategy)}</span>
        <span class="pill ${s.grade}">Grade ${s.grade} · ${s.score} of ${s.of} confirmations</span><span class="pill">${esc(s.status)}</span></div>
      <div class="levels4">
        <div class="lv"><div class="k">Entry</div><div class="v">${f(s.entry)}</div><div class="p">${esc(s.entry_type)}</div></div>
        <div class="lv sl"><div class="k">Stop loss</div><div class="v">${f(s.sl)}</div><div class="p">${s.risk_pips} pips</div></div>
        <div class="lv tp"><div class="k">TP1 (1R)</div><div class="v">${f(s.tp1)}</div><div class="p">${pips(s.tp1, s.entry)} pips</div></div>
        <div class="lv tp"><div class="k">TP2</div><div class="v">${f(s.tp2)}</div><div class="p">${pips(s.tp2, s.entry)} pips · RR ${Number(s.rr).toFixed(1)}</div></div>
      </div>
      <div><h2>Confirmations</h2><div class="checks">${s.checks.passed.map((x) => `<span class="ck ok">${esc(x)}</span>`).join('')}${s.checks.missing.map((x) => `<span class="ck no">${esc(x)}</span>`).join('')}</div></div>
      <div><h2>Why this trade</h2><ul class="why">${s.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul></div>
      <div class="note">${esc(s.manage)} ${esc(s.cancel)}.</div>`;
  } else {
    $('#signal').innerHTML = `
      <div class="sig-head"><span class="badge none">NO TRADE</span><strong>${esc(an.symbol)}</strong><span class="pill">${esc(an.interval)}</span></div>
      <div><h2>Wait for</h2><ul class="why">${(an.watch.length ? an.watch : ['No clean setup yet. Waiting for a sweep of a key level with displacement.']).map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>`;
  }
  const cl = (l) => (l === 'Bullish' ? 'up' : l === 'Bearish' ? 'dn' : 'flat');
  const sm = (k) => an.smas[k] == null ? '<span class="note">n/a</span>' : `<span class="num ${an.price > an.smas[k] ? 'up' : 'dn'}">${f(an.smas[k])}</span>`;
  $('#trend').innerHTML = `
    <div class="kv"><span>Price</span><span class="num">${f(an.price)}</span></div>
    <div class="kv"><span>${an.interval} trend</span><span class="${cl(an.trend.label)}">${an.trend.label}</span></div>
    <div class="kv"><span>Structure</span><span>${esc(an.trend.structure)}</span></div>
    ${an.htf ? `<div class="kv"><span>${esc(an.htf.name)} trend</span><span class="${cl(an.htf.label)}">${an.htf.label}</span></div>` : ''}
    <div class="kv"><span>SMA 50</span>${sm('50')}</div><div class="kv"><span>SMA 100</span>${sm('100')}</div><div class="kv"><span>SMA 200</span>${sm('200')}</div>
    ${an.range ? `<div class="kv"><span>Range</span><span class="num">${f(an.range.lo)} – ${f(an.range.hi)}</span></div><div class="kv"><span>Price is in</span><span class="${an.range.zone === 'discount' ? 'up' : 'dn'}">${an.range.zone} (EQ ${f(an.range.eq)})</span></div>` : ''}
    <div class="kv"><span>Session now</span><span>${esc(an.session)}</span></div>
    <div class="kv"><span>ATR</span><span class="num">${an.atr_pips} pips</span></div>`;
  $('#levels').innerHTML = `<table><thead><tr><th>Level</th><th style="text-align:right">Price</th><th style="text-align:right">Pips</th></tr></thead><tbody>${an.levels.filter((l) => l.kind !== 'handle').slice(0, 18).map((l) => `<tr><td>${esc(l.name)}</td><td class="n">${f(l.price)}</td><td class="n ${l.dist_pips > 0 ? 'dn' : 'up'}">${(l.dist_pips > 0 ? '+' : '') + l.dist_pips.toFixed(1)}</td></tr>`).join('')}</tbody></table>`;
  $('#pats').innerHTML = an.patterns.length ? an.patterns.map((p) => `<div class="pat"><b class="${p.side > 0 ? 'up' : p.side < 0 ? 'dn' : 'flat'}">${p.side > 0 ? 'Bullish' : p.side < 0 ? 'Bearish' : 'Neutral'}</b> ${esc(p.name)} <span class="pill">${esc(p.status)}</span><div class="note">${esc(p.note)}</div></div>`).join('') : '<div class="note">No patterns on the latest candles.</div>';
}

function render(refit) { setData(); renderPanels(); layout(); if (refit) fit(); else clampView(); readTheme(); save(); schedule(); }
$('#pairs').addEventListener('click', (e) => { const b = e.target.closest('button[data-s]'); if (b) { sym = b.dataset.s; render(true); } });
$('#modes').addEventListener('click', (e) => { const b = e.target.closest('button[data-m]'); if (b && !b.disabled) { mode = b.dataset.m; render(true); } });
$('#toggles').addEventListener('click', (e) => { const b = e.target.closest('button[data-k]'); if (b) { on[b.dataset.k] = !on[b.dataset.k]; b.setAttribute('aria-pressed', on[b.dataset.k]); save(); schedule(); } });
$('#demo').hidden = !DATA.demo;
$('#upd').textContent = 'Updated ' + new Date(DATA.generated).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) + ' IST';
try { new ResizeObserver(() => { layout(); clampView(); schedule(); }).observe($('#box')); } catch (e) { window.addEventListener('resize', () => { layout(); clampView(); schedule(); }); }
const retheme = () => { readTheme(); schedule(); };
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', retheme); } catch (e) {}
new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
try { document.fonts && document.fonts.ready.then(() => { readTheme(); schedule(); }); } catch (e) {}
render(true);
</script>
"""
