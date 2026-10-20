fxbot - free forex signal bot

Looks at forex pairs on three views (Scalp 15m, Swing 4H, Daily 1D), finds ICT and Set & Forget setups,
and sends you a signal with entry, stop loss, take profit and the reasons, on Telegram and on a web dashboard.
Everything here is free. No coding is needed to run it: you use buttons on GitHub.

## What it looks at
- **Strategies:** ICT (liquidity sweep -> displacement -> fair value gap entry), a trend-pullback strategy, and a daily
  Set & Forget (pin bar / engulfing candle at a swing level, order placed then left alone).
- **On the chart:** fair value gaps and order blocks (drawn from where they formed), previous day / week highs and lows,
  Asia range, London and New York session highs and lows, daily / midnight / weekly opens, round numbers, premium / discount
  midpoint, 50 / 100 / 200 SMAs, chart patterns, killzone shading.
- **A signal only appears** when enough checks agree (trend, higher timeframe, moving averages, killzone, key level,
  FVG / order block, pattern, premium / discount, reward-to-risk) and never against the trend.

## Set up with clicks only
The code is already in this repo. You only need to switch things on:
1. Repo **Settings > Pages > Build and deployment > Source: GitHub Actions**.
2. Repo **Actions** tab. If GitHub asks, press **I understand my workflows, go ahead and enable them**.
3. **Actions > dashboard > Run workflow**. After about 2 minutes your page is at
   `https://luciferalive.github.io/FX-Signal-Desk/` (open it on your phone and add it to your home screen).
4. Set up Telegram (below), then **Actions > telegram-test > Run workflow**. A message should arrive.
5. **Actions > scan** runs by itself every 15 minutes on weekdays. Press **Run workflow** once to test it now.

### Telegram in 5 steps
1. In Telegram search **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`. Copy the token it gives you.
2. Open your new bot and press **Start**, then send it the word `hi`.
3. In a browser open `https://api.telegram.org/bot` + your token + `/getUpdates`. Find `"chat":{"id":` and copy the number after it.
4. GitHub repo **Settings > Secrets and variables > Actions > New repository secret**. Add `TELEGRAM_BOT_TOKEN` (the token)
   and `TELEGRAM_CHAT_ID` (the number). Names must match exactly.
5. Run **telegram-test** (Actions tab). Read the log: it says SUCCESS or tells you what to fix.

## Choose your pairs
Edit `pairs.txt` on GitHub (open it, press the pencil icon, change the list, Commit). One pair per line, for example
`EURUSD`, `GBPJPY`, `XAUUSD`. You do not need to supply any chart data: the bot downloads prices itself.

## Check how well it works
**Actions > validate > Run workflow**, choose a pair and a style. It replays the live rules over past prices and prints
win rate, average R, profit factor and drawdown by grade. Look for 100+ trades and a positive average R after costs.
Free Yahoo data is limited (about 60 days for 15m, 2 years for 4H / daily), so small samples are normal. For more history
upload a CSV (Dukascopy export) and run the command line version, see below.

## Command line (optional)
```bash
pip install -r requirements.txt
python -m fxbot analyze --symbol EURUSD --mode scalp
python -m fxbot dashboard
python -m fxbot validate --symbol EURUSD --mode swing
python -m fxbot validate --symbol EURUSD --mode scalp --csv eurusd_15m.csv
python -m fxbot backtest --symbol EURUSD --strategy ict --mode swing
python -m fxbot demo        # offline, synthetic data (layout / pipeline check only)
```

## Good to know
- GitHub pauses scheduled workflows in a public repo after about 60 days with no activity. Press **Run workflow** on `scan`
  now and then, or make a small edit, to keep it alive.
- Yahoo sometimes refuses requests from cloud servers. The bot retries; if a run shows "No data", the next run usually works.
- The Pages dashboard of a Public repo is public. No tokens are stored in the code.
- TradingView cannot be scraped and its webhooks need a paid plan. `pine/ict_sessions.pine` works on a free account for
  killzones and sweeps. `python -m fxbot webhook` receives TradingView alerts if you ever upgrade.
- Backtests are not predictions. Paper trade first. Not financial advice.
