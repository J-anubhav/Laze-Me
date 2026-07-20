"""Lazy Me bot — long-poll Telegram and act on 'Trash all' button taps.

Run this as a persistent process (your PC / a small host):
    python src/bot.py

It listens for the inline buttons attached to digest cards. Tapping
'🗑 Trash all #Tag' moves those Gmail messages to Trash (recoverable ~30 days).
Only taps from the configured TELEGRAM_CHAT_ID are honored.
"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
import gmail_client
import state
import telegram_client as tg


def _authorized(cq) -> bool:
    """Only the owner, in the configured chat, may trigger deletes.

    Checking the chat alone is not enough: in a group chat every member can
    tap buttons, so the tapping user's id must match TELEGRAM_OWNER_ID too.
    """
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
    user_id = str(cq.get("from", {}).get("id", ""))
    return (
        chat_id == str(config.TELEGRAM_CHAT_ID)
        and user_id == str(config.TELEGRAM_OWNER_ID)
    )


def handle_callback(cq):
    data = cq.get("data", "")
    cb_id = cq["id"]
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")

    if not _authorized(cq):
        tg.answer_callback(cb_id, "Not authorized.")
        return

    if not data.startswith("trash:"):
        tg.answer_callback(cb_id)
        return

    token = data.split(":", 1)[1]
    entry = state.get(token)
    if not entry:
        tg.answer_callback(cb_id, "Already handled or expired.")
        return

    category = entry["category"]
    ids = entry["ids"]
    tag = config.TAG.get(category, "")
    try:
        n = gmail_client.trash_messages(ids)
    except Exception as exc:  # noqa: BLE001
        print(f"[bot] trash failed: {exc}")
        tg.answer_callback(cb_id, "Trash failed — see logs.")
        return

    state.delete(token)
    tg.answer_callback(cb_id, f"Trashed {n}.")
    tg.edit_message(
        chat_id,
        message_id,
        f"🗑 <b>Trashed {n} email(s)</b> · {tag}\n<i>Recoverable in Gmail Trash ~30 days.</i>",
    )
    print(f"[bot] trashed {n} from {category}")


def main():
    config.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    print("Lazy Me bot listening. Ctrl+C to stop.")
    offset = None
    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=25)
        except Exception as exc:  # noqa: BLE001 - survive transient network errors
            print(f"[bot] getUpdates error: {exc}; retrying in 3s")
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            if "callback_query" in u:
                try:
                    handle_callback(u["callback_query"])
                except Exception as exc:  # noqa: BLE001 - one bad tap must not kill the bot
                    print(f"[bot] handler error: {exc}")


if __name__ == "__main__":
    main()
