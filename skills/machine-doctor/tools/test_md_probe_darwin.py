"""Unit tests for md_probe's macOS parsers, against fixture text captured from
a real Mac (ps / top / sysctl / vm_stat / pmset / df).

Run from this directory: python3 -m unittest
"""

import unittest

from md_probe import (
    MemInfo,
    SpikeConfig,
    SpikeState,
    cpu_totals_from_ticks,
    detect_spike,
    idle_pct_between,
    AlertState,
    alert_due,
    alert_sent,
    alert_step,
    ease_told,
    hazards,
    parse_catcher_log,
    parse_df,
    parse_pmset_sleeps,
    parse_ps_duration,
    parse_ps_rows,
    parse_speed_limit,
    parse_swapusage,
    parse_thermal_pressure,
    parse_top_mem,
    parse_top_size,
    parse_vm_stat,
    pressure_name,
    select_mounts,
)

PS = """\
    1     0 Ss   145:30.89 10-08:27:49   9072 /sbin/launchd
 1513     1 Rs   3480:00.89 10-08:26:23 1705840 /Applications/OrbStack.app/Contents/Frameworks/OrbStack Helper.app/Contents/MacOS/OrbStack Helper
  777     1 Z      0:00.00    00:05      0 (sh)
garbage line
"""

TOP = """\
Processes: 631 total, 5 running, 626 sleeping, 3121 threads
PhysMem: 15G used (2945M wired, 10G compressor), 204M unused.

PID    MEM
1513   12G+
436    3074M-
99949  5120K
42     0B
"""

VM_STAT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     4675.
Pages occupied by compressor:                 353459.
Swapouts:                                   16256461.
"""

PMSET_LOG = """\
2026-09-26 22:21:58 -0700 Notification        	Display is turned off
2026-09-26 22:22:03 -0700 Sleep               	Entering Sleep state due to 'Software Sleep pid=40700':TCPKeepAlive=active Using AC (Charge:0%) 9 secs
2026-09-26 22:45:23 -0700 Sleep               	Entering Sleep state due to 'Dark Wake Thermal Emergency':TCPKeepAlive=active Using AC (Charge:0%) 191 secs
2026-09-27 05:34:06 -0700 Sleep               	Entering Sleep state due to 'Maintenance Sleep':TCPKeepAlive=active Using AC (Charge:0%) 26 secs
2026-09-20 15:36:03 -0700 Sleep               	Entering Sleep state due to 'Idle Sleep':TCPKeepAlive=active Using AC (Charge:0%) 12 secs
2026-09-27 05:36:35 -0700 Wake                	DarkWake to FullWake from Deep Idle [CDNVA] : due to UserActivity Assertion
"""

DF = """\
Filesystem                     1024-blocks      Used Available Capacity  Mounted on
/dev/disk3s1s1                   239362496  13631488  19922944    41%    /
devfs                                  222       222         0   100%    /dev
/dev/disk3s5                     239362496 185597952  19922944    91%    /System/Volumes/Data
/dev/disk3s2                     239362496   8000000  19922944    35%    /System/Volumes/Preboot
map auto_home                            0         0         0   100%    /System/Volumes/Data/home
/dev/disk5s1                     500000000 420000000  80000000    84%    /Volumes/SD 4G 1
/dev/disk6s1                     500000000 175000000 325000000    35%    /Volumes/SD 4G 1/nested
/dev/disk7s1                       2000000   1960000     40000    98%    /Library/Developer/CoreSimulator/Volumes/iOS_23D8133
"""


class TestDurations(unittest.TestCase):
    def test_ps_time_minutes_past_59(self):
        self.assertAlmostEqual(parse_ps_duration("3480:00.89"), 208800.89)

    def test_etime_with_days(self):
        self.assertEqual(
            parse_ps_duration("10-08:27:49"), 10 * 86400 + 8 * 3600 + 27 * 60 + 49
        )

    def test_short_etime(self):
        self.assertEqual(parse_ps_duration("00:05"), 5)


class TestPsRows(unittest.TestCase):
    def setUp(self):
        self.rows = {r.pid: r for r in parse_ps_rows(PS)}

    def test_skips_garbage(self):
        self.assertEqual(sorted(self.rows), [1, 777, 1513])

    def test_comm_is_basename_and_keeps_spaces(self):
        self.assertEqual(self.rows[1513].comm, "OrbStack Helper")
        self.assertEqual(self.rows[1].comm, "launchd")

    def test_fields(self):
        r = self.rows[1513]
        self.assertEqual((r.ppid, r.state, r.rss_kb), (1, "R", 1705840))
        self.assertAlmostEqual(r.cpu_s, 208800.89)

    def test_zombie_state(self):
        self.assertEqual(self.rows[777].state, "Z")


class TestTopMem(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(parse_top_size("12G+"), 12 * 1024 * 1024)
        self.assertEqual(parse_top_size("3074M-"), 3074 * 1024)
        self.assertEqual(parse_top_size("750K"), 750)
        self.assertEqual(parse_top_size("0B"), 0)
        self.assertIsNone(parse_top_size("n/a"))

    def test_footprints_skip_header_block(self):
        mem = parse_top_mem(TOP)
        self.assertEqual(mem[1513], 12 * 1024 * 1024)
        self.assertEqual(mem[99949], 5120)
        self.assertNotIn(631, mem)  # "Processes: 631 total" is not a pid row


class TestMemoryAndSwap(unittest.TestCase):
    def test_swapusage(self):
        total, free = parse_swapusage(
            "vm.swapusage: total = 8192.00M  used = 6821.75M  free = 1370.25M  (encrypted)"
        )
        self.assertEqual(total, 8192 * 1024)
        self.assertEqual(free, round(1370.25 * 1024))

    def test_vm_stat(self):
        vm = parse_vm_stat(VM_STAT)
        self.assertEqual(vm["page_kb"], 16)
        self.assertEqual(vm["swapouts"], 16256461)
        self.assertEqual(vm["compressor_kb"], 353459 * 16)

    def test_pressure_levels(self):
        self.assertEqual(pressure_name(1), "normal")
        self.assertEqual(pressure_name(2), "warn")
        self.assertEqual(pressure_name(4), "critical")
        self.assertIsNone(pressure_name(None))

    def test_critical_pressure_is_a_spike(self):
        mem = MemInfo(16 << 20, 8 << 20, 0, 0, pressure="critical")
        _, reasons = detect_spike(
            SpikeState(), SpikeConfig(), idle_pct=80, mem=mem, swap_out=0, procs=[]
        )
        self.assertTrue(any("critical" in r for r in reasons))

    def test_warn_pressure_alone_is_not_a_spike(self):
        mem = MemInfo(16 << 20, 8 << 20, 0, 0, pressure="warn")
        _, reasons = detect_spike(
            SpikeState(), SpikeConfig(), idle_pct=80, mem=mem, swap_out=0, procs=[]
        )
        self.assertEqual(reasons, [])


class TestCpuTicks(unittest.TestCase):
    def test_mach_ticks_feed_idle_pct(self):
        a = cpu_totals_from_ticks(user=100, system=100, idle=800, nice=0)
        b = cpu_totals_from_ticks(user=150, system=200, idle=850, nice=0)
        # 200 ticks elapsed, 50 of them idle
        self.assertEqual(idle_pct_between(a, b), 25)


class TestThermal(unittest.TestCase):
    def test_never_throttled(self):
        self.assertIsNone(
            parse_speed_limit("Note: No thermal warning level has been recorded")
        )

    def test_limited(self):
        self.assertEqual(
            parse_speed_limit("CPU_Scheduler_Limit \t= 100\nCPU_Speed_Limit \t= 70"), 70
        )

    def test_thermal_pressure_parsed(self):
        out = "com.apple.system.thermalpressurelevel 2\n"
        self.assertEqual(parse_thermal_pressure(out), 2)
        self.assertIsNone(parse_thermal_pressure(""))

    def _reasons(self, thermal):
        mem = MemInfo(16 << 20, 8 << 20, 0, 0, pressure="normal")
        _, reasons = detect_spike(
            SpikeState(),
            SpikeConfig(),
            idle_pct=80,
            mem=mem,
            swap_out=0,
            procs=[],
            thermal=thermal,
        )
        return reasons

    def test_nominal_is_quiet(self):
        self.assertEqual(self._reasons(0), [])
        self.assertEqual(self._reasons(None), [])

    def test_moderate_spikes(self):
        self.assertEqual(self._reasons(1), ["thermal pressure is moderate"])


def _haz(thermal=None, pressure="normal", disk=None):
    mem = MemInfo(16 << 20, 8 << 20, 0, 0, pressure=pressure)
    return hazards(SpikeConfig(), thermal=thermal, mem=mem, disk_free_gb=disk)


class TestHazards(unittest.TestCase):
    def test_quiet_box_has_none(self):
        self.assertEqual(_haz(thermal=0, disk=45.0), {})

    def test_each_kind(self):
        h = _haz(thermal=2, pressure="critical", disk=0.9)
        self.assertEqual(set(h), {"thermal", "memory", "disk"})
        self.assertEqual(h["thermal"].level, 2)
        self.assertEqual(h["disk"].text, "disk has only 0.9 GB free")

    def test_low_disk_is_not_a_dump_spike(self):
        mem = MemInfo(16 << 20, 8 << 20, 0, 0, pressure="normal")
        _, reasons = detect_spike(
            SpikeState(), SpikeConfig(), idle_pct=80, mem=mem, swap_out=0, procs=[]
        )
        self.assertEqual(reasons, [])

    def test_disk_still_filling_escalates(self):
        self.assertGreater(_haz(disk=4.0)["disk"].level, _haz(disk=18.0)["disk"].level)


class TestAlertDue(unittest.TestCase):
    def due(self, cur, told, now=100, last=90):
        return alert_due(cur, told, now=now, last_sent=last, throttle_s=1800)

    def test_new_hazard_alerts(self):
        self.assertTrue(self.due(_haz(thermal=1), {}))

    def test_known_hazard_waits_for_throttle(self):
        cur = _haz(thermal=1)
        self.assertFalse(self.due(cur, {"thermal": 1}))
        self.assertTrue(self.due(cur, {"thermal": 1}, now=1900))

    def test_heat_is_not_hidden_behind_disk(self):
        cur = _haz(thermal=1, disk=10.0)
        told = {"disk": cur["disk"].level}
        self.assertTrue(self.due(cur, told))

    def test_escalation_alerts_at_once(self):
        self.assertTrue(self.due(_haz(thermal=3), {"thermal": 1}))

    def test_nothing_active_never_alerts(self):
        self.assertFalse(self.due({}, {"thermal": 2}, now=10**6))

    def test_failed_send_retries(self):
        # told only advances on success, so after a failure the hazard is still new
        self.assertTrue(self.due(_haz(thermal=1), {}, now=91))


class TestEaseTold(unittest.TestCase):
    def test_clear_keeps_told_for_recovery(self):
        self.assertEqual(ease_told({"thermal": 2}, {}), {"thermal": 2})

    def test_cleared_kind_is_forgotten_and_eased_kind_lowered(self):
        told = {"thermal": 3, "disk": 1}
        self.assertEqual(ease_told(told, _haz(thermal=1)), {"thermal": 1})

    def test_cleared_then_back_alerts(self):
        told = ease_told({"thermal": 1, "disk": 1}, _haz(disk=19.0))
        self.assertTrue(
            alert_due(
                _haz(thermal=1, disk=19.0), told, now=1, last_sent=0, throttle_s=1800
            )
        )


def _run(ticks, hold_s=180, state=None, t0=0):
    """Drive alert_step over (seconds, hazards) spans at 30 s ticks, every send
    succeeding. Returns (actions as (ts, action), final state)."""
    state, now, out = state or AlertState(), t0, []
    for secs, raw in ticks:
        for _ in range(secs // 30):
            state, action, cur = alert_step(
                state, raw, now=now, hold_s=hold_s, throttle_s=1800
            )
            if action:
                out.append((now, action))
                state = alert_sent(state, action, cur, now)
            now += 30
    return out, state


HEAVY = _haz(thermal=2)
CLEAR = _haz(thermal=0)


class TestAlertHold(unittest.TestCase):
    def test_brief_heat_is_silent(self):
        self.assertEqual(_run([(30, HEAVY), (600, CLEAR)])[0], [])

    def test_heat_past_hold_alerts_once(self):
        actions, _ = _run([(600, HEAVY)])
        self.assertEqual(actions, [(180, "alert")])

    def test_dip_during_heat_neither_recovers_nor_realerts(self):
        actions, _ = _run([(300, HEAVY), (30, CLEAR), (600, HEAVY)])
        self.assertEqual(actions, [(180, "alert")])

    def test_recovery_after_clear_for_hold(self):
        actions, _ = _run([(300, HEAVY), (600, CLEAR)])
        # clear from t=300, so recovered lands on the tick at t=480
        self.assertEqual(actions, [(180, "alert"), (480, "recover")])

    def test_low_disk_alerts_on_first_tick(self):
        actions, _ = _run([(30, _haz(disk=10.0))])
        self.assertEqual(actions, [(0, "alert")])

    def test_critical_memory_is_held(self):
        mem = _haz(pressure="critical")
        self.assertEqual(_run([(150, mem), (600, CLEAR)])[0], [])
        self.assertEqual(_run([(210, mem)])[0], [(180, "alert")])

    def test_disk_does_not_skip_heat_hold(self):
        actions, state = _run([(30, _haz(thermal=2, disk=10.0))])
        self.assertEqual(actions, [(0, "alert")])
        self.assertEqual(state.told, {"disk": _haz(disk=10.0)["disk"].level})

    def test_zero_hold_is_immediate(self):
        actions, _ = _run([(30, HEAVY), (30, CLEAR)], hold_s=0)
        self.assertEqual(actions, [(0, "alert"), (30, "recover")])

    def test_failed_send_retries_next_tick(self):
        state, action, cur = alert_step(
            AlertState(), _haz(disk=10.0), now=0, hold_s=180, throttle_s=1800
        )
        self.assertEqual(action, "alert")
        # send failed: alert_sent not applied, so the next tick is still due
        _, action, _ = alert_step(
            state, _haz(disk=10.0), now=30, hold_s=180, throttle_s=1800
        )
        self.assertEqual(action, "alert")


class TestSleeps(unittest.TestCase):
    def setUp(self):
        self.events = parse_pmset_sleeps(PMSET_LOG)

    def test_drops_maintenance_and_non_sleep_lines(self):
        self.assertEqual(
            [e.reason for e in self.events],
            ["Software Sleep pid=40700", "Dark Wake Thermal Emergency", "Idle Sleep"],
        )

    def test_forced_sleep_carries_pid(self):
        self.assertEqual(self.events[0].pid, 40700)
        self.assertEqual(self.events[0].when, "2026-09-26 22:22:03")

    def test_forced_and_thermal_are_separate(self):
        self.assertEqual([e.forced for e in self.events], [True, False, False])
        self.assertEqual([e.thermal for e in self.events], [False, True, False])


class TestCatcherLog(unittest.TestCase):
    def test_details_attach_to_their_event(self):
        log = (
            "2026-09-27 05:50:08 watch: started (pid 1)\n"
            "2026-09-27 05:50:08 FORCED sleep (Software Sleep pid=62359) at 2026-09-27 05:50:08\n"
            "2026-09-27 05:50:08   requester 62359 and its parents:\n"
            "    62359 62226       00:00 pmset sleepnow\n"
            "    62226     1    07:50:59 bash -c pmset sleepnow\n"
            "2026-09-27 06:10:00 sleep (Idle Sleep) at 2026-09-27 06:10:00\n"
        )
        got = parse_catcher_log(log)
        self.assertEqual(list(got), ["2026-09-27 05:50:08"])
        detail = got["2026-09-27 05:50:08"]
        self.assertIn("requester 62359", detail)
        self.assertIn("bash -c pmset sleepnow", detail)
        self.assertNotIn("Idle Sleep", detail)

    def test_details_are_redacted(self):
        log = (
            "2026-09-27 05:50:08 FORCED sleep (Software Sleep pid=7) at 2026-09-27 05:50:08\n"
            "    9 1 01:00 tmux -L city new -e ANTHROPIC_API_KEY=sk-leak123\n"
        )
        detail = parse_catcher_log(log)["2026-09-27 05:50:08"]
        self.assertNotIn("sk-leak123", detail)
        self.assertIn("ANTHROPIC_API_KEY=<redacted>", detail)


class TestDisks(unittest.TestCase):
    def test_mounts_with_spaces(self):
        disks = parse_df(DF)
        self.assertEqual(disks["/Volumes/SD 4G 1"], 84)
        self.assertEqual(disks["/"], 41)

    def test_select_keeps_only_volumes_people_fill(self):
        self.assertEqual(
            select_mounts(parse_df(DF)),
            {"/": 41, "/System/Volumes/Data": 91, "/Volumes/SD 4G 1": 84},
        )


if __name__ == "__main__":
    unittest.main()
