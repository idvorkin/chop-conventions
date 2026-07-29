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


if __name__ == "__main__":
    unittest.main()
