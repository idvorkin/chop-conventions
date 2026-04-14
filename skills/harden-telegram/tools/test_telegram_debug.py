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

sys.path.insert(0, str(Path(__file__).parent))

import telegram_debug  # noqa: E402
from telegram_debug import (  # noqa: E402
    DoctorReport,
    _default_chat_id,
    _doctor_check_deploy,
    _find_owning_claude,
    _read_bot_token,
    _redact,
    _resolve_source_dir,
    build_direct_request,
    check_plugin_deploy,
    classify_bridges,
    parse_env_token,
    parse_proc_stat,
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


class TestResolveSourceDir(unittest.TestCase):
    """Unit tests for the source-dir lookup order used by the drift check.

    The drift check's whole value hinges on this function:
      1. TELEGRAM_SOURCE_DIR env var wins if set.
      2. Hardcoded default (~/gits/igor2/telegram-server) is next.
      3. Neither resolving to a dir with server.ts → (None, None).

    Tests monkeypatch the module-level _DEFAULT_SOURCE_DIR constant and the
    env var so no real filesystem state outside a tempdir is consulted.
    """

    def setUp(self):
        self._saved_env = os.environ.pop("TELEGRAM_SOURCE_DIR", None)
        self._saved_default = telegram_debug._DEFAULT_SOURCE_DIR
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        if self._saved_env is not None:
            os.environ["TELEGRAM_SOURCE_DIR"] = self._saved_env
        else:
            os.environ.pop("TELEGRAM_SOURCE_DIR", None)
        telegram_debug._DEFAULT_SOURCE_DIR = self._saved_default
        self.tmp.cleanup()

    def test_env_var_wins_when_valid(self):
        env_dir = self.tmp_path / "env_source"
        env_dir.mkdir()
        (env_dir / "server.ts").write_text("// env content")
        default_dir = self.tmp_path / "default_source"
        default_dir.mkdir()
        (default_dir / "server.ts").write_text("// default content")
        os.environ["TELEGRAM_SOURCE_DIR"] = str(env_dir)
        telegram_debug._DEFAULT_SOURCE_DIR = default_dir
        path, source = _resolve_source_dir()
        self.assertEqual(path, env_dir)
        self.assertEqual(source, "env")

    def test_env_var_set_but_missing_server_ts_returns_none_path(self):
        # Explicitly-set-but-broken path should NOT silently fall through to
        # the default — that would confuse operators who think they turned
        # the check off by setting the env var to /tmp/nonexistent.
        os.environ["TELEGRAM_SOURCE_DIR"] = "/tmp/definitely-does-not-exist-xyz"
        # Even though the default exists, the explicit override suppresses it.
        default_dir = self.tmp_path / "default_source"
        default_dir.mkdir()
        (default_dir / "server.ts").write_text("// default content")
        telegram_debug._DEFAULT_SOURCE_DIR = default_dir
        path, source = _resolve_source_dir()
        self.assertIsNone(path)
        self.assertEqual(source, "env")

    def test_falls_back_to_default_when_env_unset(self):
        default_dir = self.tmp_path / "default_source"
        default_dir.mkdir()
        (default_dir / "server.ts").write_text("// default content")
        telegram_debug._DEFAULT_SOURCE_DIR = default_dir
        path, source = _resolve_source_dir()
        self.assertEqual(path, default_dir)
        self.assertEqual(source, "default")

    def test_returns_none_none_when_nothing_resolves(self):
        # Env unset, default dir doesn't contain server.ts.
        telegram_debug._DEFAULT_SOURCE_DIR = self.tmp_path / "nonexistent"
        path, source = _resolve_source_dir()
        self.assertIsNone(path)
        self.assertIsNone(source)

    def test_default_dir_exists_but_no_server_ts(self):
        # Directory present but file absent — still a skip, not a crash.
        default_dir = self.tmp_path / "default_source"
        default_dir.mkdir()
        telegram_debug._DEFAULT_SOURCE_DIR = default_dir
        path, source = _resolve_source_dir()
        self.assertIsNone(path)
        self.assertIsNone(source)


class TestDoctorCheckDeploy(unittest.TestCase):
    """Tests for the DEPLOY section of the doctor.

    Patches `_resolve_source_dir` and `_find_plugin_server_ts` at the module
    level to avoid touching the real filesystem / plugin cache. The goal is
    to verify the branching logic, not the sha256 implementation — that's
    covered by the file itself being `hashlib.sha256` of `read_bytes()`.
    """

    def setUp(self):
        self._orig_resolve = telegram_debug._resolve_source_dir
        self._orig_find_plugin = telegram_debug._find_plugin_server_ts
        self._orig_file_hash = telegram_debug._file_hash

    def tearDown(self):
        telegram_debug._resolve_source_dir = self._orig_resolve
        telegram_debug._find_plugin_server_ts = self._orig_find_plugin
        telegram_debug._file_hash = self._orig_file_hash

    def _patch(
        self,
        resolve_return,
        plugin_return,
        hash_map: dict,
    ):
        telegram_debug._resolve_source_dir = lambda: resolve_return
        telegram_debug._find_plugin_server_ts = lambda: plugin_return
        telegram_debug._file_hash = lambda p: hash_map.get(str(p))

    def test_match_emits_ok_no_failure(self):
        src_dir = Path("/fake/source")
        src_ts = src_dir / "server.ts"
        plugin_ts = Path("/fake/plugin/server.ts")
        self._patch(
            resolve_return=(src_dir, "default"),
            plugin_return=(plugin_ts, "abc123"),
            hash_map={str(src_ts): "abc123", str(plugin_ts): "abc123"},
        )
        report = DoctorReport()
        _doctor_check_deploy(report)
        self.assertEqual(report.failures, 0)
        joined = "\n".join(report.lines)
        self.assertIn("plugin cache matches source", joined)
        self.assertIn("abc123", joined)

    def test_drift_fails_doctor(self):
        src_dir = Path("/fake/source")
        src_ts = src_dir / "server.ts"
        plugin_ts = Path("/fake/plugin/server.ts")
        self._patch(
            resolve_return=(src_dir, "default"),
            plugin_return=(plugin_ts, "cafebabe"),
            hash_map={str(src_ts): "deadbeef", str(plugin_ts): "cafebabe"},
        )
        report = DoctorReport()
        _doctor_check_deploy(report)
        # Drift must flip the doctor to non-zero exit — this is the whole
        # point of making the check auto-run. A silent warn() would be a
        # regression of the 2026-04-14 incident.
        self.assertEqual(report.failures, 1)
        joined = "\n".join(report.lines)
        self.assertIn("DRIFT", joined)
        self.assertIn("deadbeef", joined)
        self.assertIn("cafebabe", joined)
        self.assertIn("cp", joined)

    def test_no_source_resolvable_degrades_to_note(self):
        plugin_ts = Path("/fake/plugin/server.ts")
        self._patch(
            resolve_return=(None, None),
            plugin_return=(plugin_ts, "abc123"),
            hash_map={str(plugin_ts): "abc123"},
        )
        report = DoctorReport()
        _doctor_check_deploy(report)
        # Legacy skipped path: no failure, but also no ok line — just a note.
        self.assertEqual(report.failures, 0)
        joined = "\n".join(report.lines)
        self.assertIn("plugin cache", joined)
        self.assertIn("no source dir resolvable", joined)

    def test_env_set_but_missing_degrades_with_pointed_message(self):
        plugin_ts = Path("/fake/plugin/server.ts")
        # env was set, but _resolve returned None path with "env" marker —
        # the doctor should call that out loudly instead of pretending the
        # check ran.
        self._patch(
            resolve_return=(None, "env"),
            plugin_return=(plugin_ts, "abc123"),
            hash_map={str(plugin_ts): "abc123"},
        )
        report = DoctorReport()
        _doctor_check_deploy(report)
        self.assertEqual(report.failures, 0)
        joined = "\n".join(report.lines)
        self.assertIn("TELEGRAM_SOURCE_DIR set but missing server.ts", joined)

    def test_plugin_cache_missing_but_source_present_warns(self):
        src_dir = Path("/fake/source")
        src_ts = src_dir / "server.ts"
        self._patch(
            resolve_return=(src_dir, "default"),
            plugin_return=None,
            hash_map={str(src_ts): "deadbeef"},
        )
        report = DoctorReport()
        _doctor_check_deploy(report)
        # Plugin missing is a warn, not a fail — someone running the doctor
        # on a machine without the plugin installed shouldn't get a red X.
        self.assertEqual(report.failures, 0)
        joined = "\n".join(report.lines)
        self.assertIn("no plugin-cache server.ts", joined)

    def test_both_missing_warns_with_help_text(self):
        self._patch(
            resolve_return=(None, None),
            plugin_return=None,
            hash_map={},
        )
        report = DoctorReport()
        _doctor_check_deploy(report)
        self.assertEqual(report.failures, 0)
        joined = "\n".join(report.lines)
        self.assertIn("no plugin-cache server.ts", joined)
        self.assertIn("TELEGRAM_SOURCE_DIR", joined)


class TestCheckPluginDeployJson(unittest.TestCase):
    """Tests for the structured `deploy` block emitted by check_plugin_deploy.

    This is the block --json mode exposes for watchdog parsing. Verifies
    field shape and severity classification — actual filesystem paths are
    stubbed.
    """

    def setUp(self):
        self._orig_plugin_dir = telegram_debug.PLUGIN_DIR
        self._orig_resolve = telegram_debug._resolve_source_dir
        self._orig_file_hash = telegram_debug._file_hash
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        telegram_debug.PLUGIN_DIR = self._orig_plugin_dir
        telegram_debug._resolve_source_dir = self._orig_resolve
        telegram_debug._file_hash = self._orig_file_hash
        self.tmp.cleanup()

    def _make_plugin_cache(self, version: str = "0.0.6") -> Path:
        """Create a fake plugin cache layout: PLUGIN_DIR/<ver>/server.ts."""
        cache_root = self.tmp_path / "cache"
        cache_root.mkdir()
        ver_dir = cache_root / version
        ver_dir.mkdir()
        (ver_dir / "server.ts").write_text("// plugin cache content")
        telegram_debug.PLUGIN_DIR = cache_root
        return ver_dir / "server.ts"

    def test_deploy_block_shape_on_match(self):
        plugin_ts = self._make_plugin_cache()
        src_dir = self.tmp_path / "src"
        src_dir.mkdir()
        src_ts = src_dir / "server.ts"
        src_ts.write_text("// source")
        telegram_debug._resolve_source_dir = lambda: (src_dir, "env")
        telegram_debug._file_hash = lambda p: (
            "samehash" if str(p) in (str(plugin_ts), str(src_ts)) else None
        )
        result = check_plugin_deploy()
        self.assertIn("deploy", result)
        deploy = result["deploy"]
        self.assertEqual(deploy["plugin_cache_sha256"], "samehash")
        self.assertEqual(deploy["source_sha256"], "samehash")
        self.assertEqual(deploy["source_source"], "env")
        self.assertFalse(deploy["drift_detected"])
        self.assertEqual(deploy["severity"], "ok")
        self.assertEqual(deploy["plugin_cache_path"], str(plugin_ts))
        self.assertEqual(deploy["source_path"], str(src_ts))

    def test_deploy_block_severity_error_on_drift(self):
        plugin_ts = self._make_plugin_cache()
        src_dir = self.tmp_path / "src"
        src_dir.mkdir()
        src_ts = src_dir / "server.ts"
        src_ts.write_text("// source")
        telegram_debug._resolve_source_dir = lambda: (src_dir, "default")
        telegram_debug._file_hash = lambda p: (
            "srchash"
            if str(p) == str(src_ts)
            else "plughash"
            if str(p) == str(plugin_ts)
            else None
        )
        result = check_plugin_deploy()
        deploy = result["deploy"]
        self.assertTrue(deploy["drift_detected"])
        self.assertEqual(deploy["severity"], "error")
        self.assertEqual(deploy["source_source"], "default")

    def test_deploy_block_skipped_when_no_source(self):
        plugin_ts = self._make_plugin_cache()
        telegram_debug._resolve_source_dir = lambda: (None, None)
        telegram_debug._file_hash = lambda p: (
            "plughash" if str(p) == str(plugin_ts) else None
        )
        result = check_plugin_deploy()
        deploy = result["deploy"]
        self.assertEqual(deploy["severity"], "skipped")
        self.assertFalse(deploy["drift_detected"])
        self.assertEqual(deploy["plugin_cache_sha256"], "plughash")
        self.assertIsNone(deploy["source_sha256"])
        self.assertIsNone(deploy["source_source"])

    def test_deploy_block_skipped_when_plugin_dir_missing(self):
        telegram_debug.PLUGIN_DIR = self.tmp_path / "nonexistent"
        telegram_debug._resolve_source_dir = lambda: (None, None)
        result = check_plugin_deploy()
        deploy = result["deploy"]
        self.assertEqual(deploy["severity"], "skipped")
        self.assertIsNone(deploy["plugin_cache_path"])
        self.assertIsNone(deploy["plugin_cache_sha256"])


if __name__ == "__main__":
    unittest.main()
