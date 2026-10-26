# News trading playbook (information only)

Big scheduled news (Non-Farm Payrolls, CPI, interest-rate decisions, GDP, retail sales...) can move a pair by many pips in
seconds. The playbook gives you a **clear BUY, SELL or WAIT for EURUSD and gold (XAUUSD)** and the reasons behind it.
**It never places news trades.** There is no automatic news trading in this project: you decide and you click.

## What you see (dashboard section "News trading playbook", and `python -m fxbot news`)

For each instrument (EURUSD and XAUUSD by default):

1. **A lean: BUY, SELL or WAIT**, with a Low or Medium confidence label and a score from -100 to +100. The score is the sum of the points below, and it needs 25 points either way to leave WAIT. Confidence is never "High" because nothing here is certain.
2. **Why**, as a list of points:

| Evidence | Points | Where it comes from |
|---|---|---|
| 1h trend (up / down) | +-30 | the existing 1h plan of the signal engine |
| Signal engine has a BUY / SELL | +-8 to 20 | grade A 20, B 15, C 8 |
| Currency strength (EUR vs USD; for gold, the USD alone) | up to +-15 | 24-hour moves of all pairs |
| 24-hour move of the instrument | up to +-10 | price data |
| A big event due in 24 h: forecast above / below last time | +-5 each | the free Forex Factory calendar |
| An event already released with its real number | +-40 | only when the calendar feed carries the number |
| Headline mood (headlines naming the instrument that say it rises / falls) | up to +-10 | free RSS feeds (FXStreet, ForexLive) |

3. **WAIT is forced around big news:** from 30 minutes before to 15 minutes after a high-impact event for the pair's currencies
   the lean is WAIT ("do not enter before the number" / "wait for the first candle to close"), and the card tells you what the points would have said.
4. **The news that moves the pair**, each with what it measures, forecast and previous, and the IF-THEN row for that pair
   (number above the forecast -> BUY or SELL, below -> the opposite). Example: Non-Farm Payrolls above forecast -> stronger USD -> SELL EURUSD, SELL gold.
5. **Type in the real number** after the release (for example `210K`) and the card shows the direction and whether it agrees with the lean.
   For speeches there are Hawkish / Dovish buttons. Unemployment and jobless claims are handled the other way round (a higher number is bad for the currency).
6. **What happened last time** for released events: the first 15-minute candle (up or down, in pips) and the move after 30 minutes and 1 hour.
7. **Latest headlines** for the instrument.

Command line:

```bash
python -m fxbot news                         # EURUSD and gold: lean, reasons, upcoming news, headlines (live data)
python -m fxbot news --symbols EURUSD        # one instrument
python -m fxbot news --event Non-Farm --actual 210K     # direction once the number is out
python -m fxbot news --demo                  # made-up sample data, works offline
```

(The command line has no all-pairs view, so it leaves out the currency-strength points. The dashboard includes them.)

## Settings (`config.json`)

```json
{ "news_trading": { "enabled": true, "symbols": ["EURUSD", "XAUUSD"], "hours_ahead": 96, "hours_back": 30 } }
```

Set `"enabled": false` to remove the section from the dashboard. On the page you can also untick **Show this section**.

## Honest limits

- This is a **lean from data, not a prediction**. Nobody knows a number before it is released, and the evidence above is mostly trend and momentum, which is already in the price. The score has **not been backtested**, so its points are reasoned choices, not measured edges.
- "Better than forecast = stronger currency" is a rule of thumb. It fails often: the market may have priced it in, the size of the surprise matters, revisions matter, and the first spike often reverses.
- Headlines are scored with simple word rules (for example "EUR/USD rises"), with a small weight. They can be wrong, and the demo uses clearly fake sample headlines.
- If the free calendar or the RSS feeds cannot be reached, those parts are simply left out (the dashboard builds anyway). The calendar feed may not carry the real number, so you can type it in.
- Spreads widen and stops slip around news, so the risk you planned is not guaranteed. Many traders wait for the first candle to close, skip the 5 minutes before the release, and use a smaller size.
- The automated trading layer (`guides/TRADING.md`) blocks new entries around high-impact news by default and does not use this playbook.
- Nothing here is financial advice.
