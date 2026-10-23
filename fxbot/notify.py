"""Free alert channels: Telegram bot, Discord webhook. Always prints too."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _post(url: str, payload: dict):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True, ""
    except urllib.error.HTTPError as e:  # keep the reason, it tells the user what to fix
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        return False, f"HTTP {e.code} {body}"
    except Exception as e:
        return False, str(e)


def chat_ids(raw: str | None) -> list[str]:
    """TELEGRAM_CHAT_ID may hold one id or several separated by commas, e.g. 111,222."""
    return [c.strip() for c in (raw or "").replace(";", ",").split(",") if c.strip()]


def send(text: str) -> None:
    print(text, flush=True)
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    if tok:
        for chat in chat_ids(os.getenv("TELEGRAM_CHAT_ID")):  # one failing chat must not stop the others
            ok, err = _post(f"https://api.telegram.org/bot{tok}/sendMessage", {"chat_id": chat, "text": text})
            if not ok:
                print(f"[notify] Telegram failed for chat {chat}: {err}")
    hook = os.getenv("DISCORD_WEBHOOK_URL")
    if hook:
        ok, err = _post(hook, {"content": text[:1900]})
        if not ok:
            print(f"[notify] Discord failed: {err}")


def telegram_test() -> bool:
    """Send a hello message and explain in plain words what is wrong if it fails."""
    tok, chats = os.getenv("TELEGRAM_BOT_TOKEN"), chat_ids(os.getenv("TELEGRAM_CHAT_ID"))
    if not tok:
        print("PROBLEM: the secret TELEGRAM_BOT_TOKEN is empty or missing. Add it in Settings > Secrets and variables > Actions.")
        return False
    if not chats:
        print("PROBLEM: the secret TELEGRAM_CHAT_ID is empty or missing. Add it in Settings > Secrets and variables > Actions.")
        return False
    good = True
    for chat in chats:
        ok, err = _post(f"https://api.telegram.org/bot{tok}/sendMessage",
                        {"chat_id": chat, "text": "Hello from your forex bot. If you can read this, Telegram alerts are working."})
        if ok:
            print(f"SUCCESS: chat {chat} should have a message from your bot.")
            continue
        good = False
        hint = ""
        if "401" in err:
            hint = " The token is wrong: copy it again from BotFather (the whole thing, including the colon)."
        elif "400" in err and "chat not found" in err:
            hint = " The chat ID is wrong, or that person has not pressed Start / sent a message to the bot yet."
        elif "403" in err:
            hint = " The bot is blocked or has not been started: that person must open the bot in Telegram and press Start."
        print(f"PROBLEM: Telegram refused chat {chat} ({err}).{hint}")
    return good
