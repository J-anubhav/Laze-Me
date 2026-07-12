"""Send the digest to Telegram via the Bot API."""
import html

import requests

import config

_BASE = "https://api.telegram.org/bot{token}/{method}"
_MAX = 4096  # Telegram hard limit per message.


def _url(method: str) -> str:
    return _BASE.format(token=config.TELEGRAM_BOT_TOKEN, method=method)


def _chunks(text: str):
    """Split on newlines so messages stay under the Telegram size limit."""
    if len(text) <= _MAX:
        yield text
        return
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > _MAX:
            if buf:
                yield buf
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        yield buf


def send(text: str, reply_markup=None):
    """Send a message. If reply_markup is given, it's attached to the LAST chunk."""
    chunks = list(_chunks(text))
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        resp = requests.post(_url("sendMessage"), json=payload, timeout=30)
        resp.raise_for_status()


def trash_button(count: int, tag: str, token: str) -> dict:
    """Inline keyboard: a single 'trash all' button carrying a token."""
    return {
        "inline_keyboard": [
            [{"text": f"🗑 Trash all {tag} ({count})", "callback_data": f"trash:{token}"}]
        ]
    }


def get_updates(offset=None, timeout=25):
    """Long-poll for updates (used by the bot poller)."""
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(_url("getUpdates"), params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])


def answer_callback(callback_id: str, text: str = "") -> bool:
    """Best-effort ack of a button tap. Never raises (network can be flaky)."""
    try:
        requests.post(
            _url("answerCallbackQuery"),
            json={"callback_query_id": callback_id, "text": text},
            timeout=30,
        )
        return True
    except requests.RequestException as exc:
        print(f"[tg] answer_callback failed: {exc}")
        return False


def edit_message(chat_id, message_id, text: str) -> bool:
    """Replace a message's text and drop its buttons. Never raises."""
    try:
        requests.post(
            _url("editMessageText"),
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        return True
    except requests.RequestException as exc:
        print(f"[tg] edit_message failed: {exc}")
        return False


def esc(text: str) -> str:
    """Escape user/email text for Telegram HTML parse mode."""
    return html.escape(text, quote=False)
