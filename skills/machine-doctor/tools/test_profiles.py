"""Unit tests for profiles: known-leak findings over seeded HostFacts."""

import unittest

from md_probe import ProcSample
from profiles import (
    PROFILES,
    USER_SOCKETS,
    HostFacts,
    STALE_JEKYLL_S,
    classify_dolt,
    is_deleted_cwd,
    is_jekyll_server,
    is_watchdog,
)


def _proc(pid, cpu=0.0, rss=0, comm="x", etime=1):
    return ProcSample(
        pid=pid, ppid=1, comm=comm, cpu_pct=cpu, rss_kb=rss, etime_s=etime
    )


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
        self.assertTrue(
            is_watchdog("/opt/gc __gc-managed-dolt-scope-watchdog /c/x.yaml")
        )
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

    def test_deleted_cwd_server_flagged_shell_ignored(self):
        facts = HostFacts(
            procs=[_proc(20, comm="ruby"), _proc(21, comm="bash")],
            deleted_cwds={20: "/w/wt1 (deleted)", 21: "/w/wt1 (deleted)"},
        )
        joined = _msgs(PROFILES["generic"](facts))
        self.assertIn("pid=20", joined)
        self.assertNotIn("pid=21", joined)  # a person's shell pane, not a leak

    def test_stale_jekyll_flagged_fresh_one_not(self):
        facts = HostFacts(
            procs=[
                _proc(30, rss=200 * 1024, comm="bundle", etime=STALE_JEKYLL_S + 1),
                _proc(31, comm="bundle", etime=60),
            ],
            jekyll_pids=[30, 31],
        )
        joined = _msgs(PROFILES["generic"](facts))
        self.assertIn("pid=30", joined)
        self.assertIn("SIGINT", joined)
        self.assertNotIn("pid=31", joined)


class TestLeakClassifiers(unittest.TestCase):
    def test_deleted_cwd(self):
        self.assertTrue(is_deleted_cwd("/home/u/wt/7 (deleted)"))
        self.assertFalse(is_deleted_cwd("/home/u/wt/7"))
        self.assertFalse(is_deleted_cwd(""))

    def test_jekyll_server_not_its_wrappers(self):
        cl = "/h/.bundle/ruby/4.0.0/bin/jekyll serve --port 4023"
        self.assertTrue(is_jekyll_server("bundle", cl))
        self.assertFalse(is_jekyll_server("bash", "bash -c 'jekyll serve --port 4023'"))
        self.assertFalse(is_jekyll_server("just", "just jekyll-serve 4015"))
        self.assertFalse(
            is_jekyll_server("bundle", "/h/bin/jekyll build")
        )  # one-shot, exits


class TestGascityProfile(unittest.TestCase):
    def test_watchdog_is_a_fail(self):
        facts = HostFacts(cmdlines={7: "gc __gc-managed-dolt-scope-watchdog x"})
        out = PROFILES["gascity"](facts)
        self.assertTrue(
            any(f.severity == "fail" and "watchdog" in f.message for f in out)
        )

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


class TestHostHealth(unittest.TestCase):
    def _find(self, **kw):
        facts = HostFacts(
            mem_total_kb=16 * 1024 * 1024, mem_avail_kb=8 * 1024 * 1024, **kw
        )
        return PROFILES["generic"](facts)

    def test_quiet_host_has_no_findings(self):
        self.assertEqual(self._find(pressure="normal", disks={"/": 40}), [])

    def test_pressure_warn_and_critical(self):
        warn = self._find(pressure="warn", compressor_kb=6 * 1024 * 1024)
        self.assertEqual([f.severity for f in warn], ["warn"])
        self.assertIn("compressor holds 6144MB", _msgs(warn))
        self.assertEqual(
            [f.severity for f in self._find(pressure="critical")], ["fail"]
        )

    def test_swap_nearly_full(self):
        got = self._find(swap_total_kb=4 * 1024 * 1024, swap_free_kb=300 * 1024)
        self.assertIn("swap 92% used", _msgs(got))

    def test_disk_thresholds(self):
        got = self._find(disks={"/": 91, "/Volumes/x": 96, "/Volumes/y": 50})
        self.assertEqual(
            sorted((f.severity, f.message) for f in got),
            [("fail", "disk /Volumes/x is 96% full"), ("warn", "disk / is 91% full")],
        )

    def test_vm_allowed_most_of_host_ram(self):
        got = self._find(vm_mem_mib={"OrbStack VM": 12288})
        self.assertIn("75%", _msgs(got))
        self.assertIn("orb config set memory_mib 8192", _msgs(got))
        self.assertEqual(self._find(vm_mem_mib={"OrbStack VM": 8192}), [])

    def test_windowserver_leak(self):
        got = self._find(procs=[_proc(436, rss=3 * 1024 * 1024, comm="WindowServer")])
        self.assertIn("WindowServer footprint 3072MB", _msgs(got))

    def test_thermal_and_forced_sleeps(self):
        from md_probe import SleepEvent

        got = self._find(
            speed_limit=70,
            sleeps=[
                SleepEvent("2026-09-26 21:00:00", "Idle Sleep", None),
                SleepEvent("2026-09-26 22:22:03", "Software Sleep pid=40700", 40700),
            ],
        )
        msgs = _msgs(got)
        self.assertIn("CPU speed limited to 70%", msgs)
        self.assertIn("1 forced/thermal sleep(s)", msgs)
        self.assertIn("22:22:03", msgs)
