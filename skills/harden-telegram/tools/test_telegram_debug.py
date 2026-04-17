#!/usr/bin/env python3
"""Unit tests for telegram_debug.py pure functions.

Run with: python3 -m unittest test_telegram_debug.py
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Import from the canonical package location — the in-tree shim at
# `tools/telegram_debug.py` is deprecated and exists only for back-compat.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chop_telegram_tools.telegram_debug import (  # noqa: E402
    REACTION_WHITELIST,
    _default_chat_id,
    _find_owning_claude,
    _read_bot_token,
    _redact,
    build_direct_request,
    build_react_request,
    build_reply_request,
    classify_bridges,
    parse_env_token,
    parse_proc_stat,
    parse_sent_message_id,
    session_subscribed_to_telegram,
)


def make_stat_reader(table: dict[int, tuple[str, int]]):
    """Build a fake stat reader from a {pid: (comm, ppid)} table."""

    def reader(pid: int):
        return table.get(pid)

    return reader


def make_alive(alive_pids: set[int]):
    def is_alive(pid: int) -> bool:
        return pid in alive_pids

    return is_alive


class TestParseProcStat(unittest.TestCase):
    def test_simple_comm(self):
        self.assertEqual(
            parse_proc_stat("123 (claude) S 99 123 99 ...\n"),
            ("claude", 99),
        )

    def test_comm_with_space(self):
        self.assertEqual(
            parse_proc_stat("123 (my proc) S 99 123 99 ...\n"),
            ("my proc", 99),
        )

    def test_comm_with_inner_parens(self):
        # The whole point of anchoring on rfind(')') — a naive split on
        # space or on first ')' would truncate the comm mid-string.
        self.assertEqual(
            parse_proc_stat("123 (weird )name) S 99 123 99 ...\n"),
            ("weird )name", 99),
        )

    def test_comm_with_only_parens(self):
        self.assertEqual(
            parse_proc_stat("7 (()) S 1 0 0 ...\n"),
            ("()", 1),
        )

    def test_missing_parens_returns_none(self):
        self.assertIsNone(parse_proc_stat("123 bad line\n"))

    def test_truncated_after_comm_returns_none(self):
        self.assertIsNone(parse_proc_stat("123 (claude)"))

    def test_non_numeric_ppid_returns_none(self):
        self.assertIsNone(parse_proc_stat("123 (claude) S ??? 123 99 ...\n"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_proc_stat(""))


class TestFindOwningClaude(unittest.TestCase):
    def test_direct_child_of_claude(self):
        table = {
            100: ("bun", 99),
            99: ("claude", 1),
        }
        self.assertEqual(
            _find_owning_claude(100, stat_reader=make_stat_reader(table)), 99
        )

    def test_grandchild_through_shell(self):
        table = {
            200: ("python3", 150),
            150: ("bash", 99),
            99: ("claude", 1),
        }
        self.assertEqual(
            _find_owning_claude(200, stat_reader=make_stat_reader(table)), 99
        )

    def test_no_claude_ancestor(self):
        table = {
            200: ("bun", 150),
            150: ("systemd", 1),
        }
        self.assertIsNone(_find_owning_claude(200, stat_reader=make_stat_reader(table)))

    def test_process_vanished_mid_walk(self):
        # pid 150 is missing from the table — simulates a race where a parent
        # exited between the pgrep result and the stat read.
        table = {
            200: ("bun", 150),
        }
        self.assertIsNone(_find_owning_claude(200, stat_reader=make_stat_reader(table)))

    def test_nearest_claude_wins_when_nested(self):
        # Two claude in the ancestry — return the nearest one, not the outermost.
        table = {
            300: ("bun", 250),
            250: ("claude", 200),
            200: ("bash", 100),
            100: ("claude", 1),
        }
        self.assertEqual(
            _find_owning_claude(300, stat_reader=make_stat_reader(table)), 250
        )

    def test_matches_claude_code_shim(self):
        # Linux truncates comm to 15 chars; launchers like `claude-code` or
        # `claude-1m` show up as-is and must still be recognized.
        table = {
            100: ("bun", 99),
            99: ("claude-code", 1),
        }
        self.assertEqual(
            _find_owning_claude(100, stat_reader=make_stat_reader(table)), 99
        )

    def test_loop_guard(self):
        # Impossible in practice, but a pid-reuse race could in theory produce
        # a cycle. The guard returns None instead of spinning forever.
        table = {
            10: ("a", 20),
            20: ("b", 10),
        }
        self.assertIsNone(_find_owning_claude(10, stat_reader=make_stat_reader(table)))


class TestClassifyBridges(unittest.TestCase):
    def test_single_bridge_owned_by_us(self):
        table = {
            500: ("bun", 400),
            400: ("bun", 300),
            300: ("claude", 1),
        }
        bridges = classify_bridges(
            [500],
            our_claude_pid=300,
            stat_reader=make_stat_reader(table),
            is_alive=make_alive({300}),
        )
        self.assertEqual(len(bridges), 1)
        self.assertEqual(bridges[0]["classification"], "ours")
        self.assertEqual(bridges[0]["owning_claude"], 300)

    def test_multi_session_ours_plus_other(self):
        # The scenario that tripped up the old doctor: two legitimate bridges,
        # one per Claude session, neither a zombie.
        table = {
            500: ("bun", 400),
            400: ("bun", 300),
            300: ("claude", 1),
            600: ("bun", 550),
            550: ("bun", 290),
            290: ("claude", 1),
        }
        bridges = classify_bridges(
            [500, 600],
            our_claude_pid=300,
            stat_reader=make_stat_reader(table),
            is_alive=make_alive({300, 290}),
        )
        by_pid = {b["pid"]: b for b in bridges}
        self.assertEqual(by_pid[500]["classification"], "ours")
        self.assertEqual(by_pid[600]["classification"], "other-session")

    def test_bridge_whose_claude_died_is_orphaned(self):
        table = {
            500: ("bun", 400),
            400: ("bun", 290),
            290: ("claude", 1),
        }
        bridges = classify_bridges(
            [500],
            our_claude_pid=300,
            stat_reader=make_stat_reader(table),
            is_alive=make_alive(set()),  # no one alive
        )
        self.assertEqual(bridges[0]["classification"], "orphaned")

    def test_bridge_with_no_claude_ancestor_is_orphaned(self):
        table = {
            500: ("bun", 1),
        }
        bridges = classify_bridges(
            [500],
            our_claude_pid=300,
            stat_reader=make_stat_reader(table),
            is_alive=make_alive({300}),
        )
        self.assertEqual(bridges[0]["classification"], "orphaned")
        self.assertIsNone(bridges[0]["owning_claude"])

    def test_two_bridges_both_owned_by_us_is_true_zombie(self):
        # Actual duplicate within a single session — this IS a failure.
        table = {
            500: ("bun", 400),
            400: ("bun", 300),
            600: ("bun", 550),
            550: ("bun", 300),
            300: ("claude", 1),
        }
        bridges = classify_bridges(
            [500, 600],
            our_claude_pid=300,
            stat_reader=make_stat_reader(table),
            is_alive=make_alive({300}),
        )
        self.assertEqual(
            [b["classification"] for b in bridges],
            ["ours", "ours"],
        )

    def test_empty_pid_list(self):
        self.assertEqual(
            classify_bridges(
                [],
                our_claude_pid=300,
                stat_reader=make_stat_reader({}),
                is_alive=make_alive({300}),
            ),
            [],
        )

    def test_doctor_run_from_cron_has_no_our_claude(self):
        # When our_claude_pid is None, every bridge with a live claude
        # ancestor is "other-session"; bridges with no ancestry are orphaned.
        table = {
            500: ("bun", 300),
            300: ("claude", 1),
        }
        bridges = classify_bridges(
            [500],
            our_claude_pid=None,
            stat_reader=make_stat_reader(table),
            is_alive=make_alive({300}),
        )
        self.assertEqual(bridges[0]["classification"], "other-session")


class TestSessionSubscribedToTelegram(unittest.TestCase):
    def test_plain_claude_is_not_subscribed(self):
        self.assertFalse(
            session_subscribed_to_telegram(["claude", "--dangerously-skip-permissions"])
        )

    def test_channels_flag_with_telegram_plugin(self):
        self.assertTrue(
            session_subscribed_to_telegram(
                [
                    "claude",
                    "--dangerously-skip-permissions",
                    "--channels",
                    "plugin:telegram@claude-plugins-official",
                ]
            )
        )

    def test_channels_flag_equals_form(self):
        self.assertTrue(
            session_subscribed_to_telegram(
                ["claude", "--channels=plugin:telegram@claude-plugins-official"]
            )
        )

    def test_channels_with_comma_separated_list(self):
        self.assertTrue(
            session_subscribed_to_telegram(
                [
                    "claude",
                    "--channels",
                    "plugin:other-channel,plugin:telegram@claude-plugins-official",
                ]
            )
        )

    def test_channels_with_unrelated_plugin(self):
        self.assertFalse(
            session_subscribed_to_telegram(
                ["claude", "--channels", "plugin:slack@somewhere"]
            )
        )

    def test_channels_case_insensitive(self):
        self.assertTrue(
            session_subscribed_to_telegram(
                ["claude", "--channels", "plugin:Telegram@official"]
            )
        )

    def test_channels_flag_with_no_value_is_ignored(self):
        # Trailing --channels with nothing after it shouldn't crash or match.
        self.assertFalse(session_subscribed_to_telegram(["claude", "--channels"]))

    def test_empty_argv(self):
        self.assertFalse(session_subscribed_to_telegram([]))


class TestParseEnvToken(unittest.TestCase):
    def test_plain_assignment(self):
        self.assertEqual(parse_env_token("TELEGRAM_BOT_TOKEN=123:abc\n"), "123:abc")

    def test_double_quoted(self):
        self.assertEqual(parse_env_token('TELEGRAM_BOT_TOKEN="123:abc"\n'), "123:abc")

    def test_single_quoted(self):
        self.assertEqual(parse_env_token("TELEGRAM_BOT_TOKEN='123:abc'\n"), "123:abc")

    def test_export_prefix(self):
        # `.env` files copied from shell profiles often carry `export`.
        self.assertEqual(
            parse_env_token("export TELEGRAM_BOT_TOKEN=123:abc\n"), "123:abc"
        )

    def test_trailing_whitespace(self):
        self.assertEqual(parse_env_token("TELEGRAM_BOT_TOKEN=123:abc   \n"), "123:abc")

    def test_inline_comment_unquoted(self):
        # Unquoted values take a `#` as an inline comment boundary.
        self.assertEqual(
            parse_env_token("TELEGRAM_BOT_TOKEN=123:abc # prod token\n"),
            "123:abc",
        )

    def test_hash_inside_quoted_value_kept(self):
        # Quoted values are literal — no comment stripping.
        self.assertEqual(parse_env_token('TELEGRAM_BOT_TOKEN="abc#def"\n'), "abc#def")

    def test_ignores_comment_lines_before_match(self):
        text = "# leading comment\nOTHER_VAR=foo\nTELEGRAM_BOT_TOKEN=123:abc\n"
        self.assertEqual(parse_env_token(text), "123:abc")

    def test_substring_collision_rejected(self):
        # A different var whose name *contains* TELEGRAM_BOT_TOKEN must not match.
        self.assertIsNone(parse_env_token("OLD_TELEGRAM_BOT_TOKEN=stale\n"))

    def test_missing_returns_none(self):
        self.assertIsNone(parse_env_token("OTHER=x\n"))

    def test_empty_input(self):
        self.assertIsNone(parse_env_token(""))


class TestReadBotToken(unittest.TestCase):
    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError) as ctx:
                _read_bot_token(Path(d) / "nonexistent.env")
            self.assertIn("token file missing", str(ctx.exception))

    def test_present_but_no_token_key_raises(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("OTHER=foo\n")
            with self.assertRaises(RuntimeError) as ctx:
                _read_bot_token(env)
            self.assertIn("TELEGRAM_BOT_TOKEN=", str(ctx.exception))

    def test_reads_plain_value(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("TELEGRAM_BOT_TOKEN=999:xyz\n")
            self.assertEqual(_read_bot_token(env), "999:xyz")


class TestDefaultChatId(unittest.TestCase):
    """Drive _default_chat_id via the LARRY_TELEGRAM_DIR env var it honors."""

    def _with_db(self, rows):
        tmp = tempfile.TemporaryDirectory()
        db = Path(tmp.name) / "inbound.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE inbound (id INTEGER PRIMARY KEY, chat_id INTEGER)")
        con.executemany("INSERT INTO inbound (chat_id) VALUES (?)", rows)
        con.commit()
        con.close()
        return tmp  # caller keeps reference alive

    def test_missing_db_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LARRY_TELEGRAM_DIR")
            os.environ["LARRY_TELEGRAM_DIR"] = d
            try:
                self.assertIsNone(_default_chat_id())
            finally:
                if old is None:
                    del os.environ["LARRY_TELEGRAM_DIR"]
                else:
                    os.environ["LARRY_TELEGRAM_DIR"] = old

    def test_returns_latest_chat_id_as_string(self):
        tmp = self._with_db([(111,), (222,), (333,)])
        old = os.environ.get("LARRY_TELEGRAM_DIR")
        os.environ["LARRY_TELEGRAM_DIR"] = tmp.name
        try:
            result = _default_chat_id()
            self.assertEqual(result, "333")
            self.assertIsInstance(result, str)  # int → str coercion
        finally:
            tmp.cleanup()
            if old is None:
                del os.environ["LARRY_TELEGRAM_DIR"]
            else:
                os.environ["LARRY_TELEGRAM_DIR"] = old

    def test_empty_table_returns_none(self):
        tmp = self._with_db([])
        old = os.environ.get("LARRY_TELEGRAM_DIR")
        os.environ["LARRY_TELEGRAM_DIR"] = tmp.name
        try:
            self.assertIsNone(_default_chat_id())
        finally:
            tmp.cleanup()
            if old is None:
                del os.environ["LARRY_TELEGRAM_DIR"]
            else:
                os.environ["LARRY_TELEGRAM_DIR"] = old


class TestBuildDirectRequest(unittest.TestCase):
    def test_url_shape(self):
        url, _ = build_direct_request("T0KEN", "123", "hi")
        self.assertEqual(url, "https://api.telegram.org/botT0KEN/sendMessage")

    def test_tag_prefix(self):
        _, body = build_direct_request("T", "123", "hello")
        self.assertIn(b"text=%5Bdirect-send%5D+hello", body)

    def test_chat_id_int_coerced_to_str(self):
        # urlencode accepts ints, but pin the coercion contract.
        _, body = build_direct_request("T", 456, "x")  # type: ignore[arg-type]
        self.assertIn(b"chat_id=456", body)

    def test_unicode_text_roundtrips(self):
        _, body = build_direct_request("T", "1", "héllo 🚨")
        # Body must decode back to the same string after urldecode.
        import urllib.parse

        decoded = dict(urllib.parse.parse_qsl(body.decode()))
        self.assertEqual(decoded["text"], "[direct-send] héllo 🚨")

    def test_double_tagging_allowed(self):
        # Passing an already-tagged message produces a doubled tag. This is
        # the documented behavior — don't silently strip.
        _, body = build_direct_request("T", "1", "[direct-send] already")
        decoded_body = body.decode()
        self.assertIn("%5Bdirect-send%5D+%5Bdirect-send%5D+already", decoded_body)


class TestRedact(unittest.TestCase):
    def test_replaces_token(self):
        self.assertEqual(
            _redact("error: got 401 for bot12345:abc/sendMessage", "12345:abc"),
            "error: got 401 for bot<redacted>/sendMessage",
        )

    def test_noop_when_token_absent(self):
        self.assertEqual(_redact("network timeout", "12345:abc"), "network timeout")

    def test_empty_token_is_noop(self):
        self.assertEqual(_redact("anything", ""), "anything")


class TestBuildReplyRequest(unittest.TestCase):
    def test_url_shape(self):
        url, _ = build_reply_request("T0KEN", "123", "hi", 42)
        self.assertEqual(url, "https://api.telegram.org/botT0KEN/sendMessage")

    def test_no_direct_send_tag(self):
        # Unlike build_direct_request, send-reply must NOT auto-tag. This
        # is a contract: replies route through the normal conversation.
        _, body = build_reply_request("T", "123", "hello", 42)
        self.assertNotIn(b"direct-send", body)
        self.assertIn(b"text=hello", body)

    def test_reply_to_message_id_present(self):
        _, body = build_reply_request("T", "123", "hi", 42)
        self.assertIn(b"reply_to_message_id=42", body)

    def test_reply_to_coerced_to_int(self):
        # Guards against accidentally allowing arbitrary strings through
        # as reply_to_message_id — int() will raise on bad input.
        with self.assertRaises((TypeError, ValueError)):
            build_reply_request("T", "1", "hi", "not-an-int")  # type: ignore[arg-type]

    def test_unicode_roundtrips(self):
        import urllib.parse

        _, body = build_reply_request("T", "1", "héllo 🎉", 7)
        decoded = dict(urllib.parse.parse_qsl(body.decode()))
        self.assertEqual(decoded["text"], "héllo 🎉")


class TestBuildReactRequest(unittest.TestCase):
    def test_url_shape(self):
        url, _ = build_react_request("T0KEN", "123", 42, "👍")
        self.assertEqual(url, "https://api.telegram.org/botT0KEN/setMessageReaction")

    def test_reaction_json_shape(self):
        import urllib.parse

        _, body = build_react_request("T", "123", 42, "🔥")
        decoded = dict(urllib.parse.parse_qsl(body.decode()))
        self.assertEqual(decoded["chat_id"], "123")
        self.assertEqual(decoded["message_id"], "42")
        # `reaction` is a JSON-encoded list per Bot API.
        import json as _json

        reaction = _json.loads(decoded["reaction"])
        self.assertEqual(reaction, [{"type": "emoji", "emoji": "🔥"}])


class TestReactionWhitelist(unittest.TestCase):
    def test_contains_common_reactions(self):
        for emoji in ("👍", "🎉", "👀", "🫡", "🔥", "❤"):
            self.assertIn(emoji, REACTION_WHITELIST)

    def test_contains_larry_bead_consumer_reactions(self):
        # These three are used by larry-bead (igor2) — capture (✍), close (🏆),
        # cancel (🗑). Regressing any of them is a silent consumer break since
        # the client-side rejection fires before any Bot API round-trip.
        for emoji in ("\u270d", "\U0001f3c6", "\U0001f5d1"):  # ✍ 🏆 🗑
            self.assertIn(emoji, REACTION_WHITELIST)


class TestParseSentMessageId(unittest.TestCase):
    def test_happy_path(self):
        body = '{"ok":true,"result":{"message_id":555,"date":1}}'
        self.assertEqual(parse_sent_message_id(body), 555)

    def test_missing_result(self):
        self.assertIsNone(parse_sent_message_id('{"ok":false}'))

    def test_missing_message_id(self):
        self.assertIsNone(parse_sent_message_id('{"ok":true,"result":{}}'))

    def test_malformed_json(self):
        self.assertIsNone(parse_sent_message_id("not-json"))

    def test_empty_string(self):
        self.assertIsNone(parse_sent_message_id(""))


class _FakeResponse:
    """Minimal stand-in for the object urllib.request.urlopen yields."""

    def __init__(self, *, status: int = 200, body: str = "{}"):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_token(monkey_env: dict, token: str = "T0KEN"):
    """Write a .env file and point HOME at it for _read_bot_token to find."""
    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    env_dir = home / ".claude" / "channels" / "telegram"
    env_dir.mkdir(parents=True)
    (env_dir / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    monkey_env["HOME"] = str(home)
    return tmp


class TestSendReply(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._tmp = _patch_token(os.environ)

    def tearDown(self):
        self._tmp.cleanup()
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def test_success_prints_new_message_id(self):
        import io
        import telegram_debug as td

        captured = {}

        def fake_urlopen(url, data=None, timeout=0):
            captured["url"] = url
            captured["data"] = data
            return _FakeResponse(
                status=200,
                body='{"ok":true,"result":{"message_id":777,"date":1}}',
            )

        old_urlopen = __import__("urllib.request").request.urlopen
        __import__("urllib.request").request.urlopen = fake_urlopen
        try:
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            try:
                rc = td.send_reply("hi there", chat_id="999", reply_to=42)
            finally:
                sys.stdout = old_stdout
        finally:
            __import__("urllib.request").request.urlopen = old_urlopen

        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "777")
        # Sanity: the token was read and the URL includes sendMessage.
        self.assertIn("/sendMessage", captured["url"])
        self.assertIn(b"reply_to_message_id=42", captured["data"])

    def test_missing_token_fails(self):
        import telegram_debug as td

        # Point HOME at an empty dir so _read_bot_token raises.
        empty = tempfile.TemporaryDirectory()
        old_home = os.environ["HOME"]
        os.environ["HOME"] = empty.name
        try:
            rc = td.send_reply("hi", chat_id="999", reply_to=1)
        finally:
            os.environ["HOME"] = old_home
            empty.cleanup()
        self.assertEqual(rc, 1)

    def test_missing_chat_id_fails(self):
        import telegram_debug as td

        rc = td.send_reply("hi", chat_id="", reply_to=1)
        self.assertEqual(rc, 1)


class TestSetReaction(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._tmp = _patch_token(os.environ)

    def tearDown(self):
        self._tmp.cleanup()
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def test_success_path(self):
        import telegram_debug as td

        captured = {}

        def fake_urlopen(url, data=None, timeout=0):
            captured["url"] = url
            captured["data"] = data
            return _FakeResponse(status=200, body='{"ok":true,"result":true}')

        old_urlopen = __import__("urllib.request").request.urlopen
        __import__("urllib.request").request.urlopen = fake_urlopen
        try:
            rc = td.set_reaction("👍", chat_id="999", message_id=42)
        finally:
            __import__("urllib.request").request.urlopen = old_urlopen

        self.assertEqual(rc, 0)
        self.assertIn("/setMessageReaction", captured["url"])

    def test_emoji_not_in_whitelist_fails_before_network(self):
        import telegram_debug as td

        # If this ever reaches the network, it means whitelist validation
        # was skipped — guard that by making urlopen explode.
        def poison(*a, **k):
            raise AssertionError("urlopen must not be called for whitelist rejection")

        old_urlopen = __import__("urllib.request").request.urlopen
        __import__("urllib.request").request.urlopen = poison
        try:
            # Picked a plausible but non-whitelisted emoji.
            rc = td.set_reaction("🧇", chat_id="999", message_id=42)
        finally:
            __import__("urllib.request").request.urlopen = old_urlopen

        self.assertEqual(rc, 1)

    def test_missing_token_fails(self):
        import telegram_debug as td

        empty = tempfile.TemporaryDirectory()
        old_home = os.environ["HOME"]
        os.environ["HOME"] = empty.name
        try:
            rc = td.set_reaction("👍", chat_id="999", message_id=42)
        finally:
            os.environ["HOME"] = old_home
            empty.cleanup()
        self.assertEqual(rc, 1)

    def test_missing_chat_id_fails(self):
        import telegram_debug as td

        rc = td.set_reaction("👍", chat_id="", message_id=42)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
