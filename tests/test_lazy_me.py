"""Unit tests for all Lazy Me modules, including security regression tests.

Run from the repo root:
    python -m unittest discover tests -v

No network calls: Gmail/Telegram/Gemini clients are mocked.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# Deterministic env BEFORE importing config (load_dotenv never overrides
# pre-set vars, so the real .env can't leak into tests).
os.environ["TELEGRAM_CHAT_ID"] = "111"
os.environ["TELEGRAM_OWNER_ID"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "000:TEST"
os.environ["DIGEST_TIMEZONE"] = "Asia/Kolkata"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import bot  # noqa: E402
import categorize  # noqa: E402
import config  # noqa: E402
import gmail_client  # noqa: E402
import main  # noqa: E402
import state  # noqa: E402
import telegram_client as tg  # noqa: E402


class TempStateMixin:
    """Point the state store at a throwaway file for each test."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = state._PATH
        state._PATH = os.path.join(self._tmp.name, "state.json")

    def tearDown(self):
        state._PATH = self._old_path
        self._tmp.cleanup()
        super().tearDown()


# ---------------------------------------------------------------- config

class ConfigTests(unittest.TestCase):
    def test_require_passes_when_set(self):
        config.require("TELEGRAM_CHAT_ID")  # should not raise

    def test_require_fails_fast_on_missing(self):
        with self.assertRaises(SystemExit):
            config.require("DEFINITELY_NOT_SET_VAR_XYZ")

    def test_owner_defaults_to_chat_id(self):
        self.assertEqual(config.TELEGRAM_OWNER_ID, "111")

    def test_important_bucket_is_trash_exempt(self):
        self.assertIn("Other/Important", config.TRASH_EXEMPT)

    def test_gmail_scope_is_modify_not_full(self):
        # modify = trash (recoverable); full mail scope would allow hard delete.
        self.assertEqual(config.GMAIL_SCOPES, ["https://www.googleapis.com/auth/gmail.modify"])


# ---------------------------------------------------------------- state

class StateTests(TempStateMixin, unittest.TestCase):
    def test_token_fits_callback_data_limit(self):
        t = state.new_token()
        self.assertEqual(len(t), 16)  # token_hex(8) -> 64 bits of entropy
        self.assertLessEqual(len(f"trash:{t}".encode()), 64)

    def test_put_get_delete_roundtrip(self):
        t = state.new_token()
        state.put(t, "Newsletters", ["a", "b"])
        entry = state.get(t)
        self.assertEqual(entry["category"], "Newsletters")
        self.assertEqual(entry["ids"], ["a", "b"])
        state.delete(t)
        self.assertIsNone(state.get(t))

    def test_expired_token_rejected(self):
        t = state.new_token()
        state.put(t, "Newsletters", ["a"])
        data = state._load()
        data[t]["ts"] = time.time() - state.TOKEN_TTL_SECONDS - 1
        state._save(data)
        self.assertIsNone(state.get(t))

    def test_legacy_entry_without_ts_treated_as_expired(self):
        state._save({"deadbeef": {"category": "X", "ids": ["a"]}})
        self.assertIsNone(state.get("deadbeef"))

    def test_expired_entries_purged_on_put(self):
        old = state.new_token()
        state.put(old, "Newsletters", ["a"])
        data = state._load()
        data[old]["ts"] = time.time() - state.TOKEN_TTL_SECONDS - 1
        state._save(data)
        state.put(state.new_token(), "Personal", ["b"])
        self.assertNotIn(old, state._load())

    def test_corrupt_store_returns_empty(self):
        with open(state._PATH, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(state._load(), {})

    def test_save_is_atomic_no_tmp_left_behind(self):
        state.put(state.new_token(), "Personal", ["a"])
        self.assertFalse(os.path.exists(state._PATH + ".tmp"))
        self.assertTrue(os.path.exists(state._PATH))


# ---------------------------------------------------------------- bot auth

class BotAuthTests(unittest.TestCase):
    @staticmethod
    def cq(chat_id, user_id):
        return {"message": {"chat": {"id": chat_id}}, "from": {"id": user_id}}

    def test_owner_in_owner_chat_allowed(self):
        self.assertTrue(bot._authorized(self.cq(111, 111)))

    def test_other_user_in_owner_chat_rejected(self):
        # The group-chat attack: right chat, wrong tapper.
        self.assertFalse(bot._authorized(self.cq(111, 999)))

    def test_owner_in_other_chat_rejected(self):
        self.assertFalse(bot._authorized(self.cq(222, 111)))

    def test_missing_fields_rejected(self):
        self.assertFalse(bot._authorized({}))
        self.assertFalse(bot._authorized({"message": {}, "from": {}}))


class BotCallbackTests(TempStateMixin, unittest.TestCase):
    def make_cq(self, data, user_id=111):
        return {
            "id": "cb1",
            "data": data,
            "from": {"id": user_id},
            "message": {"chat": {"id": 111}, "message_id": 5},
        }

    def test_unauthorized_tap_never_touches_gmail(self):
        t = state.new_token()
        state.put(t, "Ads/Promotions", ["m1"])
        with mock.patch.object(bot.gmail_client, "trash_messages") as trash, \
             mock.patch.object(bot.tg, "answer_callback") as ack:
            bot.handle_callback(self.make_cq(f"trash:{t}", user_id=999))
        trash.assert_not_called()
        ack.assert_called_once_with("cb1", "Not authorized.")
        self.assertIsNotNone(state.get(t))  # token untouched

    def test_unknown_or_expired_token_is_noop(self):
        with mock.patch.object(bot.gmail_client, "trash_messages") as trash, \
             mock.patch.object(bot.tg, "answer_callback") as ack:
            bot.handle_callback(self.make_cq("trash:ffffffffffffffff"))
        trash.assert_not_called()
        ack.assert_called_once_with("cb1", "Already handled or expired.")

    def test_non_trash_callback_ignored(self):
        with mock.patch.object(bot.gmail_client, "trash_messages") as trash, \
             mock.patch.object(bot.tg, "answer_callback"):
            bot.handle_callback(self.make_cq("something:else"))
        trash.assert_not_called()

    def test_valid_tap_trashes_and_consumes_token(self):
        t = state.new_token()
        state.put(t, "Ads/Promotions", ["m1", "m2"])
        with mock.patch.object(bot.gmail_client, "trash_messages", return_value=2) as trash, \
             mock.patch.object(bot.tg, "answer_callback") as ack, \
             mock.patch.object(bot.tg, "edit_message") as edit:
            bot.handle_callback(self.make_cq(f"trash:{t}"))
        trash.assert_called_once_with(["m1", "m2"])
        ack.assert_called_once_with("cb1", "Trashed 2.")
        edit.assert_called_once()
        self.assertIsNone(state.get(t))  # no replay: token consumed

    def test_gmail_failure_keeps_token_for_retry(self):
        t = state.new_token()
        state.put(t, "Ads/Promotions", ["m1"])
        with mock.patch.object(bot.gmail_client, "trash_messages", side_effect=RuntimeError("boom")), \
             mock.patch.object(bot.tg, "answer_callback") as ack:
            bot.handle_callback(self.make_cq(f"trash:{t}"))
        ack.assert_called_once_with("cb1", "Trash failed — see logs.")
        self.assertIsNotNone(state.get(t))


# ---------------------------------------------------------------- main

class SenderParsingTests(unittest.TestCase):
    def test_display_name_variants(self):
        self.assertEqual(main.display_name("Jane Doe <jane@x.com>"), "Jane Doe")
        self.assertEqual(main.display_name('"Quoted" <q@x.com>'), "Quoted")
        self.assertEqual(main.display_name("bare@addr.com"), "bare")
        self.assertEqual(main.display_name(""), "Unknown")

    def test_sender_address_variants(self):
        self.assertEqual(main.sender_address("Jane Doe <jane@x.com>"), "jane@x.com")
        self.assertEqual(main.sender_address("bare@addr.com"), "bare@addr.com")
        self.assertEqual(main.sender_address("No Address"), "")
        self.assertEqual(main.sender_address(""), "")


class BuildMessagesTests(TempStateMixin, unittest.TestCase):
    EMAILS = [
        {"id": "m1", "from": "Google Security <evil@phish.com>", "subject": "act now"},
        {"id": "m2", "from": "Shop <deals@shop.com>", "subject": "sale"},
    ]
    RESULT = {
        "emails": [
            {"index": 0, "category": "Other/Important", "summary": "urgent <b>thing</b>"},
            {"index": 1, "category": "Ads/Promotions", "summary": "50% off"},
        ],
        "category_digests": {},
    }

    def cards(self, with_buttons=True):
        return main.build_messages(self.EMAILS, self.RESULT, with_buttons=with_buttons)

    def test_important_card_has_no_trash_button(self):
        card = next((t, m) for t, m in self.cards() if "Other/Important" in t)
        self.assertIsNone(card[1])

    def test_other_categories_get_trash_button_with_stored_token(self):
        text, markup = next((t, m) for t, m in self.cards() if "Ads/Promotions" in t)
        self.assertIsNotNone(markup)
        data = markup["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(data.startswith("trash:"))
        self.assertLessEqual(len(data.encode()), 64)
        entry = state.get(data.split(":", 1)[1])
        self.assertEqual(entry["ids"], ["m2"])

    def test_spoofed_display_name_shows_real_address(self):
        text = next(t for t, _ in self.cards() if "Other/Important" in t)
        self.assertIn("evil@phish.com", text)

    def test_html_in_llm_summary_is_escaped(self):
        text = next(t for t, _ in self.cards() if "Other/Important" in t)
        self.assertIn("urgent &lt;b&gt;thing&lt;/b&gt;", text)
        self.assertNotIn("<b>thing</b>", text)

    def test_unknown_category_falls_back_to_important(self):
        result = {"emails": [{"index": 0, "category": "Fake Cat", "summary": "x"}],
                  "category_digests": {}}
        msgs = main.build_messages(self.EMAILS[:1], result, with_buttons=True)
        self.assertTrue(any("Other/Important" in t for t, _ in msgs))

    def test_out_of_range_index_does_not_crash(self):
        result = {"emails": [{"index": 99, "category": "Ads/Promotions", "summary": "x"}],
                  "category_digests": {}}
        msgs = main.build_messages(self.EMAILS, result, with_buttons=True)
        self.assertTrue(msgs)  # header at minimum

    def test_dry_run_stores_no_tokens(self):
        self.cards(with_buttons=False)
        self.assertEqual(state._load(), {})

    def test_empty_inbox_message(self):
        msgs = main.build_messages([], {"emails": [], "category_digests": {}})
        self.assertEqual(len(msgs), 1)
        self.assertIn("No mail today", msgs[0][0])


# ---------------------------------------------------------------- categorize

class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(categorize._extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(categorize._extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prose_wrapped_json(self):
        self.assertEqual(
            categorize._extract_json('Sure! Here it is: {"a": 1} hope that helps'),
            {"a": 1},
        )

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            categorize._extract_json("no json here at all")


class HeuristicTests(unittest.TestCase):
    def cat(self, subject="", snippet="", sender=""):
        return categorize.heuristic({"subject": subject, "snippet": snippet, "from": sender})

    def test_rejection(self):
        self.assertEqual(self.cat("Unfortunately we regret..."), "Job — Rejection")

    def test_interview(self):
        self.assertEqual(self.cat("Interview invitation"), "Job — Interview/Progress")

    def test_application_received(self):
        self.assertEqual(self.cat("Application received"), "Job — Application Received")

    def test_finance(self):
        self.assertEqual(self.cat("Your invoice is ready"), "Finance/Bills")

    def test_newsletter_by_sender(self):
        self.assertEqual(self.cat("hello", sender="news@substack.com"), "Newsletters")

    def test_default_bucket(self):
        self.assertEqual(self.cat("hello there"), "Other/Important")


class CategorizeTests(unittest.TestCase):
    EMAILS = [{"from": "a@b.c", "subject": "Your invoice", "snippet": "pay up"}]

    def test_prompt_contains_injection_guard(self):
        self.assertIn("UNTRUSTED DATA", categorize._PROMPT)

    def test_no_key_uses_heuristic_fallback(self):
        with mock.patch.object(categorize.config, "GEMINI_API_KEY", ""):
            result = categorize.categorize(self.EMAILS)
        self.assertEqual(result["emails"][0]["category"], "Finance/Bills")

    def test_llm_failure_falls_back_not_crash(self):
        with mock.patch.object(categorize.config, "GEMINI_API_KEY", "k"), \
             mock.patch.object(categorize.genai, "Client", side_effect=RuntimeError("down")):
            result = categorize.categorize(self.EMAILS)
        self.assertEqual(result["emails"][0]["category"], "Finance/Bills")

    def test_llm_reply_missing_emails_list_falls_back(self):
        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = mock.Mock(text='{"nope": 1}')
        with mock.patch.object(categorize.config, "GEMINI_API_KEY", "k"), \
             mock.patch.object(categorize.genai, "Client", return_value=fake_client):
            result = categorize.categorize(self.EMAILS)
        self.assertEqual(result["emails"][0]["category"], "Finance/Bills")

    def test_empty_input(self):
        self.assertEqual(categorize.categorize([]), {"emails": [], "category_digests": {}})


# ---------------------------------------------------------------- telegram_client

class TelegramClientTests(unittest.TestCase):
    def test_esc_escapes_html(self):
        self.assertEqual(tg.esc("<b>&x</b>"), "&lt;b&gt;&amp;x&lt;/b&gt;")

    def test_chunks_short_text_single_chunk(self):
        self.assertEqual(list(tg._chunks("hi")), ["hi"])

    def test_chunks_respect_limit(self):
        text = "\n".join("x" * 100 for _ in range(100))  # ~10k chars
        chunks = list(tg._chunks(text))
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), tg._MAX)
        # No content lost.
        self.assertEqual("\n".join(chunks).replace("\n", ""), text.replace("\n", ""))

    def test_trash_button_shape(self):
        markup = tg.trash_button(3, "#Promotions", "abcd1234")
        btn = markup["inline_keyboard"][0][0]
        self.assertEqual(btn["callback_data"], "trash:abcd1234")
        self.assertIn("#Promotions", btn["text"])

    def test_url_contains_token(self):
        self.assertIn("000:TEST", tg._url("sendMessage"))

    def test_post_message_retries_on_flood_control(self):
        flood = mock.Mock(status_code=429)
        flood.json.return_value = {"parameters": {"retry_after": 0}}
        ok = mock.Mock(status_code=200)
        with mock.patch.object(tg.requests, "post", side_effect=[flood, ok]) as post, \
             mock.patch.object(tg.time, "sleep"):
            tg._post_message({"chat_id": 1, "text": "hi"})
        self.assertEqual(post.call_count, 2)
        ok.raise_for_status.assert_called_once()

    def test_send_attaches_markup_to_last_chunk_only(self):
        sent = []
        with mock.patch.object(tg, "_post_message", side_effect=lambda p: sent.append(p)):
            text = "\n".join("x" * 100 for _ in range(100))
            tg.send(text, reply_markup={"k": 1})
        self.assertNotIn("reply_markup", sent[0])
        self.assertEqual(sent[-1]["reply_markup"], {"k": 1})

    def test_answer_callback_never_raises(self):
        with mock.patch.object(tg.requests, "post",
                               side_effect=tg.requests.RequestException("net down")):
            self.assertFalse(tg.answer_callback("id1"))


# ---------------------------------------------------------------- gmail_client

class GmailClientTests(unittest.TestCase):
    def test_resolve_date_today_yesterday_explicit(self):
        today = gmail_client.resolve_date("today")
        self.assertEqual(gmail_client.resolve_date(None), today)
        self.assertEqual((today - gmail_client.resolve_date("yesterday")).days, 1)
        d = gmail_client.resolve_date("2026-01-15")
        self.assertEqual((d.year, d.month, d.day), (2026, 1, 15))

    def test_resolve_date_bad_spec_exits(self):
        with self.assertRaises(SystemExit):
            gmail_client.resolve_date("not-a-date")

    def test_day_window_spans_24h(self):
        start, end = gmail_client._day_window(gmail_client.resolve_date("2026-01-15"))
        self.assertEqual(end - start, 86400)

    def test_header_lookup_case_insensitive(self):
        headers = [{"name": "FROM", "value": "a@b.c"}]
        self.assertEqual(gmail_client._header(headers, "from"), "a@b.c")
        self.assertEqual(gmail_client._header(headers, "subject", "dflt"), "dflt")

    def test_extract_body_nested_multipart(self):
        import base64
        enc = base64.urlsafe_b64encode("hello body".encode()).decode()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": enc}, "parts": []},
            ],
        }
        self.assertEqual(gmail_client._extract_body(payload), "hello body")
        self.assertEqual(gmail_client._extract_body(None), "")

    def test_trash_empty_ids_never_builds_service(self):
        with mock.patch.object(gmail_client, "_service") as svc:
            self.assertEqual(gmail_client.trash_messages([]), 0)
            self.assertEqual(gmail_client.trash_messages([None, ""]), 0)
        svc.assert_not_called()

    def test_trash_uses_trash_label_not_delete(self):
        service = mock.Mock()
        with mock.patch.object(gmail_client, "_service", return_value=service):
            n = gmail_client.trash_messages(["m1", None, "m2"])
        self.assertEqual(n, 2)
        _, kwargs = service.users().messages().batchModify.call_args
        self.assertEqual(kwargs["body"], {"ids": ["m1", "m2"], "addLabelIds": ["TRASH"]})


if __name__ == "__main__":
    unittest.main()
