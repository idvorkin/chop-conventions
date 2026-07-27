"""Unit tests for gascity_doctor pure logic.

Run from this directory:
    python3 -m unittest test_gascity_doctor.py

Imports the pure-function layer only — no `uv`, no typer, no subprocess.
"""

import unittest

from gascity_doctor import (
    USER_SOCKETS,
    State,
    classify_dolt,
    diff_state,
    is_watchdog,
    parse_loadavg,
    parse_vmstat_idle,
    redact,
    render_snapshot,
)


class TestUserSockets(unittest.TestCase):
    def test_humans_own_sockets_are_excluded(self):
        """Flagging the user's own tmux sockets trains them to ignore the tool."""
        self.assertIn("default", USER_SOCKETS)
        self.assertIn("ssh", USER_SOCKETS)

    def test_city_socket_is_not_excluded(self):
        self.assertNotIn("my-city", USER_SOCKETS)


class TestRedact(unittest.TestCase):
    def test_redacts_api_key(self):
        cl = "tmux -u -L my-city new-session -d -s mayor -e ANTHROPIC_API_KEY=sk-ant-secret123"
        got = redact(cl)
        self.assertNotIn("sk-ant-secret123", got)
        self.assertIn("ANTHROPIC_API_KEY=<redacted>", got)

    def test_redacts_multiple_and_varied_names(self):
        cl = "cmd -e OPENAI_API_KEY=aaa -e GH_TOKEN=bbb -e DB_PASSWORD=ccc"
        got = redact(cl)
        for secret in ("aaa", "bbb", "ccc"):
            self.assertNotIn(secret, got)

    def test_leaves_benign_env_alone(self):
        cl = "cmd -e BEADS_ACTOR=mayor -e GC_CITY=/home/u/city"
        self.assertEqual(redact(cl), cl)


class TestClassifyDolt(unittest.TestCase):
    def test_city_scope(self):
        self.assertEqual(classify_dolt("/home/u/city/.gc/runtime/packs/dolt"), "city")

    def test_beads_repo(self):
        self.assertEqual(classify_dolt("/home/u/gits/proj/.beads/dolt"), "beads-repo")

    def test_unknown_and_empty(self):
        self.assertEqual(classify_dolt("/tmp/elsewhere"), "unknown")
        self.assertEqual(classify_dolt(""), "unknown")


class TestIsWatchdog(unittest.TestCase):
    def test_matches_watchdog(self):
        self.assertTrue(is_watchdog("/opt/gc __gc-managed-dolt-scope-watchdog /c/.gc/x.yaml"))

    def test_plain_gc_is_not_watchdog(self):
        self.assertFalse(is_watchdog("/opt/gc supervisor run"))


class TestParsers(unittest.TestCase):
    def test_loadavg(self):
        self.assertEqual(parse_loadavg("1.50 0.90 0.55 2/742 123"), (1.50, 0.90, 0.55))

    def test_vmstat_idle(self):
        text = (
            "procs -----------memory----------\n"
            " r  b   swpd   free  buff cache si so bi bo in cs us sy id wa st\n"
            " 0  0      0 655696    28 800508 14  0 66 12 35 78  5  2 92  0  0\n"
        )
        self.assertEqual(parse_vmstat_idle(text), 92)

    def test_vmstat_idle_malformed_returns_none(self):
        self.assertIsNone(parse_vmstat_idle(""))
        self.assertIsNone(parse_vmstat_idle("too few fields here\n"))


class TestStateRunning(unittest.TestCase):
    def test_empty_state_not_running(self):
        self.assertFalse(State().running)

    def test_watchdog_alone_counts_as_running(self):
        """The whole point: gc cities says 'none' but a watchdog is still up."""
        self.assertTrue(State(watchdog_pids=[42]).running)

    def test_orphan_tmux_alone_counts_as_running(self):
        self.assertTrue(State(orphan_tmux={7: "my-city"}).running)

    def test_beads_repo_dolt_does_not_count(self):
        """A bd-spawned repo store is not Gas City's problem."""
        st = State(dolt={9: "/home/u/gits/proj/.beads/dolt"})
        self.assertFalse(st.running)

    def test_city_dolt_does_count(self):
        st = State(dolt={9: "/home/u/city/.gc/runtime/packs/dolt"})
        self.assertTrue(st.running)


class TestDiffState(unittest.TestCase):
    def test_no_change_is_silent(self):
        st = State(dolt={1: "/x/.beads/dolt"})
        self.assertEqual(diff_state(st, st), [])

    def test_reports_start(self):
        events = diff_state(State(), State(gc_pids=[10]))
        self.assertTrue(any("GAS CITY RUNNING" in e for e in events))

    def test_reports_stop(self):
        events = diff_state(State(gc_pids=[10]), State())
        self.assertTrue(any("fully stopped" in e for e in events))

    def test_reports_new_watchdog(self):
        events = diff_state(State(), State(watchdog_pids=[99]))
        joined = " ".join(events)
        self.assertIn("ORPHANED DOLT WATCHDOG", joined)
        self.assertIn("99", joined)

    def test_reports_new_orphan_tmux(self):
        events = diff_state(State(), State(orphan_tmux={5: "my-city"}))
        self.assertTrue(any("ORPHANED CITY TMUX SERVER" in e for e in events))

    def test_reports_new_dolt_with_classification(self):
        events = diff_state(State(), State(dolt={3: "/home/u/gits/p/.beads/dolt"}))
        self.assertTrue(any("beads-repo" in e for e in events))

    def test_departed_dolt_is_not_noisy(self):
        """Things going away shouldn't alarm; only arrivals and start/stop do."""
        events = diff_state(State(dolt={3: "/x/.beads/dolt"}), State())
        self.assertEqual(events, [])


class TestRenderSnapshot(unittest.TestCase):
    def test_clean_state_has_no_failures(self):
        _, failures = render_snapshot(State(idle_pct=95))
        self.assertEqual(failures, 0)

    def test_watchdog_is_a_failure(self):
        text, failures = render_snapshot(State(watchdog_pids=[1], idle_pct=95))
        self.assertGreaterEqual(failures, 1)
        self.assertIn("orphaned dolt watchdog", text)

    def test_beads_repo_dolt_is_not_a_failure(self):
        _, failures = render_snapshot(
            State(dolt={2: "/home/u/gits/p/.beads/dolt"}, idle_pct=95)
        )
        self.assertEqual(failures, 0)

    def test_high_load_with_idle_cpu_is_not_flagged(self):
        """The load-vs-idle trap: load 26 with 92% idle is queueing, not CPU."""
        text, failures = render_snapshot(State(load1=26.0, idle_pct=92))
        self.assertEqual(failures, 0)
        self.assertIn("queueing, not CPU", text)


if __name__ == "__main__":
    unittest.main()
