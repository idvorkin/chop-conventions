"""Unit tests for md_probe pure logic.

Run from this directory: python3 -m unittest
Stdlib only — no uv, no typer, no live /proc.
"""

import unittest

from md_probe import (
    CpuTotals,
    idle_pct_between,
    parse_cpu_totals,
    parse_loadavg,
    parse_meminfo,
    parse_pswpout,
    redact,
    swap_out_kb_s,
)


class TestRedact(unittest.TestCase):
    def test_redacts_api_key(self):
        cl = "tmux -L my-city new-session -e ANTHROPIC_API_KEY=sk-ant-secret123"
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


class TestLoadavg(unittest.TestCase):
    def test_parses_three_loads(self):
        self.assertEqual(parse_loadavg("1.50 0.90 0.55 2/742 123"), (1.50, 0.90, 0.55))


class TestCpuTotals(unittest.TestCase):
    STAT = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 25 0 12 200 12 0 0 0 0 0\n"

    def test_parse_counts_idle_plus_iowait_as_idle(self):
        t = parse_cpu_totals(self.STAT)
        self.assertEqual(t.total, 1000)
        self.assertEqual(t.busy, 150)  # 1000 - (800 idle + 50 iowait)

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            parse_cpu_totals("intr 12345\n")

    def test_idle_pct_between_is_interval_not_lifetime(self):
        a = CpuTotals(busy=150, total=1000)
        b = CpuTotals(busy=950, total=2000)  # interval: 800 busy / 1000 total
        self.assertEqual(idle_pct_between(a, b), 20)

    def test_idle_pct_between_zero_delta_is_none(self):
        a = CpuTotals(busy=1, total=10)
        self.assertIsNone(idle_pct_between(a, a))


class TestMeminfo(unittest.TestCase):
    TEXT = (
        "MemTotal:       12237500 kB\n"
        "MemFree:          655696 kB\n"
        "MemAvailable:    8123456 kB\n"
        "SwapTotal:       4194300 kB\n"
        "SwapFree:        4000000 kB\n"
    )

    def test_parses_fields(self):
        m = parse_meminfo(self.TEXT)
        self.assertEqual(m.mem_total_kb, 12237500)
        self.assertEqual(m.mem_avail_kb, 8123456)
        self.assertEqual(m.swap_total_kb, 4194300)
        self.assertEqual(m.swap_free_kb, 4000000)

    def test_missing_fields_default_zero(self):
        m = parse_meminfo("MemTotal: 100 kB\n")
        self.assertEqual(m.mem_avail_kb, 0)


class TestSwapOut(unittest.TestCase):
    def test_parse_pswpout(self):
        self.assertEqual(parse_pswpout("pswpin 5\npswpout 4200\n"), 4200)
        self.assertIsNone(parse_pswpout("pswpin 5\n"))

    def test_kb_per_second_from_page_delta(self):
        # 300 pages * 4KB over 10s = 120 KB/s
        self.assertEqual(swap_out_kb_s(1000, 1300, 10), 120)

    def test_none_or_zero_dt_is_zero(self):
        self.assertEqual(swap_out_kb_s(None, 5, 10), 0)
        self.assertEqual(swap_out_kb_s(5, None, 10), 0)
        self.assertEqual(swap_out_kb_s(5, 10, 0), 0)

    def test_counter_reset_clamps_to_zero(self):
        self.assertEqual(swap_out_kb_s(9999, 3, 10), 0)


from datetime import datetime

from md_probe import (
    PidStat,
    ProcSample,
    SpikeConfig,
    SpikeState,
    detect_spike,
    etime_s,
    interval_cpu_pct,
    parse_duration,
    parse_pid_stat,
    render_tree,
    resolve_at,
    top_n,
)


def _proc(pid, cpu=0.0, rss=0, ppid=1, comm="x", etime=100):
    return ProcSample(pid=pid, ppid=ppid, comm=comm, cpu_pct=cpu, rss_kb=rss, etime_s=etime)


class TestPidStat(unittest.TestCase):
    # comm contains a space AND a paren — the kernel format's trap.
    LINE = "1234 (tmux: server (x)) S 1 1234 1234 0 -1 4194304 0 0 0 0 500 250 0 0 20 0 1 0 99000 1000000 2560 18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"

    def test_parses_comm_with_spaces_and_parens(self):
        st = parse_pid_stat(self.LINE)
        self.assertEqual(st.pid, 1234)
        self.assertEqual(st.comm, "tmux: server (x)")
        self.assertEqual(st.state, "S")
        self.assertEqual(st.ppid, 1)

    def test_cpu_jiffies_is_utime_plus_stime(self):
        self.assertEqual(parse_pid_stat(self.LINE).cpu_jiffies, 750)

    def test_rss_pages_to_kb(self):
        self.assertEqual(parse_pid_stat(self.LINE).rss_kb, 2560 * 4)

    def test_etime_from_starttime(self):
        st = parse_pid_stat(self.LINE)  # starttime 99000 jiffies = 990s
        self.assertEqual(etime_s(st, uptime_s=1000.0, hertz=100), 10)


class TestIntervalCpu(unittest.TestCase):
    def test_interval_percentage(self):
        # 3000 jiffies over 10s at 100Hz = 3 cpu-seconds/s = 300%
        self.assertEqual(interval_cpu_pct(1000, 4000, 10.0), 300.0)

    def test_first_sample_is_none(self):
        self.assertIsNone(interval_cpu_pct(None, 4000, 10.0))

    def test_zero_dt_is_none(self):
        self.assertIsNone(interval_cpu_pct(1000, 4000, 0.0))


class TestTopN(unittest.TestCase):
    def test_union_of_cpu_and_rss(self):
        procs = [_proc(1, cpu=90.0), _proc(2, cpu=10.0), _proc(3, cpu=0.0, rss=999999)]
        got = top_n(procs, n=1)
        self.assertEqual({p.pid for p in got}, {1, 3})

    def test_none_cpu_ranks_last_not_crashing(self):
        procs = [ProcSample(1, 1, "a", None, 5, 1), _proc(2, cpu=1.0)]
        got = top_n(procs, n=1)
        self.assertIn(2, {p.pid for p in got})


class TestDetectSpike(unittest.TestCase):
    MEM_OK = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")

    def _detect(self, state, **kw):
        args = dict(idle_pct=90, mem=self.MEM_OK, swap_out=0, procs=[])
        args.update(kw)
        return detect_spike(state, SpikeConfig(), **args)

    def test_quiet_box_no_reasons(self):
        st, reasons = self._detect(SpikeState())
        self.assertEqual(reasons, [])

    def test_hot_process_fires_immediately(self):
        st, reasons = self._detect(SpikeState(), procs=[_proc(9, cpu=400.0, comm="cc1")])
        self.assertTrue(any("cc1" in r for r in reasons))

    def test_low_idle_needs_two_consecutive_samples(self):
        st, reasons = self._detect(SpikeState(), idle_pct=5)
        self.assertEqual(reasons, [])  # single sample is an artifact
        st, reasons = self._detect(st, idle_pct=5)
        self.assertTrue(any("idle" in r for r in reasons))

    def test_idle_recovery_resets_counter(self):
        st, _ = self._detect(SpikeState(), idle_pct=5)
        st, _ = self._detect(st, idle_pct=90)
        st, reasons = self._detect(st, idle_pct=5)
        self.assertEqual(reasons, [])

    def test_swap_out_needs_two_consecutive_samples(self):
        st, reasons = self._detect(SpikeState(), swap_out=500)
        self.assertEqual(reasons, [])
        st, reasons = self._detect(st, swap_out=500)
        self.assertTrue(any("swap" in r for r in reasons))

    def test_mem_pressure_fires_immediately(self):
        low = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 50 kB\n")
        st, reasons = self._detect(SpikeState(), mem=low)
        self.assertTrue(any("MemAvailable" in r for r in reasons))


class TestTimeParsing(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("6h"), 21600)
        self.assertEqual(parse_duration("2d"), 172800)

    def test_bare_number_is_an_error(self):
        with self.assertRaises(ValueError):
            parse_duration("6")

    def test_at_bare_time_today(self):
        now = datetime(2026, 7, 29, 12, 0, 0)
        self.assertEqual(resolve_at("07:16", now), datetime(2026, 7, 29, 7, 16, 0))

    def test_at_future_time_rolls_to_yesterday(self):
        now = datetime(2026, 7, 29, 0, 10, 0)
        self.assertEqual(resolve_at("23:50", now), datetime(2026, 7, 28, 23, 50, 0))

    def test_at_full_iso(self):
        now = datetime(2026, 7, 29, 12, 0, 0)
        self.assertEqual(
            resolve_at("2026-07-29T07:16:38", now), datetime(2026, 7, 29, 7, 16, 38)
        )


class TestRenderTree(unittest.TestCase):
    def test_children_indented_under_parents(self):
        procs = [_proc(1, comm="init"), _proc(20, ppid=1, comm="make"), _proc(21, ppid=20, comm="cc1")]
        out = render_tree(procs, {})
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("1 init"))
        self.assertTrue(lines[1].startswith("  20 make"))
        self.assertTrue(lines[2].startswith("    21 cc1"))

    def test_cmdlines_are_redacted(self):
        procs = [_proc(5, comm="tmux")]
        out = render_tree(procs, {5: "tmux -e ANTHROPIC_API_KEY=sk-leak123"})
        self.assertNotIn("sk-leak123", out)
        self.assertIn("<redacted>", out)


if __name__ == "__main__":
    unittest.main()
