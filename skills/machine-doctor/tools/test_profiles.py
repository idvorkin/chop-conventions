"""Unit tests for profiles: known-leak findings over seeded HostFacts."""

import unittest

from md_probe import ProcSample
from profiles import (
    PROFILES,
    USER_SOCKETS,
    HostFacts,
    classify_dolt,
    is_watchdog,
)


def _proc(pid, cpu=0.0, rss=0, comm="x"):
    return ProcSample(pid=pid, ppid=1, comm=comm, cpu_pct=cpu, rss_kb=rss, etime_s=1)


def _msgs(findings):
    return " | ".join(f.message for f in findings)


class TestPortedClassifiers(unittest.TestCase):
    def test_city_scope(self):
        self.assertEqual(classify_dolt("/home/u/city/.gc/runtime/packs/dolt"), "city")

    def test_beads_repo(self):
        self.assertEqual(classify_dolt("/home/u/gits/proj/.beads/dolt"), "beads-repo")

    def test_unknown_and_empty(self):
        self.assertEqual(classify_dolt("/tmp/elsewhere"), "unknown")
        self.assertEqual(classify_dolt(""), "unknown")

    def test_watchdog(self):
        self.assertTrue(is_watchdog("/opt/gc __gc-managed-dolt-scope-watchdog /c/x.yaml"))
        self.assertFalse(is_watchdog("/opt/gc supervisor run"))

    def test_user_sockets(self):
        self.assertIn("default", USER_SOCKETS)
        self.assertIn("ssh", USER_SOCKETS)
        self.assertNotIn("my-city", USER_SOCKETS)


class TestGenericProfile(unittest.TestCase):
    def test_clean_facts_no_findings(self):
        self.assertEqual(PROFILES["generic"](HostFacts()), [])

    def test_hot_process_is_flagged(self):
        facts = HostFacts(procs=[_proc(9, cpu=450.0, comm="cc1")])
        out = PROFILES["generic"](facts)
        self.assertIn("cc1", _msgs(out))

    def test_memory_pressure_is_a_fail(self):
        facts = HostFacts(mem_total_kb=1000, mem_avail_kb=50)
        out = PROFILES["generic"](facts)
        self.assertTrue(any(f.severity == "fail" for f in out))

    def test_zombies_flagged(self):
        out = PROFILES["generic"](HostFacts(zombies=[4, 5]))
        self.assertIn("zombie", _msgs(out))


class TestGascityProfile(unittest.TestCase):
    def test_watchdog_is_a_fail(self):
        facts = HostFacts(cmdlines={7: "gc __gc-managed-dolt-scope-watchdog x"})
        out = PROFILES["gascity"](facts)
        self.assertTrue(any(f.severity == "fail" and "watchdog" in f.message for f in out))

    def test_city_dolt_is_a_fail_beads_repo_is_not(self):
        facts = HostFacts(dolt_cwds={1: "/c/.gc/runtime/dolt", 2: "/r/.beads/dolt"})
        out = PROFILES["gascity"](facts)
        joined = _msgs(out)
        self.assertIn("city dolt", joined)
        self.assertNotIn(".beads", joined)  # bd-owned repo store is never a leak

    def test_orphan_tmux_is_a_fail_with_kill_hint(self):
        facts = HostFacts(orphan_tmux={5: "my-city"})
        out = PROFILES["gascity"](facts)
        self.assertIn("tmux -L my-city kill-server", _msgs(out))

    def test_gascity_includes_generic_checks(self):
        facts = HostFacts(mem_total_kb=1000, mem_avail_kb=50)
        self.assertTrue(PROFILES["gascity"](facts))


if __name__ == "__main__":
    unittest.main()
