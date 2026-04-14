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

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from watchdog import (  # noqa: E402
    find_ancestor_pane,
    parse_proc_stat,
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


if __name__ == "__main__":
    unittest.main()
