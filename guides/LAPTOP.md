# Run the desk on your own always-on laptop (fast signals + automatic trades)

**Why:** GitHub's free scheduler can run a job many minutes or even hours late, so a Telegram alert can arrive long after the
candle. On your own computer the bot checks every minute, so the alert and the trade happen within about a minute or two of the signal.

What the laptop bot does, with **one** signal source:

1. Every minute it analyses all your pairs (the same 1h plan > 15m setup > 5m entry workflow, plus swing and daily setups of grade B or better).
2. When a new setup appears it sends **the same Telegram message as before**.
3. The **same signal** goes to the risk manager and, if it passes, becomes an order on your demo account, with the stop-loss and TP1 attached at the broker.
4. If a Telegram signal is **not** traded, you get a second message saying why (for example "price already moved too far from the entry").
5. A setup that is already more than 15 minutes old when the bot first sees it is not sent and not traded (no flood of old alerts when you start it).

## One-time setup (Windows laptop that stays on)

1. Do the XM / MetaTrader 5 / Python 3.12 steps from `guides/TRADING.md` if you have not (the laptop already has them if the demo test worked).
2. **Download the newest code:** GitHub > Code > Download ZIP, extract it, and **copy your `trading.json` (and `.env` if you have one) into the new folder**.
3. **Put your Telegram details on the laptop.** Copy `.env.example` to a new file named `.env` and fill in your bot token and chat id (`.env` never goes to GitHub). It looks like:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=7246083803
   ```
4. **Turn off the slow GitHub alerts** so you do not get the same signal twice (the late one from GitHub and the fast one from the laptop):
   GitHub > your repo > **Actions** > **scan** (left side) > the **...** menu at the top right > **Disable workflow**.
   Leave **dashboard** on: it only updates the web page, and being late does not matter there.
5. Double-click **`install-autostart.bat`** once. It starts the bot every time you log in and stops the laptop from sleeping while plugged in.
6. Double-click **`start-bot.bat`** now to start it. A black window stays open and prints one line per cycle. If it stops it restarts by itself.
7. Keep **MetaTrader 5 open and logged in**. Put a shortcut of MetaTrader 5 in the Windows Startup folder (press Win+R, type `shell:startup`) so it opens after a restart too.

Also set the laptop to **never sleep when plugged in** (Settings > System > Power) and, if it is a laptop, keep it on the charger.

## How much it risks (your 1,500 account)

The demo defaults are now **1% of equity per trade**, so about **15** on a 1,500 account (it was 0.25%, about 3.75).
The bot also never risks more than 2% on one trade, 4% in total across open trades, and stops opening new trades after a **4%** loss in a day
or **8%** in a week. At most 4 trades are open at once and 1 per pair.

To change it, edit `trading.json` (it only needs the lines you change):

```json
{
  "broker": { "name": "xm", "mt5": { "suffix": "#", "server_utc_offset_hours": null } },
  "demo": { "risk": { "risk_pct": 2.0, "max_trade_risk_pct": 3.0, "max_total_risk_pct": 6.0 } }
}
```

`risk_pct` must not be above `max_trade_risk_pct`, and that must not be above `max_total_risk_pct`.
2% risk means about 30 on 1,500. Higher risk means bigger swings in both directions, and the strategies are not proven to make money.

## Take profit

Each trade has **one target: TP1** (about 1 times the stop distance for most setups). The whole trade closes there, and the stop-loss and TP1
are set at the broker, so they still work if the laptop or the bot is off. To use TP2 / TP3 and partial closes again, set `"exits": { "max_targets": 2 }` (or 3).
With a 1R target you need to win more than about half of your trades just to break even after costs, so watch the results on demo.

## Which signals are traded

The same ones Telegram sends: scalp (the 1h > 15m > 5m workflow), swing and daily, grade B or better, in **any session**
(the old London / New York-only filter is off). The risk manager still refuses trades around high-impact news, a wide spread, an invalid stop, a price that
has already run away from the entry, an unconfirmed earlier order, or a daily / weekly loss limit.
To go back to fewer signals: `"signals": { "modes": ["scalp"] }` or `"sessions": { "allowed": ["London", "New York", "overlap"] }` inside `"demo"`.

## Check it is working

- Black window: a line appears about every 30 seconds. `python -m fxbot trade status` should show your XM demo balance.
- `python -m fxbot trade history` lists decisions and trades. `python -m fxbot trade panel` opens the local control panel.
- The signal-to-Telegram delay should now be about a minute or two, limited by the free Yahoo price data.

## Limits

- The prices used for signals come from free Yahoo data, not XM's own feed, so XM's price can differ a little. Every signal is re-checked against XM's live quote before an order.
- If the laptop is off or offline, nothing is checked, so no signals and no trades. Open trades keep their stop-loss and TP1 at XM.
- Nothing here is financial advice. Run it on the demo account first.
