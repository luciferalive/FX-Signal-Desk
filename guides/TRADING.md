# Automated trading (optional, DEMO first)

This layer connects the signal engine to a broker account, sizes trades from your real equity, applies a central risk
manager and manages each trade from entry to exit. **It is off unless you run it, it starts on a DEMO account, and
nothing here is proven profitable.** The strategies were never shown to make money; this layer only makes the
*execution* disciplined.

## What is implemented, and how well it is tested

| Piece | Status |
|---|---|
| Broker interface (account, margin, positions, orders, symbol specs, quotes, order/modify/cancel/partial close, reconciliation) | Implemented |
| **Simulated broker** (`sim`): full account with margin, stops, commission, slippage, failure injection | Implemented, fully tested |
| **XM through MetaTrader 5** (`mt5`, alias `xm`) | Implemented, tested **only against a fake MT5 module**. Not run against a real XM terminal. Try it on an XM **demo** account first |
| OANDA v20 (`oanda`) | Implemented, tested **only against mock HTTP responses**. Not run against a real OANDA account |
| Central risk manager, equity-based sizing, trade arbitration | Implemented, tested |
| TP1 / TP2 / TP3 partial closes, optional breakeven and trailing, time / session / rollover / news exits | Implemented, tested |
| Order state machine, timeout handling, restart recovery, reconciliation | Implemented, tested on the simulated broker and the fakes |
| Daily / weekly loss lock, pause, STOP ALL, emergency liquidation | Implemented, tested |
| Local control panel, audit log, trade history | Implemented, tested |
| MetaTrader 4 | **Not supported** (no official Python interface). You need an XM **MT5** account |
| Direct XM web API | **Does not exist** for retail accounts. XM trades through MetaTrader |

## How it fits together

```
signals (existing engine: 1h plan > 15m setup > 5m entry, or swing / daily analysis)
   -> arbitration     one idea = one proposal; BUY vs SELL conflicts are rejected by default
   -> RISK MANAGER    the only approver. Strategies can never reach the broker
   -> order manager   writes the order to saved state BEFORE sending; confirms from the broker's reply
   -> broker          sim | mt5 (XM) | oanda
   -> trade manager   partial closes, stops, exits, every tick
```

Every decision (signal, market conditions, account state, the risk numbers, approve or the exact rejection reasons,
the order request, the broker's reply, partial closes, stop changes, the final exit and the P&L) is one line in
`.state/audit_<profile>.jsonl`. `python -m fxbot trade history` and the panel show it in plain words.

## Connect an XM DEMO account (exact steps)

You need a **Windows computer** that stays on while the bot runs. The MetaTrader 5 terminal must be installed, open and
logged in, because that is how XM accounts are reached from Python.

1. Open your XM account area and create a **Demo account on MetaTrader 5** (not MT4). Note the login number, password and server name.
2. Install **MetaTrader 5** from XM (download link in your XM member area) and open it.
3. In MetaTrader: **File > Login to Trade Account**, enter the demo login, password and server. The account must show in the **Navigator** panel.
4. Turn on automated trading: click the **Algo Trading** button on the toolbar (it turns green), and check **Tools > Options > Expert Advisors > Allow algorithmic trading**.
5. Open **Market Watch** and look at how XM spells the pairs (for example `GBPJPY`). If every name has an extra ending (for example `GBPJPYm` or `GBPJPY.`), remember that ending: it is your **suffix**.
6. Install Python 3.12 from python.org (tick "Add Python to PATH"). Open **Command Prompt** in the project folder and run:
   `pip install -r requirements.txt MetaTrader5`
7. Create a file called `trading.json` in the project folder (copy `trading.example.json`). For XM:
   ```json
   { "broker": { "name": "xm", "mt5": { "suffix": "", "server_utc_offset_hours": null } } }
   ```
   Put your suffix in `"suffix"` if you have one. If the bot later says it cannot work out the server time, set `server_utc_offset_hours` to the number of hours the XM server time is ahead of UTC (XM is normally 2 in winter and 3 in summer).
   If you use an XM account type that charges commission, set `"commission_per_lot_round_turn"` to the round-turn amount per lot so sizing includes it.
8. Test the connection: `python -m fxbot trade status`. You must see `DEMO ACCOUNT broker=mt5 ...` with your real balance. If it says DISCONNECTED, the message says why (terminal closed, not logged in, package missing).
9. Run the bot on the demo: `python -m fxbot trade run`. Leave the window open.
10. In a second Command Prompt window: `python -m fxbot trade panel`, then open http://127.0.0.1:8765 in your browser.
11. Optional phone alerts: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as Windows environment variables; the bot messages you when it opens, partially closes and closes trades and when the risk lock trips.

Safe-by-default facts: the bot never sees your XM password (it uses the account already logged in inside the terminal);
the demo profile **refuses to run on a live account**; the live profile refuses a demo account.

## Controls

Panel buttons or commands (all take effect on the running bot's next cycle, about 30 seconds):

| Control | Command | What it does |
|---|---|---|
| Pause / resume entries | `trade pause` / `trade resume` | No new entries; open trades keep being managed |
| **STOP ALL TRADING** | `trade stop` (`trade start` re-enables) | No new signals, pending entry orders cancelled, no automatic management. Broker-side stops keep protecting trades and a missing stop is still restored |
| Close one / all | `trade close --position ID` / `trade close-all` | Closes at market |
| **Emergency liquidation** | `trade liquidate` | Separate from STOP: closes everything, cancels all orders, stops trading |
| Unlock | `trade unlock` | Clears a risk lock by hand (it locks again at once if the loss is still over the limit) |

On a live account every destructive action needs a typed `CLOSE` confirmation.

## Position sizing

Volume comes from your **real equity** and the broker's own instrument data (tick size, tick value in your account
currency, contract size, volume min / max / step):

`lots = (equity x risk%) / (loss per lot at the stop)` where the loss per lot includes the stop distance measured from
the price the order will really fill at (so the spread is inside it), expected slippage and commission.
The volume is rounded **down** to the broker's step. If even the minimum volume would risk more than allowed, the trade
is **rejected**; the risk is never raised silently to fit a minimum.

## Risk limits (defaults, all configurable, none proven optimal)

| Setting | DEMO | LIVE (stricter, set separately) |
|---|---|---|
| Risk per trade | 0.25% of equity | 0.10% |
| Maximum risk of one trade | 0.5% | 0.25% |
| Maximum total open risk (positions **and** pending orders, manual trades included) | 1.0% | 0.5% |
| Maximum daily loss (from start-of-day equity, realized + unrealized + costs) | 2.0% | 1.0% |
| Maximum weekly loss | 4.0% | 2.0% |
| Simultaneous positions / per symbol / per strategy+symbol | 3 / 1 / 1 | 1 / 1 / 1 |
| Entries per symbol per day | 3 | 1 |

Also checked on every trade: stop distance and side, take-profit side, reward-to-risk, stop in ATR terms, spread against the
stop and against the pair's typical spread, volatility, stale quote, signal age, "not chasing", margin (the broker
must be able to confirm it, otherwise the trade is rejected), currency exposure, correlated pairs, cooldown after a
close, duplicate signals and repeats of the same setup, sessions, rollover, weekend and news.
DEMO and LIVE are two separate profiles: LIVE never inherits demo values.

**Remaining risk.** A position's risk is counted from its **current** stop. A breakeven or in-profit stop never counts as
zero: the cost of getting out (slippage allowance and commission) stays in. A position or order with no stop counts as
1% of equity.

## Daily loss lock

When the daily (or weekly) limit is reached the bot: rejects all new entries, cancels pending entry orders, enters a
**persistent** lock (it survives restarts), tells you, keeps monitoring open trades and applies your policy
`daily_lock.existing_positions`: `keep` (default), `close_losers` or `close_all`. A daily lock ends when the next trading
day starts (17:00 New York, in the configured broker time zone). Do not deposit or withdraw while the bot runs: equity
changes would count as profit or loss.

## Multiple take-profit targets and stops

A strategy can bring one, two or three targets. Defaults: TP1 closes 40% of the **original** volume, TP2 closes 30%, the
last target closes the rest (with two targets: 40% then the rest). Partials are rounded down to the broker's step; a
partial smaller than the minimum is **skipped**, and one that would leave less than the minimum is reduced or skipped.
Only the last target (or a stop or a manual action) can close the whole position. A small account therefore may only
get a single target, on purpose.

- **Breakeven** is off by default and never automatic. When enabled it moves the stop to entry plus spread, expected slippage and commission, only after the target you choose.
- **Trailing** (`none`, `atr`, `swing`, `strategy`) only tightens. `strategy` is an extension point: no strategy supplies one yet.
- The initial stop is **never widened** automatically and a stop is **never removed**. If a stop change fails the existing stop stays, you are alerted, and the policy `on_stop_failure` applies (`retry_then_close` by default: a missing stop is restored or the position is closed).
- Swaps / financing are not added to the breakeven offset (brokers do not publish them in a form the bot can rely on).

**Important:** TP1 and TP2 partial closes are done **by the bot while it is running**. At the broker only the stop-loss and
the *final* take-profit are attached. If the bot or your computer is off, trades are still protected by the stop and the final target, but not
partially closed or trailed.

## Sessions, rollover and news

Sessions are real wall-clock hours in their own city (Tokyo, London, New York) so daylight saving is handled
automatically. The defaults allow London, New York and their overlap, at most 2 entries per session, no new trades in the
30 minutes before the 17:00 New York rollover, scalping trades closed 10 minutes before rollover, swing and intraday trades
may stay open overnight. Maximum holding time: scalp 6 h, intraday 30 h, swing none (swing trades are never cut by an intraday rule).

The news filter uses the Forex Factory calendar the project already downloads. It blocks new trades 30 minutes before and
15 minutes after high-impact events for the pair's currencies and, only if you choose `"existing_positions": "close"`, closes trades ahead of them.
**If the calendar cannot be downloaded the filter has no events and cannot protect you.** The status shows when this happens; do not rely on it blindly.

## Failures and restarts

- An order is saved **before** it is sent. After a timeout or lost connection the bot asks the broker (by the order's own id) what happened and **never re-sends blindly**. While an order is unconfirmed no other entry is made on that symbol; after 2 minutes without a trace it is treated as not executed.
- On every start and after every reconnect the bot reads the broker's positions and orders, matches them with its saved records (rebuilding records if needed, remembering which targets were already taken), counts manual trades in the risk without touching them, and only then resumes.
- A trade closed by the broker while the bot was off (stop or target) is recorded with its result.
- Partial fills use the filled volume for the stop, targets and plan.

## Enable LIVE trading (only after a long, honest demo)

1. Run on demo for weeks and review `trade history`. Remember: the strategies have **no proven edge**. Run the `backtest-history` workflow and read the **evaluation** results first.
2. Open an XM **live MT5** account, log into it in the terminal, and check `python -m fxbot trade status --profile live` shows `LIVE ACCOUNT` with the right balance.
3. Open `trading.json` and review the `live` section (limits are stricter than demo; keep them that way to start).
4. Run `python -m fxbot trade run --profile live`. The bot shows the account and the live risk settings and asks you to **type this exact sentence**:
   `I UNDERSTAND LIVE TRADING RISKS MY MONEY`
   Anything else leaves it in watch-only mode: no orders at all.
5. This confirmation is needed again on every restart. A normal restart never arms live trading by itself.

## Known limitations

- The MT5 (XM) and OANDA adapters were written to the documented APIs but **have not been run against a real account**. The first run on a demo account is the real test. Expect to adjust symbol names, filling mode or time offset.
- The MetaTrader 5 Python package needs the terminal on the same (Windows) computer, so this cannot run on GitHub's servers.
- Signals are calculated from free Yahoo data, which is delayed and not XM's price feed. Every signal is re-checked against the broker's current quote (stop side, spread, reward-to-risk, "not chasing"), but small differences are normal and some good signals will be rejected.
- Entries are **market** entries. Limit-entry signals are only taken if price is still near the entry.
- Currency conversion uses the broker's own tick value; a symbol without one is refused.
- Daily loss uses account equity; deposits and withdrawals while running distort it.
- Only one engine per profile should run at a time.
- Nothing here is financial advice. Trading leveraged products can lose more than you expect.
