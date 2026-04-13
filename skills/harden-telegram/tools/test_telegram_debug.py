#!/usr/bin/env python3
"""Unit tests for telegram_debug.py pure functions.

Run with: python3 -m unittest test_telegram_debug.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telegram_debug import (  # noqa: E402
    _find_owning_claude,
    classify_bridges,
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
        self.assertEqual(_find_owning_claude(100, stat_reader=make_stat_reader(table)), 99)

    def test_grandchild_through_shell(self):
        table = {
            200: ("python3", 150),
            150: ("bash", 99),
            99: ("claude", 1),
        }
        self.assertEqual(_find_owning_claude(200, stat_reader=make_stat_reader(table)), 99)

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
        self.assertEqual(_find_owning_claude(300, stat_reader=make_stat_reader(table)), 250)

    def test_matches_claude_code_shim(self):
        # Linux truncates comm to 15 chars; launchers like `claude-code` or
        # `claude-1m` show up as-is and must still be recognized.
        table = {
            100: ("bun", 99),
            99: ("claude-code", 1),
        }
        self.assertEqual(_find_owning_claude(100, stat_reader=make_stat_reader(table)), 99)

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
            session_subscribed_to_telegram(
                ["claude", "--dangerously-skip-permissions"]
            )
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
        self.assertFalse(
            session_subscribed_to_telegram(["claude", "--channels"])
        )

    def test_empty_argv(self):
        self.assertFalse(session_subscribed_to_telegram([]))


if __name__ == "__main__":
    unittest.main()
