#!/usr/bin/env python3
"""Unit tests for watchdog.py pure helpers.

Focus: the pane-resolution logic that walks the parent-process chain to find
the tmux pane containing the caller. The real bug (fixed 2026-04-14) was that
`reload` used unscoped `tmux display-message -p '#{pane_id}'` from a
backgrounded, disowned watchdog subprocess — by then `TMUX_PANE` was stale
and display-message fell back to the session's most-recently-active pane,
which with multiple concurrent Claude sessions was routinely the wrong one.
Parent-chain walk via /proc/<pid>/stat derives the answer from the kernel
process tree, so it's deterministic regardless of env-var hygiene.

Run with: python3 -m unittest test_watchdog.py
"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Import from the canonical package location — the in-tree shim at
# `tools/watchdog.py` is deprecated and exists only for back-compat. Tests
# must target the real module so `mock.patch("chop_telegram_tools.watchdog.
# <name>", …)` hits the same reference `resolve_pane_for_pid()` reads.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chop_telegram_tools.watchdog import (  # noqa: E402
    _FALLBACK,
    _resolve_pane_via_rmux_helper,
    find_ancestor_pane,
    parse_proc_stat,
    resolve_pane_for_pid,
)


def make_stat_reader(table: dict[int, tuple[str, int]]):
    """Build a fake stat reader from a {pid: (comm, ppid)} table."""

    def reader(pid: int):
        return table.get(pid)

    return reader


class TestParseProcStat(unittest.TestCase):
    def test_simple_comm(self):
        self.assertEqual(
            parse_proc_stat("123 (bash) S 99 123 99 ...\n"),
            ("bash", 99),
        )

    def test_comm_with_space(self):
        self.assertEqual(
            parse_proc_stat("123 (my proc) S 99 123 99 ...\n"),
            ("my proc", 99),
        )

    def test_comm_with_inner_parens(self):
        # Anchor on rfind(')') so names like "(weird)" don't truncate.
        self.assertEqual(
            parse_proc_stat("123 (weird )name) S 99 123 99 ...\n"),
            ("weird )name", 99),
        )

    def test_missing_parens_returns_none(self):
        self.assertIsNone(parse_proc_stat("123 bad line\n"))

    def test_truncated_after_comm_returns_none(self):
        self.assertIsNone(parse_proc_stat("123 (bash)"))

    def test_non_numeric_ppid_returns_none(self):
        self.assertIsNone(parse_proc_stat("123 (bash) S ??? 123 99 ...\n"))


class TestFindAncestorPane(unittest.TestCase):
    """The core fix. Each test mirrors a real scenario from the 2026-04-14 bug."""

    def test_larry_scenario_finds_correct_pane(self):
        # Real scenario: watchdog spawned inside Larry's Claude session at
        # pane %35 (shell pid 2594534, pts/7). Another Claude at pane %65
        # (shell pid 331460, pts/4) is also running on the same box. The
        # watchdog's pid is 3000000, parent is bash 2800000, whose parent is
        # the bun server 2700000, whose parent is Claude 2594600, whose
        # parent is the pane-35 shell 2594534. Parent-chain walk should
        # land on %35, NOT %65.
        table = {
            3000000: ("python3", 2800000),
            2800000: ("bash", 2700000),
            2700000: ("bun", 2594600),
            2594600: ("claude", 2594534),
            2594534: ("zsh", 1),
            # Unrelated Claude session tree — must NOT match.
            331460: ("zsh", 1),
        }
        pane_pids = {
            2594534: "%35",
            331460: "%65",
        }
        self.assertEqual(
            find_ancestor_pane(3000000, pane_pids, stat_reader=make_stat_reader(table)),
            "%35",
        )

    def test_caller_is_the_shell_itself(self):
        # Edge case: the caller's own pid IS a pane shell (e.g., running
        # from the interactive shell directly). Include-self is the
        # documented behavior.
        table = {99: ("zsh", 1)}
        self.assertEqual(
            find_ancestor_pane(99, {99: "%7"}, stat_reader=make_stat_reader(table)),
            "%7",
        )

    def test_no_ancestor_in_tmux(self):
        # Process chain walks up to init but never passes through a tmux
        # pane shell. Returns None so the caller falls back gracefully.
        table = {
            100: ("python3", 50),
            50: ("bash", 1),
        }
        pane_pids = {999: "%1", 888: "%2"}
        self.assertIsNone(
            find_ancestor_pane(100, pane_pids, stat_reader=make_stat_reader(table))
        )

    def test_empty_pane_map_returns_none(self):
        # tmux not running, or list-panes failed.
        table = {100: ("bash", 1)}
        self.assertIsNone(
            find_ancestor_pane(100, {}, stat_reader=make_stat_reader(table))
        )

    def test_nearest_pane_wins_on_nested_tmux(self):
        # Nested tmux: outer pane shell at pid 10, inner tmux server spawns
        # inner pane shell at pid 20 which is a child (eventually) of 10.
        # The caller at pid 100 is inside the inner pane — should resolve
        # to the inner pane, not the outer one.
        table = {
            100: ("python3", 20),
            20: ("bash", 15),
            15: ("tmux", 10),
            10: ("bash", 1),
        }
        pane_pids = {20: "%inner", 10: "%outer"}
        self.assertEqual(
            find_ancestor_pane(100, pane_pids, stat_reader=make_stat_reader(table)),
            "%inner",
        )

    def test_process_vanished_mid_walk(self):
        # A parent exited between `list_tmux_pane_pids()` and our stat read.
        # Return None rather than crash.
        table = {
            200: ("bash", 150),
            # 150 missing — race
        }
        self.assertIsNone(
            find_ancestor_pane(200, {99: "%1"}, stat_reader=make_stat_reader(table))
        )

    def test_loop_guard(self):
        # Impossible in practice but guard against pid-reuse cycles.
        table = {
            10: ("a", 20),
            20: ("b", 10),
        }
        self.assertIsNone(
            find_ancestor_pane(10, {999: "%1"}, stat_reader=make_stat_reader(table))
        )

    def test_stops_at_init(self):
        # Walking up hits init (pid 1) without finding a pane — None.
        table = {
            100: ("bash", 1),
        }
        self.assertIsNone(
            find_ancestor_pane(100, {999: "%1"}, stat_reader=make_stat_reader(table))
        )

    def test_max_depth_terminates(self):
        # Pathologically deep chain (should still terminate and return None).
        table = {i: ("proc", i - 1) for i in range(2, 200)}
        # No entry matches any pane pid.
        self.assertIsNone(
            find_ancestor_pane(
                199, {9999: "%1"}, stat_reader=make_stat_reader(table), max_depth=50
            )
        )


def _completed_process(returncode: int, stdout: str = "", stderr: str = ""):
    """Build a fake CompletedProcess for subprocess.run mocks."""
    return subprocess.CompletedProcess(
        args=["rmux_helper", "parent-pid-tree", "--pid", "999"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestResolvePaneViaRmuxHelper(unittest.TestCase):
    """Unit tests for the rmux_helper subprocess path.

    The primary path of ``resolve_pane_for_pid`` shells out to
    ``rmux_helper parent-pid-tree --pid <pid>``. These tests mock
    ``subprocess.run`` to cover each exit-code branch so we don't
    depend on rmux_helper being installed in the test environment.
    """

    def test_happy_path_returns_pane_id(self):
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            return_value=_completed_process(0, stdout="%35\n"),
        ):
            self.assertEqual(_resolve_pane_via_rmux_helper(999), "%35")

    def test_exit_1_returns_none_not_sentinel(self):
        # Exit 1 is a definitive "no pane in ancestor chain" — caller
        # must NOT fall back, the answer is None.
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            return_value=_completed_process(
                1, stderr="no tmux pane found for pid 999\n"
            ),
        ):
            self.assertIsNone(_resolve_pane_via_rmux_helper(999))

    def test_file_not_found_returns_fallback(self):
        # rmux_helper not on PATH → fall back to Python walker.
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            side_effect=FileNotFoundError("rmux_helper"),
        ):
            self.assertIs(_resolve_pane_via_rmux_helper(999), _FALLBACK)

    def test_timeout_returns_fallback(self):
        # rmux_helper hangs → fall back to Python walker.
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="rmux_helper", timeout=2.0),
        ):
            self.assertIs(_resolve_pane_via_rmux_helper(999), _FALLBACK)

    def test_exit_2_returns_fallback(self):
        # Exit 2 = tmux not running. Fall back — rmux_helper's tmux
        # detection may be wrong on this box.
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            return_value=_completed_process(2, stderr="tmux not running\n"),
        ):
            self.assertIs(_resolve_pane_via_rmux_helper(999), _FALLBACK)

    def test_exit_3_returns_fallback(self):
        # Exit 3 = /proc unreadable. Fall back.
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            return_value=_completed_process(3, stderr="/proc unreadable\n"),
        ):
            self.assertIs(_resolve_pane_via_rmux_helper(999), _FALLBACK)

    def test_empty_stdout_on_exit_0_returns_fallback(self):
        # Exit 0 with empty stdout is malformed — fall back defensively.
        with mock.patch(
            "chop_telegram_tools.watchdog.subprocess.run",
            return_value=_completed_process(0, stdout="\n"),
        ):
            self.assertIs(_resolve_pane_via_rmux_helper(999), _FALLBACK)


class TestResolvePaneForPid(unittest.TestCase):
    """Integration tests for the two-tier resolver.

    ``resolve_pane_for_pid`` tries ``rmux_helper`` first and only falls
    back to the Python walker on sentinel responses. These tests verify
    the dispatch logic between the two paths.
    """

    def test_falls_back_to_python_walker_when_rmux_helper_missing(self):
        with (
            mock.patch(
                "chop_telegram_tools.watchdog.subprocess.run",
                side_effect=FileNotFoundError("rmux_helper"),
            ),
            mock.patch(
                "chop_telegram_tools.watchdog._resolve_pane_via_python_walker",
                return_value="%99",
            ) as mock_walker,
        ):
            self.assertEqual(resolve_pane_for_pid(999), "%99")
            mock_walker.assert_called_once_with(999)

    def test_primary_path_wins_python_walker_not_called(self):
        # When rmux_helper succeeds, the Python walker must never run.
        # Mock it to raise so the test fails loudly if the fallback is
        # invoked by mistake.
        with (
            mock.patch(
                "chop_telegram_tools.watchdog.subprocess.run",
                return_value=_completed_process(0, stdout="%35\n"),
            ),
            mock.patch(
                "chop_telegram_tools.watchdog._resolve_pane_via_python_walker",
                side_effect=AssertionError(
                    "fallback must not be called on primary success"
                ),
            ) as mock_walker,
        ):
            self.assertEqual(resolve_pane_for_pid(999), "%35")
            mock_walker.assert_not_called()

    def test_exit_1_does_not_fall_back(self):
        # Exit 1 from rmux_helper is a definitive "no match". The Python
        # walker must NOT be called — if it were, the two implementations
        # could disagree on the same process tree.
        with (
            mock.patch(
                "chop_telegram_tools.watchdog.subprocess.run",
                return_value=_completed_process(1),
            ),
            mock.patch(
                "chop_telegram_tools.watchdog._resolve_pane_via_python_walker",
                return_value="%99",
            ) as mock_walker,
        ):
            self.assertIsNone(resolve_pane_for_pid(999))
            mock_walker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
