# Strategies, architecture and sources

This is the reference for what the engine does today. Nothing here claims a win rate: **no result is published until
you run `backtest-history` yourself** (see [Testing a strategy](#testing-a-strategy)).

## 1. How the engine is built

```
data (Yahoo / Dukascopy)
  -> fxbot/strategies/*   13 strategies, one common interface, one Signal object
  -> fxbot/regime.py      trend / range / volatile / transition label (ADX + ATR ratio)
  -> fxbot/confluence.py  counts independent FAMILIES, not indicators
  -> fxbot/risk.py        stop, RR, spread, volatility, session, size checks (never places orders)
  -> fxbot/workflow.py    1h plan -> 15m setup -> 5m execution (-> 1m optional precision)
  -> dashboard / Telegram / backtest
```

**Signal object** (every strategy returns the same fields): instrument, direction (BUY / SELL / NO TRADE), strategy,
timeframe, entry and entry zone, stop loss, take profits, risk-reward, confidence score, regime, timestamp,
invalidation, explanation and rejection reasons. A strategy that finds nothing returns nothing: there are no filler signals.

**Confidence** is a heuristic 0 to 100 score of how much independent evidence agrees. It is **not a probability of winning.**

**Families.** Strategies are grouped by the idea they trade (ICT / SMC, trend following, price action, breakout,
mean reversion, scalping, swing). Two breakout strategies agreeing count as one vote, so the same idea is never counted twice.

## 2. The multi-timeframe workflow

| Stage | Timeframe (configurable) | Job | Can it create a trade alone? |
|---|---|---|---|
| Plan | 1h | Bias: BUY / SELL / NEUTRAL / NO TRADE, key levels, regime | **No** |
| Setup | 15m | A strategy that agrees with the 1h bias finds a setup | No (needs the 5m) |
| Execution | 5m | A confirming candle closes; the stop may be tightened to the last 5m swing; chase check | Only with a valid 15m setup |
| Precision | 1m (off by default) | Optional entry refinement | **No** |

Then the risk check decides the final answer. **WAIT or NO TRADE is a normal result** and is shown as such.
Settings live in `config.json` (see `config.example.json`).

## 3. Strategies implemented

Source column: **Book** = "9 Advanced and Profitable Trading Strategies" (the PDF you supplied, called *PTS* below);
**IFC** = the IFC Markets "Forex Trading Strategies" PDF (an overview with no numeric rules, so nothing is copied from it);
**Public** = a widely documented technique; the exact parameters are this project's choice and are marked as such.

| Key | Family | Idea | Entry | Stop / target | Source |
|---|---|---|---|---|---|
| `ict` | ICT / SMC | Liquidity sweep, displacement, fair value gap | Limit at the FVG midpoint after a sweep and structure shift | Stop beyond the sweep + buffer, fixed RR (2 on 15m, 3 on 4H) | Public (ICT concepts, project's own parameters) |
| `trendpull` | Trend | Pullback inside a trend | Price above the 200 EMA with the 50 EMA rising and above it, a touch of the 21 EMA within the last 4 bars, then a candle closing above the previous high | Beyond the last 5 bars' low (min 0.8 ATR), RR 2 (15m) or 3 (4H) | Public |
| `emacross` | Trend | EMA 20 / 60 cross | Cross in the direction of the 100 EMA, ADX >= 20 | 2 ATR, RR 2 | Book (PTS S2); stop, target and filters added by this project |
| `donchian` | Breakout | N-bar channel breakout | Close beyond the N-bar extreme, 100 EMA on the side, not a dead-flat range | 2 ATR, RR 2 | Public (Turtle-style); parameters are ours |
| `retest` | Breakout | Break, pull back, role reversal | Close back in the breakout direction after a retest within 0.3 ATR | Beyond the retest extreme, RR 2 | Book (PTS S6, simplified); the 3-tranche scale-in is not used |
| `squeeze` | Breakout | Bollinger squeeze | Close outside the band after a squeeze (BandWidth in the lowest 20% of 120 bars) | Previous candle's far side (min 0.8 ATR), RR 2 | Book (PTS S7); the book's fixed 0.0100 threshold only suits EURUSD H4, so a relative rule is used |
| `nr7` | Breakout | Narrowest of 7 candles | Close beyond the NR7 candle, in the 50 EMA slope direction | Opposite side of the NR7 candle, RR 2 | Book (PTS S8); market entry at the close instead of a stop order |
| `setforget` | Price action | Daily pin bar or engulfing at a swing level | Pin bar: limit at the midpoint; engulfing: close | Beyond the wick, RR 2, then leave it | Book (PTS S5 / S6 in spirit); the exact rules of the "Set & Forget" system by name are **not** known and this is an interpretation |
| `failbreak` | Price action | Fade a failed breakout | Close back inside the range within 3 bars, ADX < 25 | Beyond the failed extreme, RR 2 | Public |
| `rsi2` | Mean reversion | RSI(2) extremes | With the 200 EMA: buy RSI(2) < 10, sell > 90 | 2 ATR, RR 1.2 | Public (reversion). The Book (PTS S9) reads RSI > 90 as a **buy**; the opposite. Both are supported via `direction`, default is reversion. Neither is validated |
| `bbrevert` | Mean reversion | Range fade with Bollinger (20, 2) | Only when ADX < 20: poke outside the band, close back inside, RSI(14) < 40 / > 60 | 0.3 ATR beyond the extreme, target the middle band (at least 1R) | Public |
| `sessionbreak` | Scalping | Asian range breakout in London / NY AM | First 15m close beyond the range in a killzone, not stretched | Middle of the range, RR 1.5, one per day and side | Public (ICT-style session ranges) |
| `hastoch` | Swing | Heikin-Ashi turn + stochastic (14, 7, 3) | Two opposite HA candles after a run of 3 or more; %K <= 40 for buys, >= 60 for sells | Beyond the last 5 bars, RR 2 | Book (PTS S3) |

### Where the sources disagreed or were vague (and what we did)

| Problem | Decision |
|---|---|
| PTS S3 p14 vs p15 give opposite stochastic zones for Heikin-Ashi entries | Used the sensible reading (buys from the lower zone, sells from the upper) |
| PTS S9 treats RSI(2) > 90 as a buy; the common usage is the reverse | Both supported, reversion is the default, neither is validated |
| PTS S2 gives no stop, target or ranging filter | ATR stop, fixed RR and ADX >= 20 added, labelled as ours |
| PTS S3 uses a fixed 100-pip stop and break-even at +50 pips | Not used: a pip means different things on a JPY pair and on gold. ATR / structure stops instead |
| PTS S7 uses BandWidth 0.0100 (EURUSD H4 only) | Relative "lowest 20% of recent history" rule |
| PTS risk rules (3% of capital and a per-point cap) cannot both hold with a 100-pip stop | Ignored; `config.json` risk settings apply |
| IFC PDF has no numeric rules | Used only for ideas (trend / range / scalping / swing categories), nothing copied |
| Scribd links | Not readable from the build environment (login wall). **Nothing from them was used.** Paste their text into an issue if you want it considered |

## 4. Researched, not implemented

| Idea | Why it was left out |
|---|---|
| PTS S1 Momentum reversal (COT + fundamentals + weekly stochastic) | Needs a fundamental bias and a "reversal pattern" the book never defines |
| PTS S4 Correction breakout | "Correction" is only shown by example; no rule to code without guessing |
| PTS S5 hammer / long shadow | Hammer ratio not given; `setforget` already covers pin bars |
| Daily pivot points (IFC p20) | Formulas are precise, but the pivot-fade and the pivot-breakout versions are contradictory and have no stops. Candidate for a future level layer |
| Carry trade, hedging, basket / pair trading, buy and hold | Not signal strategies (long-term, portfolio or fundamental) |
| Volume-based setups | Spot forex has no central volume; Yahoo / Dukascopy volume is tick count at best |
| Full ICT models (Silver Bullet, breaker blocks, order-flow) | Rules vary a lot between teachers; listed on the roadmap for someone with exact rules |

## 5. Rules that apply to every signal

- **Regime.** Trend strategies skip ranges; range strategies skip trends; most skip volatile markets.
- **Risk.** Stop distance within 0.4 to 4 ATR, reward-to-risk at least 1.5 (1.0 for mean reversion), spread at most 15% of the stop, volatility between 0.35x and 2.5x normal, optional session filter, de-duplication of the same idea.
- **No order execution.** The project only analyses and alerts. Lot size is a suggestion from your `config.json` account settings; crosses are approximate, check your broker's calculator.
- **News.** High-impact events for the pair add a warning, they do not silently change the signal.

## 6. Limitations (read these)

- Strategy logic is documented and tested for *behaviour* (no look-ahead, risk rules, de-duplication). Its *profitability* is **unknown** until you test it.
- Free data (Yahoo) is delayed and limited; Dukascopy history is better but only available inside the Action.
- A same-candle stop and target hit is counted as a **loss** (conservative).
- Spread, slippage and commission are modelled with typical values and will differ from your broker.
- Past results do not predict future results. This is not financial advice.

## Testing a strategy

1. **Actions > backtest-history > Run workflow.** Pick a pair, a style and years.
2. The run summary shows development and evaluation results separately (the last 30% of history is never used for tuning), with costs included.
3. Look for 100+ trades and a positive average R in the **evaluation** part. Anything less is noise.

Command line: `python -m fxbot backtest --symbol EURUSD --strategy donchian --mode daily --split 0.7`
