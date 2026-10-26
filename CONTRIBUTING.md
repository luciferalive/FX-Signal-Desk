# Contributing to FX Signal Desk

Thanks for wanting to help. You do not need to be a pro coder: ideas, bug reports, strategy rules and doc fixes all count.

## Easy ways to help
- **Report a bug** or **suggest a feature** with the issue templates.
- **Share a strategy idea** (for example exact Set & Forget rules, Silver Bullet, breaker blocks). Write the rules as clearly as you can: what must happen, where entry, stop loss and take profit go.
- **Improve the docs** or fix typos.
- **Add tests** in `tests/`.

## Running it locally
```bash
pip install -r requirements.txt pytest
python -m pytest -q
python -m fxbot dashboard --demo --out demo   # builds a demo page with made-up prices
```

## Ground rules
1. **Never commit secrets.** Telegram tokens, chat IDs and webhooks go in GitHub Secrets or a local `.env` (already git-ignored).
2. **No look-ahead.** Signals may only use candles that are already closed. Tests and `validate` check this.
3. **No performance promises.** Do not add claims like "80% win rate" unless they come from a reproducible backtest that is committed with the code.
4. Keep new strategies behind the confirmation gate so weak setups are filtered.
5. **Trading layer:** every order must go through the risk manager; never add a path from a strategy straight to a broker, and never log or store credentials. Test broker code with the fakes in `tests/`, not real accounts.
6. Add or update a test with every change, and make sure `python -m pytest -q` passes.

## Pull requests
Fork, make a branch, open a PR, and fill in the template. Small focused PRs get reviewed fastest.

This project is for education and research. It is not financial advice.
