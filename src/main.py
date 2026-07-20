"""Lazy Me — fetch today's Gmail, categorize with Gemini, push to Telegram."""
import argparse
import sys
import time

# Emojis in the digest break the default Windows console (cp1252); force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

import categorize as categorize_mod
import config
import gmail_client
import state
import telegram_client as tg


def display_name(from_header: str) -> str:
    """'Jane Doe <jane@x.com>' -> 'Jane Doe'. Drops the email address entirely."""
    raw = (from_header or "").strip()
    if "<" in raw:
        raw = raw.split("<", 1)[0].strip()
    raw = raw.strip('"').strip()
    if raw:
        return raw
    # No display name: fall back to the address' local part.
    addr = (from_header or "").strip().strip("<>")
    return addr.split("@", 1)[0] if "@" in addr else (addr or "Unknown")


def build_messages(emails, result, day=None, with_buttons=True):
    """Return a list of (text, reply_markup) for Telegram: header, then per category.

    Each category message ends with its #hashtag and (optionally) a Trash button.
    When with_buttons, this also records token -> Gmail ids in the state store.
    """
    tz = ZoneInfo(config.DIGEST_TIMEZONE)
    day = day or datetime.now(tz).date()
    label = day.strftime("%A, %d %b %Y")

    if not emails:
        return [(f"📭 <b>Lazy Me</b> · {label}\nNo mail today. Enjoy the quiet. 🌤️", None)]

    # Group emails by category, preserving CATEGORY order.
    grouped = OrderedDict((c, []) for c in config.CATEGORIES)
    for item in result.get("emails", []):
        cat = item.get("category")
        if cat not in grouped:
            cat = "Other/Important"
        idx = item.get("index")
        e = emails[idx] if isinstance(idx, int) and 0 <= idx < len(emails) else {}
        grouped[cat].append(
            {
                "id": e.get("id"),
                "summary": (item.get("summary") or e.get("subject", "")).strip(),
                "sender": display_name(e.get("from", "")),
            }
        )

    digests = result.get("category_digests", {})

    counts = " ".join(f"{config.EMOJI[c]}{len(v)}" for c, v in grouped.items() if v)
    header = (
        f"📬 <b>Lazy Me</b> · {label}\n"
        f"<b>{len(emails)}</b> mail today\n"
        f"{counts}"
    )
    messages = [(header, None)]

    for cat, items in grouped.items():
        if not items:
            continue
        emoji = config.EMOJI[cat]
        tag = config.TAG[cat]
        lines = [f"{emoji} <b>{tg.esc(cat)}</b> · {len(items)}"]
        blurb = digests.get(cat, "").strip()
        if blurb:
            lines.append(f"<i>{tg.esc(blurb)}</i>")
        lines.append("")
        for it in items:
            lines.append(f"• {tg.esc(it['summary']) or '(no subject)'}")
            if it["sender"]:
                lines.append(f"   <i>{tg.esc(it['sender'])}</i>")
        lines.append("")
        lines.append(tag)

        markup = None
        if with_buttons:
            ids = [it["id"] for it in items if it["id"]]
            if ids:
                token = state.new_token()
                state.put(token, cat, ids)
                markup = tg.trash_button(len(ids), tag, token)
        messages.append(("\n".join(lines), markup))

    return messages


def main():
    parser = argparse.ArgumentParser(description="Lazy Me daily Gmail digest.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest to stdout instead of sending to Telegram.",
    )
    parser.add_argument(
        "--date",
        default="today",
        help="Which day to digest: 'today' (default), 'yesterday', or YYYY-MM-DD.",
    )
    args = parser.parse_args()

    config.require("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
    if not args.dry_run:
        config.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    day = gmail_client.resolve_date(args.date)
    print(f"Fetching mail for {day.isoformat()}...")
    emails = gmail_client.fetch_for_date(day)
    print(f"Fetched {len(emails)} email(s).")

    if not emails and not config.SEND_ON_EMPTY:
        print("No mail and SEND_ON_EMPTY is false; nothing to do.")
        return

    if emails:
        print("Categorizing...")
        result = categorize_mod.categorize(emails)
    else:
        result = {"emails": [], "category_digests": {}}

    messages = build_messages(emails, result, day, with_buttons=not args.dry_run)

    if args.dry_run:
        print("\n----- DIGEST (dry-run) -----\n")
        print("\n\n──────────\n\n".join(text for text, _ in messages))
        return

    print(f"Sending {len(messages)} message(s) to Telegram...")
    for i, (text, markup) in enumerate(messages):
        if i:
            time.sleep(tg.SEND_GAP_SECONDS)  # pace to avoid flood control
        tg.send(text, reply_markup=markup)
    print("Done.")


if __name__ == "__main__":
    main()
