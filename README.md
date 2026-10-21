<div align="center">

<img src="assets/banner.png" alt="FX Signal Desk" width="100%">

<br>

[![CI](https://github.com/luciferalive/FX-Signal-Desk/actions/workflows/ci.yml/badge.svg)](https://github.com/luciferalive/FX-Signal-Desk/actions/workflows/ci.yml)
[![Live dashboard](https://img.shields.io/badge/dashboard-live-2ecc9a?logo=githubpages&logoColor=white)](https://luciferalive.github.io/FX-Signal-Desk/)
![Python](https://img.shields.io/badge/python-3.12-3776ab?logo=python&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Cost](https://img.shields.io/badge/cost-%240%20forever-brightgreen)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg)](CONTRIBUTING.md)
<br>
[![Stars](https://img.shields.io/github/stars/luciferalive/FX-Signal-Desk?style=social)](https://github.com/luciferalive/FX-Signal-Desk/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/luciferalive/FX-Signal-Desk?color=8a7bff)](https://github.com/luciferalive/FX-Signal-Desk/commits/main)
[![Issues](https://img.shields.io/github/issues/luciferalive/FX-Signal-Desk?color=f5b942)](https://github.com/luciferalive/FX-Signal-Desk/issues)
![Telegram](https://img.shields.io/badge/alerts-Telegram-26a5e4?logo=telegram&logoColor=white)
![TradingView](https://img.shields.io/badge/indicator-TradingView-131722?logo=tradingview&logoColor=white)
![Pairs](https://img.shields.io/badge/pairs-30-2ecc9a)
![Timeframes](https://img.shields.io/badge/timeframes-1m%E2%86%921M-2ecc9a)

**A free, open-source forex signal desk. Every signal comes with entry, stop loss, take profit and the reasons behind it.**<br>
ICT and trend setups, key levels, fair value gaps, killzones, SMAs, news colours and Telegram alerts. No server, no subscription.

[**Open the dashboard**](https://luciferalive.github.io/FX-Signal-Desk/) · [**Quick start**](#-quick-start) · [**Contribute**](#-help-build-this) · [**Roadmap**](#-roadmap)

</div>

---

## Why FX Signal Desk?

| | Typical signal group | Paid TradingView scripts | **FX Signal Desk** |
|---|---|---|---|
| Price | Monthly fee | Monthly fee | **Free forever** |
| Shows the reasons | Rarely | Sometimes | **Always, every signal** |
| Checks the news calendar | No | No | **Yes, with warnings** |
| You can test it on history | No | Sometimes | **Yes, one click** |
| Open source, you can change it | No | No | **Yes, MIT** |
| Runs while your PC is off | Yes | Needs TradingView open | **Yes, on GitHub** |

## What you get

<table>
<tr>
<td width="50%"><img src="assets/chart-dark.png" alt="Dark chart with levels, FVGs and a trade"></td>
<td width="50%"><img src="assets/chart-light.png" alt="Light chart"></td>
</tr>
<tr>
<td align="center"><sub>Dark mode: killzones, FVGs, daily / weekly levels, SMA 50 / 100 / 200 and the trade, all drawn on the chart</sub></td>
<td align="center"><sub>Light mode. One button switches Auto / Light / Dark</sub></td>
</tr>
</table>

> **Heads up:** screenshots use **made-up demo prices and sample news** so they never go stale. The live dashboard shows real data.

<table>
<tr>
<td width="33%"><img src="assets/signal-card.png" alt="Signal card"></td>
<td width="33%"><img src="assets/news-dark.png" alt="News panel"></td>
<td width="33%"><img src="assets/telegram-alert.png" alt="Telegram alert"></td>
</tr>
<tr>
<td align="center"><sub>Signal card: grade, entry, SL, TP and reasons</sub></td>
<td align="center"><sub>News for the selected pair: red high, yellow medium, green low</sub></td>
<td align="center"><sub>The same signal as a Telegram message</sub></td>
</tr>
</table>

<div align="center"><img src="assets/phone.png" alt="Works on a phone" height="420"><br><sub>Works on a phone. Add it to your home screen.</sub></div>

## A day with the desk

1. **Weekdays, every ~15 minutes:** GitHub downloads fresh prices and the news calendar.
2. **The engine** maps levels, gaps, killzones and trend, then counts how many of 9 checks agree.
3. **Only strong setups get through.** You get a Telegram message with entry, SL, TP and the reasons.
4. **You open the dashboard** to see the same trade drawn on the chart, with the news colour for that pair.
5. **Want proof?** Press one button to replay the rules over years of real history.

## Features

- **30 instruments:** all 28 major, minor and cross pairs, plus gold (XAUUSD) and silver (XAGUSD). Edit `pairs.txt` to change the list.
- **9 timeframes:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 1M.
- **Everything on the chart:** fair value gaps, order blocks, premium / discount, today / previous day / previous week highs and lows, Asia and session ranges, daily / midnight / weekly opens, round-number handles, SMA 50 / 100 / 200.
- **Killzones in New York time:** London, NY AM, NY PM.
- **Multi-confirmation signals:** a trade needs enough of 9 checks to agree and never goes against the trend. Each signal is graded A to D.
- **Fundamental news panel:** Forex Factory calendar filtered to the pair, coloured by impact, with a warning before high-impact releases.
- **Live chart tab:** the free TradingView widget for real-time prices.
- **Telegram alerts** with de-duplication, so you do not get the same signal twice.
- **TradingView indicator** (`pine/fx_signal_desk.pine`) for true live signals inside TradingView.
- **One-click backtest on real history:** downloads up to 3 years from Dukascopy and replays the rules with no look-ahead, win rate and average R by grade.
- **Light and dark themes.**
- **Free hosting:** GitHub Pages and GitHub Actions. Nothing to pay for.

## Automations (all run on GitHub, all free)

| Workflow | What it does | When |
|---|---|---|
| `scan` | Looks for setups and sends Telegram alerts | Every ~15 min, Mon-Fri |
| `dashboard` | Rebuilds and publishes the web dashboard | Every ~15 min, Mon-Fri |
| `telegram-test` | Sends a test message to check your setup | Button |
| `validate` | Quick replay on recent Yahoo data | Button |
| `backtest-history` | Downloads years of Dukascopy data and replays the rules | Button |
| `ci` | Runs the tests on every change | On every push |

## How it works

```mermaid
flowchart LR
    Y[Yahoo Finance prices] --> A
    F[Forex Factory calendar] --> A
    subgraph GH[GitHub Actions - every ~15 min]
        A[Analysis engine<br/>levels, FVG, SMAs, patterns] --> S[Strategies + confirmation gate]
    end
    S --> D[GitHub Pages dashboard]
    S --> T[Telegram alert]
    TV[TradingView] -. live price tab .-> D
    P[Pine indicator] -. live signals .-> TV
```

## Quick start

### Path 1: the dashboard (clicks only)
1. Repo **Settings > Pages > Build and deployment > Source: GitHub Actions**.
2. Open the **Actions** tab. If asked, press **I understand my workflows, go ahead and enable them**.
3. **Actions > dashboard > Run workflow**. After about 2 minutes your page is live at
   `https://luciferalive.github.io/FX-Signal-Desk/`. On a phone, add it to your home screen.
4. It refreshes by itself about every 15 minutes on weekdays (GitHub can run it a little late).

### Path 2: Telegram alerts
1. In Telegram search **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`. Copy the token.
2. Open your new bot, press **Start**, and send `hi`.
3. In a browser open `https://api.telegram.org/bot` + your token + `/getUpdates`. Copy the number after `"chat":{"id":`.
4. Repo **Settings > Secrets and variables > Actions > New repository secret**. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Names must match exactly.
5. **Actions > telegram-test > Run workflow**. A message should arrive. After that, **scan** runs by itself every 15 minutes on weekdays.

### Path 3: test it on real history
1. **Actions > backtest-history > Run workflow**.
2. Choose a pair (for example `EURUSD`), a style (`swing`, `scalp`, `daily`) and years (`2`).
3. Wait 10 to 40 minutes, then open the run. The report is on the summary page: trades, win rate, average R, profit factor and drawdown for each grade.
4. Look for 100+ trades and a positive average R after costs. Grades A and B should beat C and D, otherwise the filter is not helping.

### Path 4: live signals inside TradingView
1. Open a chart on TradingView and the **Pine Editor** tab at the bottom.
2. Paste the contents of [`pine/fx_signal_desk.pine`](pine/fx_signal_desk.pine) and press **Add to chart**.
3. For alerts: **Add alert**, choose **FX Signal Desk**, condition **Any alert() function call**.
4. TradingView has its own Economic Calendar for news.

## Strategies

| Strategy | Idea | Where |
|---|---|---|
| **ICT sweep + FVG** | Liquidity sweep, displacement with market structure shift, limit entry at the fair value gap midpoint | Scalp 15m, Swing 4H |
| **Trend pullback** | Trade with the higher-timeframe trend after a pullback to a level or moving average | Scalp, Swing |
| **Daily Set & Forget** | Pin bar or engulfing candle at a swing level on the daily chart, wide stop, fixed target | Daily |

> The Set & Forget rules here are **my interpretation**, not an official copy of anyone's system. Know the exact rules? [Open a strategy idea.](https://github.com/luciferalive/FX-Signal-Desk/issues/new?template=strategy_idea.md)

**The 9 confirmations:** trend, higher timeframe, SMAs, killzone, key level, FVG or order block, sweep or candle pattern, reward-to-risk of at least 2, premium / discount.

| Timeframe | Signals | Analysis |
|---|---|---|
| 15m, 4H, 1D | Yes | Yes |
| 1m, 5m, 30m, 1h, 1W, 1M | No | Yes (levels, zones, SMAs, patterns, trend) |

## Other commands

```bash
pip install -r requirements.txt
python -m fxbot analyze --symbol EURUSD --mode scalp
python -m fxbot dashboard --demo --out demo       # offline demo with made-up prices
python -m fxbot validate --symbol EURUSD --mode swing
python -m fxbot validate --symbol EURUSD --mode scalp --csv eurusd_15m.csv
python -m fxbot backtest --symbol EURUSD --strategy ict --mode swing
python -m pytest -q
```

Free Yahoo data is limited (about 60 days of 15m, 2 years of 4H / daily). For longer tests use the `backtest-history` button, or a Dukascopy CSV export.

## Project layout

```
fxbot/            analysis, zones, strategies, news, scanner, dashboard, backtest
fxbot/templates/  the dashboard page
pine/             TradingView indicator
tests/            pytest suite
pairs.txt         your instruments
.github/workflows CI, scan, dashboard, telegram-test, validate, backtest-history
assets/           README images
```

## Roadmap

Good first contributions:

- [ ] Exact Alex Set & Forget rules (needs someone who knows them)
- [ ] More ICT models: Silver Bullet, breaker blocks, mitigation blocks
- [ ] Order blocks in the Pine indicator
- [ ] Discord and email alerts
- [x] Longer history through Dukascopy data (`backtest-history`)
- [ ] Signal history page and performance tracker
- [ ] More tests and a bigger replay suite
- [ ] Translations
- [ ] Publish backtest results for each pair in the repo (honest numbers, committed with the code)
- [ ] Prop-firm risk rules (daily loss limit, max drawdown) in alerts

## Help build this

Contributions are very welcome, whether you code or not: strategy rules, bug reports, screenshots, docs, tests.
Read [CONTRIBUTING.md](CONTRIBUTING.md), pick something from the roadmap, and open an issue or pull request.
If it is useful to you, a star helps others find it.

**Not a coder? You can still help:**
- Send exact strategy rules you trade (use the *Strategy idea* template).
- Test it on your favourite pair and report what looked wrong.
- Share screenshots of setups that worked or failed, honestly.

[![Contributors](https://contrib.rocks/image?repo=luciferalive/FX-Signal-Desk)](https://github.com/luciferalive/FX-Signal-Desk/graphs/contributors)

## Disclaimer

This is an educational tool, **not financial advice**. No result is promised: there are no win-rate claims here, and
a signal is a starting point, not a guarantee. Run `validate`, paper trade first, and never risk money you cannot afford to lose.
Free data can be delayed or wrong. Never share your Telegram token.

## License

[MIT](LICENSE)
