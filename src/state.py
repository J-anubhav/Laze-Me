"""Tiny JSON store mapping a short token -> the Gmail ids for one category card.

Telegram callback_data is capped at 64 bytes, so we can't put ids in the button.
Instead the button carries a short token; this store resolves it to the ids.
Tokens expire after TOKEN_TTL_SECONDS so stale digest buttons stop working.
"""
import json
import os
import secrets
import time

# Store next to the project root (src/..). Git-ignored.
_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "state.json")

# How long a Trash button stays live. Old cards answer "expired" after this.
TOKEN_TTL_SECONDS = 3 * 24 * 3600


def _load() -> dict:
    if not os.path.exists(_PATH):
        return {}
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    # Write-then-rename so a crash mid-write can't corrupt the store.
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _PATH)


def _expired(entry: dict) -> bool:
    return entry.get("ts", 0) < time.time() - TOKEN_TTL_SECONDS


def new_token() -> str:
    return secrets.token_hex(8)  # 16 chars; "trash:<token>" still fits 64 bytes


def put(token: str, category: str, ids: list) -> None:
    # Drop expired entries on each write so the file can't grow forever.
    data = {k: v for k, v in _load().items() if not _expired(v)}
    data[token] = {"category": category, "ids": ids, "ts": time.time()}
    _save(data)


def get(token: str):
    entry = _load().get(token)
    if entry is None or _expired(entry):
        return None
    return entry


def delete(token: str) -> None:
    data = _load()
    if token in data:
        del data[token]
        _save(data)
