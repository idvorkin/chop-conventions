# Machine Doctor Forensics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `skills/machine-doctor/tools/gascity_doctor.py` into a generic
machine doctor that records resource history (SQLite + spike dumps) and answers
"who was hot at time T", per the spec at
`docs/superpowers/specs/2026-07-29-machine-doctor-design.md`.

**Architecture:** Three pure/stdlib modules (`md_probe.py` for /proc parsing +
spike detection, `md_store.py` for SQLite + spike-dump persistence,
`profiles.py` for known-leak findings) orchestrated by a Typer CLI
(`machine_doctor.py`) with `watch` / `report` / `at` / `snapshot` commands. The
Gas City leak hunt becomes `--profile gascity`.

**Tech Stack:** Python 3.13, stdlib `sqlite3` + `unittest`, Typer (CLI only,
lazy-imported), uv PEP 723 script header.

## Global Constraints

- All work happens in the worktree `/home/developer/gits/chop-conventions/.worktrees/gascity-doctor`, branch `delegated/gascity-doctor` (PR #197's branch). Never edit through `~/.claude/skills/` symlinks.
- `machine_doctor.py` keeps the uv header exactly: `#!/usr/bin/env -S uv run --script`, `requires-python = ">=3.13"`, `dependencies = ["typer>=0.12"]`, executable bit set.
- `md_probe.py`, `md_store.py`, `profiles.py` are stdlib-only — no typer import, no subprocess, no live `/proc` reads. All I/O lives in `machine_doctor.py` (and `md_store`'s explicit sqlite/filesystem calls).
- Typer stays behind `_build_app()` (lazy import) so `python3 -m unittest` runs without uv.
- Tests run from the tools directory: `python3 -m unittest` discovers `test_*.py`.
- Any code path that prints or persists a process command line MUST pass it through `redact()` first — argv is world-readable via /proc and gc puts credentials there.
- Data lands in `~/.local/state/machine-doctor/` (`samples.db`, `spikes/`); every store function takes the path as a parameter so tests never touch the real state dir.
- `cpu_pct` is interval CPU (delta utime+stime between reads), never ps-style lifetime average. First sample records NULL.
- No commits to main; commit per task on the feature branch; push only in Task 8 after checking PR #197 is still OPEN (`gh pr list --head delegated/gascity-doctor --state all --json state,number`).

---

### Task 1: `md_probe.py` — system-level parsers

**Files:**
- Create: `skills/machine-doctor/tools/md_probe.py`
- Test: `skills/machine-doctor/tools/test_md_probe.py`

**Interfaces:**
- Produces: `redact(str)->str`, `parse_loadavg(str)->tuple[float,float,float]`,
  `CpuTotals(busy:int,total:int)`, `parse_cpu_totals(str)->CpuTotals`,
  `idle_pct_between(CpuTotals,CpuTotals)->int|None`,
  `MemInfo(mem_total_kb,mem_avail_kb,swap_total_kb,swap_free_kb)`,
  `parse_meminfo(str)->MemInfo`, `parse_pswpout(str)->int|None`,
  `swap_out_kb_s(prev,cur,dt_s,page_kb=4)->int`

- [ ] **Step 1: Write the failing tests** (`test_md_probe.py`, system-parser classes only)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/machine-doctor/tools && python3 -m unittest test_md_probe -v`
Expected: `ModuleNotFoundError: No module named 'md_probe'`

- [ ] **Step 3: Write the implementation** (`md_probe.py`, first half)

```python
"""Pure probe logic for machine-doctor: /proc parsing, spike detection.

Stdlib only — no subprocess, no filesystem, no live /proc. Every function takes
text or values and returns values, so the module is fully unit-testable with
fixture strings. All I/O lives in machine_doctor.py.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

SECRET_ENV_RE = re.compile(
    r"(-e\s+)([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=\S+"
)


def redact(cmdline: str) -> str:
    """Strip secret values out of a process command line.

    Agent sessions carry credentials in argv (`tmux ... -e
    ANTHROPIC_API_KEY=sk-...`), world-readable via /proc, so anything that
    prints or persists a cmdline must scrub it first.
    """
    return SECRET_ENV_RE.sub(r"\1\2=<redacted>", cmdline)


def parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.split()
    return (float(parts[0]), float(parts[1]), float(parts[2]))


@dataclass(frozen=True)
class CpuTotals:
    """Aggregate jiffies from the `cpu ` line of /proc/stat."""

    busy: int
    total: int


def parse_cpu_totals(text: str) -> CpuTotals:
    """Idle% must come from a delta of two of these; one read is a boot-lifetime
    average that says nothing about now. iowait counts as idle: waiting on disk
    is not CPU pressure."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            f = [int(x) for x in line.split()[1:]]
            idle = f[3] + (f[4] if len(f) > 4 else 0)
            total = sum(f)
            return CpuTotals(busy=total - idle, total=total)
    raise ValueError("no 'cpu ' line in /proc/stat text")


def idle_pct_between(prev: CpuTotals, cur: CpuTotals) -> int | None:
    d_total = cur.total - prev.total
    if d_total <= 0:
        return None
    d_busy = cur.busy - prev.busy
    return max(0, min(100, round(100 * (1 - d_busy / d_total))))


@dataclass(frozen=True)
class MemInfo:
    mem_total_kb: int
    mem_avail_kb: int
    swap_total_kb: int
    swap_free_kb: int


def parse_meminfo(text: str) -> MemInfo:
    vals: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            vals[key] = int(parts[0])
    return MemInfo(
        mem_total_kb=vals.get("MemTotal", 0),
        mem_avail_kb=vals.get("MemAvailable", 0),
        swap_total_kb=vals.get("SwapTotal", 0),
        swap_free_kb=vals.get("SwapFree", 0),
    )


def parse_pswpout(text: str) -> int | None:
    """Cumulative pages swapped out — the `pswpout` line of /proc/vmstat."""
    for line in text.splitlines():
        if line.startswith("pswpout "):
            return int(line.split()[1])
    return None


def swap_out_kb_s(
    prev_pages: int | None, cur_pages: int | None, dt_s: float, page_kb: int = 4
) -> int:
    """KB/s swapped out over the interval (vmstat's `so` column, derived).

    Clamped at zero: a counter reset (reboot) must not produce a negative rate.
    """
    if prev_pages is None or cur_pages is None or dt_s <= 0:
        return 0
    return max(0, round((cur_pages - prev_pages) * page_kb / dt_s))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/machine-doctor/tools && python3 -m unittest test_md_probe -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add skills/machine-doctor/tools/md_probe.py skills/machine-doctor/tools/test_md_probe.py
git commit -m "feat(machine-doctor): md_probe system parsers (cpu/mem/swap/loadavg, redact)"
```

---

### Task 2: `md_probe.py` — per-process parsing, spike detection, time parsing, tree render

**Files:**
- Modify: `skills/machine-doctor/tools/md_probe.py` (append)
- Modify: `skills/machine-doctor/tools/test_md_probe.py` (append)

**Interfaces:**
- Produces:
  `PidStat(pid:int, comm:str, state:str, ppid:int, cpu_jiffies:int, rss_kb:int, starttime_jiffies:int)`,
  `parse_pid_stat(text, page_kb=4)->PidStat`,
  `etime_s(stat, uptime_s, hertz=100)->int`,
  `ProcSample(pid:int, ppid:int, comm:str, cpu_pct:float|None, rss_kb:int, etime_s:int)`,
  `interval_cpu_pct(prev_jiffies:int|None, cur_jiffies:int, dt_s:float, hertz=100)->float|None`,
  `top_n(list[ProcSample], n=10)->list[ProcSample]`,
  `SpikeConfig(cpu_pct=300.0, idle_pct=25, mem_avail_pct=10, consecutive=2)`,
  `SpikeState(low_idle=0, swapping=0)`,
  `detect_spike(state, cfg, *, idle_pct, mem, swap_out, procs) -> tuple[SpikeState, list[str]]`,
  `parse_duration(str)->int`, `resolve_at(str, now:datetime)->datetime`,
  `render_tree(list[ProcSample], cmdlines: dict[int,str])->str`

- [ ] **Step 1: Append the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_md_probe -v` — Expected: ImportError on `PidStat`.

- [ ] **Step 3: Append the implementation to `md_probe.py`**

```python
@dataclass(frozen=True)
class PidStat:
    """One parsed /proc/<pid>/stat line."""

    pid: int
    comm: str
    state: str
    ppid: int
    cpu_jiffies: int  # utime + stime
    rss_kb: int
    starttime_jiffies: int


def parse_pid_stat(text: str, page_kb: int = 4) -> PidStat:
    """comm may contain spaces and parens; everything after the LAST ')' is
    guaranteed numeric by the kernel, so split there, not on whitespace."""
    lparen = text.index("(")
    rparen = text.rindex(")")
    rest = text[rparen + 1 :].split()
    return PidStat(
        pid=int(text[:lparen].strip()),
        comm=text[lparen + 1 : rparen],
        state=rest[0],
        ppid=int(rest[1]),
        cpu_jiffies=int(rest[11]) + int(rest[12]),  # utime + stime
        rss_kb=int(rest[21]) * page_kb,  # rss is in pages
        starttime_jiffies=int(rest[19]),
    )


def etime_s(stat: PidStat, uptime_s: float, hertz: int = 100) -> int:
    return max(0, int(uptime_s - stat.starttime_jiffies / hertz))


@dataclass(frozen=True)
class ProcSample:
    pid: int
    ppid: int
    comm: str
    cpu_pct: float | None  # None on the first sample: no previous read exists
    rss_kb: int
    etime_s: int


def interval_cpu_pct(
    prev_jiffies: int | None, cur_jiffies: int, dt_s: float, hertz: int = 100
) -> float | None:
    """Interval CPU%, NOT ps-style lifetime average — a long-lived process that
    starts spinning must show the spike immediately, and lifetime averaging
    dilutes it toward zero."""
    if prev_jiffies is None or dt_s <= 0:
        return None
    return max(0.0, 100.0 * (cur_jiffies - prev_jiffies) / hertz / dt_s)


def top_n(procs: list[ProcSample], n: int = 10) -> list[ProcSample]:
    """Union of top-n by interval CPU and top-n by RSS: a leak can be memory-hot
    while CPU-cold, and vice versa."""
    by_cpu = sorted(procs, key=lambda p: p.cpu_pct if p.cpu_pct is not None else -1.0, reverse=True)[:n]
    by_rss = sorted(procs, key=lambda p: p.rss_kb, reverse=True)[:n]
    keep = {p.pid for p in by_cpu} | {p.pid for p in by_rss}
    return sorted(
        (p for p in procs if p.pid in keep),
        key=lambda p: (-(p.cpu_pct if p.cpu_pct is not None else -1.0), -p.rss_kb),
    )


@dataclass(frozen=True)
class SpikeConfig:
    cpu_pct: float = 300.0  # below cpu-watchdog's 400% throttle, deliberately
    idle_pct: int = 25
    mem_avail_pct: int = 10
    consecutive: int = 2  # a single sample is an artifact, not an event


@dataclass
class SpikeState:
    low_idle: int = 0
    swapping: int = 0


def detect_spike(
    state: SpikeState,
    cfg: SpikeConfig,
    *,
    idle_pct: int | None,
    mem: MemInfo,
    swap_out: int,
    procs: list[ProcSample],
) -> tuple[SpikeState, list[str]]:
    """Returns (next_state, reasons). Empty reasons -> not a spike.

    Idle and swap-out require cfg.consecutive samples; per-process CPU and
    memory pressure fire immediately (they are already interval measurements).
    """
    nxt = SpikeState(low_idle=state.low_idle, swapping=state.swapping)
    reasons: list[str] = []

    hot = [p for p in procs if (p.cpu_pct or 0.0) > cfg.cpu_pct]
    if hot:
        worst = max(hot, key=lambda p: p.cpu_pct or 0.0)
        reasons.append(f"proc {worst.comm} pid={worst.pid} at {worst.cpu_pct:.0f}% cpu")

    nxt.low_idle = nxt.low_idle + 1 if (idle_pct is not None and idle_pct < cfg.idle_pct) else 0
    if nxt.low_idle >= cfg.consecutive:
        reasons.append(f"idle {idle_pct}% for {nxt.low_idle} samples")

    nxt.swapping = nxt.swapping + 1 if swap_out > 0 else 0
    if nxt.swapping >= cfg.consecutive:
        reasons.append(f"swap-out {swap_out} KB/s for {nxt.swapping} samples")

    if mem.mem_total_kb > 0 and mem.mem_avail_kb * 100 < mem.mem_total_kb * cfg.mem_avail_pct:
        reasons.append(
            f"MemAvailable {mem.mem_avail_kb // 1024}MB is under "
            f"{cfg.mem_avail_pct}% of {mem.mem_total_kb // 1024}MB"
        )

    return nxt, reasons


DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """'30m' -> 1800 seconds. Bare numbers are an error, not six of something."""
    m = DURATION_RE.match(text.strip())
    if not m:
        raise ValueError(f"bad duration {text!r}: expected <int><s|m|h|d>, e.g. 6h")
    return int(m.group(1)) * _UNIT_S[m.group(2)]


def resolve_at(text: str, now: datetime) -> datetime:
    """'07:16', '07:16:38' (today; future rolls back to yesterday) or ISO-8601."""
    t = text.strip()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if m:
        cand = now.replace(
            hour=int(m.group(1)),
            minute=int(m.group(2)),
            second=int(m.group(3) or 0),
            microsecond=0,
        )
        if cand > now:
            cand -= timedelta(days=1)
        return cand
    return datetime.fromisoformat(t)


def render_tree(procs: list[ProcSample], cmdlines: dict[int, str]) -> str:
    """Full process tree for a spike dump. Cmdlines pass through redact() here —
    the single choke point before tree text can reach disk or a terminal."""
    by_pid = {p.pid: p for p in procs}
    kids: dict[int, list[ProcSample]] = {}
    roots: list[ProcSample] = []
    for p in procs:
        if p.ppid in by_pid and p.ppid != p.pid:
            kids.setdefault(p.ppid, []).append(p)
        else:
            roots.append(p)

    lines: list[str] = []

    def emit(p: ProcSample, depth: int) -> None:
        cpu = "-" if p.cpu_pct is None else f"{p.cpu_pct:.0f}%"
        cl = redact(cmdlines.get(p.pid, ""))[:200]
        lines.append(
            f"{'  ' * depth}{p.pid} {p.comm} cpu={cpu} rss={p.rss_kb // 1024}MB "
            f"etime={p.etime_s}s ppid={p.ppid} {cl}".rstrip()
        )
        for k in sorted(kids.get(p.pid, []), key=lambda x: x.pid):
            emit(k, depth + 1)

    for r in sorted(roots, key=lambda x: x.pid):
        emit(r, 0)
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_md_probe -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/machine-doctor/tools/md_probe.py skills/machine-doctor/tools/test_md_probe.py
git commit -m "feat(machine-doctor): per-process parsing, spike detection, time parsing, tree render"
```

---

### Task 3: `md_store.py` — SQLite schema, inserts, pruning, spike dumps

**Files:**
- Create: `skills/machine-doctor/tools/md_store.py`
- Test: `skills/machine-doctor/tools/test_md_store.py`

**Interfaces:**
- Consumes: `ProcSample` from `md_probe`
- Produces: `DEFAULT_STATE_DIR: Path`, `connect(db_path)->sqlite3.Connection`,
  `insert_sample(conn, ts, *, load1, idle_pct, mem_avail_kb, swap_used_kb, swap_out, nproc, is_spike, procs)`,
  `prune(conn, now_ts, keep_days=7)->int`,
  `write_spike_dump(state_dir, ts, text)->Path`,
  `prune_spike_dumps(state_dir, keep=50)->int`,
  `dump_path_for(state_dir, ts)->Path` (same name `write_spike_dump` uses)

- [ ] **Step 1: Write the failing tests** (`test_md_store.py`)

```python
"""Unit tests for md_store persistence. In-memory SQLite + tmp dirs only."""

import tempfile
import unittest
from pathlib import Path

from md_probe import ProcSample
from md_store import (
    connect,
    dump_path_for,
    insert_sample,
    prune,
    prune_spike_dumps,
    write_spike_dump,
)


def _proc(pid, cpu=0.0, rss=0, comm="x"):
    return ProcSample(pid=pid, ppid=1, comm=comm, cpu_pct=cpu, rss_kb=rss, etime_s=1)


def _insert(conn, ts, procs=(), is_spike=False, **kw):
    defaults = dict(load1=1.0, idle_pct=90, mem_avail_kb=1000, swap_used_kb=0,
                    swap_out=0, nproc=100)
    defaults.update(kw)
    insert_sample(conn, ts, is_spike=is_spike, procs=list(procs), **defaults)


class TestSchemaAndInsert(unittest.TestCase):
    def test_insert_and_read_back(self):
        conn = connect(":memory:")
        _insert(conn, 1000, procs=[_proc(5, cpu=50.0, comm="go")])
        row = conn.execute("SELECT ts, load1, is_spike FROM sample").fetchone()
        self.assertEqual((row[0], row[2]), (1000, 0))
        prow = conn.execute("SELECT ts, pid, comm FROM proc_sample").fetchone()
        self.assertEqual(prow, (1000, 5, "go"))

    def test_ts_collision_fails_loudly(self):
        """A clock stepping backwards must not silently overwrite history."""
        import sqlite3
        conn = connect(":memory:")
        _insert(conn, 1000)
        with self.assertRaises(sqlite3.IntegrityError):
            _insert(conn, 1000)


class TestPrune(unittest.TestCase):
    def test_drops_old_keeps_new(self):
        conn = connect(":memory:")
        old = 1000
        new = old + 8 * 86400
        _insert(conn, old, procs=[_proc(1)])
        _insert(conn, new, procs=[_proc(2)])
        dropped = prune(conn, now_ts=new, keep_days=7)
        self.assertEqual(dropped, 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM proc_sample").fetchone()[0], 1)


class TestSpikeDumps(unittest.TestCase):
    def test_write_and_locate(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_spike_dump(Path(d), 1753798598, "tree text")
            self.assertTrue(p.exists())
            self.assertEqual(p, dump_path_for(Path(d), 1753798598))
            self.assertEqual(p.read_text(), "tree text")

    def test_prune_keeps_newest_n(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(6):
                write_spike_dump(Path(d), 1753798598 + i * 60, f"dump {i}")
            removed = prune_spike_dumps(Path(d), keep=3)
            self.assertEqual(removed, 3)
            left = sorted((Path(d) / "spikes").glob("*.txt"))
            self.assertEqual(len(left), 3)
            self.assertIn("dump 5", left[-1].read_text())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_md_store -v` — Expected: `ModuleNotFoundError: No module named 'md_store'`

- [ ] **Step 3: Write the implementation** (`md_store.py`)

```python
"""Persistence for machine-doctor: SQLite sample history + spike-dump files.

Owns all durable state. Every function takes its path/connection explicitly so
tests run against :memory: and tmp dirs, never the real state directory.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from md_probe import ProcSample

DEFAULT_STATE_DIR = Path("~/.local/state/machine-doctor").expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sample (
    ts            INTEGER PRIMARY KEY,   -- unix seconds; PK so a clock step back fails loudly
    load1         REAL,
    idle_pct      INTEGER,
    mem_avail_kb  INTEGER,
    swap_used_kb  INTEGER,
    swap_out      INTEGER,               -- KB/s swapped out over the interval
    nproc         INTEGER,
    is_spike      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proc_sample (
    ts        INTEGER NOT NULL,
    pid       INTEGER NOT NULL,
    ppid      INTEGER,
    comm      TEXT,
    cpu_pct   REAL,
    rss_kb    INTEGER,
    etime_s   INTEGER,
    PRIMARY KEY (ts, pid)
);
CREATE INDEX IF NOT EXISTS idx_proc_ts   ON proc_sample(ts);
CREATE INDEX IF NOT EXISTS idx_proc_comm ON proc_sample(comm);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def insert_sample(
    conn: sqlite3.Connection,
    ts: int,
    *,
    load1: float,
    idle_pct: int | None,
    mem_avail_kb: int,
    swap_used_kb: int,
    swap_out: int,
    nproc: int,
    is_spike: bool,
    procs: list[ProcSample],
) -> None:
    conn.execute(
        "INSERT INTO sample VALUES (?,?,?,?,?,?,?,?)",
        (ts, load1, idle_pct, mem_avail_kb, swap_used_kb, swap_out, nproc, int(is_spike)),
    )
    conn.executemany(
        "INSERT INTO proc_sample VALUES (?,?,?,?,?,?,?)",
        [(ts, p.pid, p.ppid, p.comm, p.cpu_pct, p.rss_kb, p.etime_s) for p in procs],
    )
    conn.commit()


def prune(conn: sqlite3.Connection, now_ts: int, keep_days: int = 7) -> int:
    cutoff = now_ts - keep_days * 86400
    dropped = conn.execute("DELETE FROM sample WHERE ts < ?", (cutoff,)).rowcount
    conn.execute("DELETE FROM proc_sample WHERE ts < ?", (cutoff,))
    conn.commit()
    return dropped


def dump_path_for(state_dir: Path, ts: int) -> Path:
    # Hyphens, not colons — colon filenames break on enough filesystems to matter.
    name = datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H-%M-%S") + ".txt"
    return Path(state_dir) / "spikes" / name


def write_spike_dump(state_dir: Path, ts: int, text: str) -> Path:
    path = dump_path_for(state_dir, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def prune_spike_dumps(state_dir: Path, keep: int = 50) -> int:
    d = Path(state_dir) / "spikes"
    if not d.is_dir():
        return 0
    dumps = sorted(d.glob("*.txt"))  # ISO names sort chronologically
    excess = dumps[:-keep] if keep > 0 else dumps
    for f in excess:
        f.unlink()
    return len(excess)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_md_store -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/machine-doctor/tools/md_store.py skills/machine-doctor/tools/test_md_store.py
git commit -m "feat(machine-doctor): SQLite store, retention pruning, spike dumps"
```

---

### Task 4: `md_store.py` — report aggregation and `at` lookups

**Files:**
- Modify: `skills/machine-doctor/tools/md_store.py` (append)
- Modify: `skills/machine-doctor/tools/test_md_store.py` (append)

**Interfaces:**
- Produces:
  `CommRank(comm:str, cpu_sum:float, peak_rss_kb:int, samples:int, first_ts:int, last_ts:int)`,
  `report(conn, since_ts, top=10, exclude=frozenset())->list[CommRank]`,
  `sample_count(conn, since_ts)->int`,
  `spike_count(conn, since_ts)->int`,
  `SampleRow(ts, load1, idle_pct, mem_avail_kb, swap_used_kb, swap_out, nproc, is_spike)`,
  `nearest_sample(conn, ts, tolerance_s=60)->SampleRow|None`,
  `procs_at(conn, ts)->list[ProcSample]`

- [ ] **Step 1: Append the failing tests**

```python
from md_store import (
    CommRank,
    nearest_sample,
    procs_at,
    report,
    sample_count,
    spike_count,
)


class TestReport(unittest.TestCase):
    def _seed(self):
        conn = connect(":memory:")
        # Build-style: many short-lived cc1 pids, each modest — must aggregate.
        _insert(conn, 1000, procs=[_proc(11, cpu=80.0, comm="cc1"), _proc(30, cpu=90.0, comm="agent")])
        _insert(conn, 1030, procs=[_proc(12, cpu=80.0, comm="cc1"), _proc(30, cpu=10.0, comm="agent")])
        _insert(conn, 1060, procs=[_proc(13, cpu=80.0, comm="cc1", rss=500000)])
        return conn

    def test_groups_by_comm_and_ranks_by_cpu_sum(self):
        got = report(self._seed(), since_ts=0)
        self.assertEqual(got[0].comm, "cc1")
        self.assertEqual(got[0].cpu_sum, 240.0)
        self.assertEqual(got[0].samples, 3)
        self.assertEqual(got[0].peak_rss_kb, 500000)
        self.assertEqual(got[1].comm, "agent")

    def test_since_filters(self):
        got = report(self._seed(), since_ts=1050)
        self.assertEqual([r.comm for r in got], ["cc1"])

    def test_exclude_drops_sampler(self):
        got = report(self._seed(), since_ts=0, exclude=frozenset({"cc1"}))
        self.assertEqual([r.comm for r in got], ["agent"])

    def test_counts(self):
        conn = self._seed()
        self.assertEqual(sample_count(conn, 0), 3)
        self.assertEqual(spike_count(conn, 0), 0)


class TestNearestSample(unittest.TestCase):
    def test_within_tolerance(self):
        conn = connect(":memory:")
        _insert(conn, 1000, is_spike=True)
        row = nearest_sample(conn, 1030, tolerance_s=60)
        self.assertEqual(row.ts, 1000)
        self.assertTrue(row.is_spike)

    def test_outside_tolerance_is_none(self):
        conn = connect(":memory:")
        _insert(conn, 1000)
        self.assertIsNone(nearest_sample(conn, 2000, tolerance_s=60))

    def test_empty_db_is_none(self):
        self.assertIsNone(nearest_sample(connect(":memory:"), 1000))

    def test_procs_at_returns_that_instant(self):
        conn = connect(":memory:")
        _insert(conn, 1000, procs=[_proc(5, cpu=42.0, comm="dolt")])
        got = procs_at(conn, 1000)
        self.assertEqual([(p.pid, p.comm) for p in got], [(5, "dolt")])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_md_store -v` — Expected: ImportError on `report`.

- [ ] **Step 3: Append the implementation to `md_store.py`**

```python
@dataclass(frozen=True)
class CommRank:
    comm: str
    cpu_sum: float  # sum of interval cpu_pct — proportional to CPU-seconds at a fixed interval
    peak_rss_kb: int
    samples: int
    first_ts: int
    last_ts: int


def report(
    conn: sqlite3.Connection,
    since_ts: int,
    top: int = 10,
    exclude: frozenset[str] = frozenset(),
) -> list[CommRank]:
    """Grouped by comm, not pid: build-style workloads split their load across
    hundreds of short-lived pids that no per-pid ranking would surface."""
    rows = conn.execute(
        """SELECT comm, COALESCE(SUM(cpu_pct), 0), COALESCE(MAX(rss_kb), 0),
                  COUNT(*), MIN(ts), MAX(ts)
           FROM proc_sample WHERE ts >= ?
           GROUP BY comm
           ORDER BY COALESCE(SUM(cpu_pct), 0) DESC, COALESCE(MAX(rss_kb), 0) DESC""",
        (since_ts,),
    ).fetchall()
    ranked = [CommRank(*r) for r in rows if r[0] not in exclude]
    return ranked[:top]


def sample_count(conn: sqlite3.Connection, since_ts: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM sample WHERE ts >= ?", (since_ts,)).fetchone()[0]


def spike_count(conn: sqlite3.Connection, since_ts: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM sample WHERE ts >= ? AND is_spike = 1", (since_ts,)
    ).fetchone()[0]


@dataclass(frozen=True)
class SampleRow:
    ts: int
    load1: float
    idle_pct: int | None
    mem_avail_kb: int
    swap_used_kb: int
    swap_out: int
    nproc: int
    is_spike: bool


def nearest_sample(
    conn: sqlite3.Connection, ts: int, tolerance_s: int = 60
) -> SampleRow | None:
    """Nearest sample within tolerance, else None. The record-time interval is
    not stored, so the tolerance is fixed rather than interval-derived."""
    row = conn.execute(
        "SELECT ts, load1, idle_pct, mem_avail_kb, swap_used_kb, swap_out, nproc, is_spike "
        "FROM sample ORDER BY ABS(ts - ?) LIMIT 1",
        (ts,),
    ).fetchone()
    if row is None or abs(row[0] - ts) > tolerance_s:
        return None
    return SampleRow(*row[:7], bool(row[7]))


def procs_at(conn: sqlite3.Connection, ts: int) -> list[ProcSample]:
    rows = conn.execute(
        "SELECT pid, ppid, comm, cpu_pct, rss_kb, etime_s FROM proc_sample "
        "WHERE ts = ? ORDER BY COALESCE(cpu_pct, -1) DESC, rss_kb DESC",
        (ts,),
    ).fetchall()
    return [
        ProcSample(pid=r[0], ppid=r[1], comm=r[2], cpu_pct=r[3], rss_kb=r[4], etime_s=r[5])
        for r in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_md_store -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/machine-doctor/tools/md_store.py skills/machine-doctor/tools/test_md_store.py
git commit -m "feat(machine-doctor): report aggregation (by comm) and at-time lookups"
```

---

### Task 5: `profiles.py` — generic + gascity known-leak findings

**Files:**
- Create: `skills/machine-doctor/tools/profiles.py`
- Test: `skills/machine-doctor/tools/test_profiles.py`

**Interfaces:**
- Consumes: `ProcSample` from `md_probe`
- Produces: `Finding(severity:str, message:str)` (severity `"warn"` | `"fail"`),
  `HostFacts` dataclass (fields below), `classify_dolt(str)->str`,
  `is_watchdog(str)->bool`, `USER_SOCKETS: frozenset[str]`,
  `PROFILES: dict[str, Callable[[HostFacts], list[Finding]]]` with keys
  `"generic"` and `"gascity"` (gascity is a superset of generic)

- [ ] **Step 1: Write the failing tests** (`test_profiles.py`)

```python
"""Unit tests for profiles: known-leak findings over seeded HostFacts."""

import unittest

from md_probe import ProcSample
from profiles import (
    PROFILES,
    USER_SOCKETS,
    Finding,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_profiles -v` — Expected: `ModuleNotFoundError: No module named 'profiles'`

- [ ] **Step 3: Write the implementation** (`profiles.py`)

```python
"""Known-leak profiles for machine-doctor snapshot.

Pure: each profile consumes a seeded HostFacts and returns Findings. The
collection I/O that fills HostFacts lives in machine_doctor.py. Two profiles is
a dict, not a plugin framework (N=2).
"""

from dataclasses import dataclass, field

from md_probe import ProcSample

# The human's own tmux sockets. Never a Gas City leak — flagging these trains
# the reader to ignore the tool.
USER_SOCKETS = frozenset({"default", "ssh"})

HOT_CPU_PCT = 300.0
MEM_AVAIL_FAIL_PCT = 10


@dataclass(frozen=True)
class Finding:
    severity: str  # "warn" | "fail"
    message: str


@dataclass
class HostFacts:
    """Everything a profile may inspect, gathered once by the collector."""

    procs: list[ProcSample] = field(default_factory=list)
    cmdlines: dict[int, str] = field(default_factory=dict)
    dolt_cwds: dict[int, str] = field(default_factory=dict)
    orphan_tmux: dict[int, str] = field(default_factory=dict)  # pid -> socket
    stale_sockets: list[str] = field(default_factory=list)
    zombies: list[int] = field(default_factory=list)
    load1: float = 0.0
    idle_pct: int | None = None
    mem_total_kb: int = 0
    mem_avail_kb: int = 0


def classify_dolt(cwd: str) -> str:
    """`city` servers belong to a Gas City scope and are the ones gc should
    have reaped. `beads-repo` servers are spawned on demand by `bd` for an
    ordinary repo's own store and are none of gc's business."""
    if not cwd:
        return "unknown"
    if "/.gc/" in cwd or cwd.endswith("/.gc"):
        return "city"
    if "/.beads/" in cwd:
        return "beads-repo"
    return "unknown"


def is_watchdog(cmdline: str) -> bool:
    """gc's managed-dolt scope watchdog: survives `gc stop`, keeps a dolt
    server alive, invisible to `gc cities`."""
    return "__gc-managed-dolt-scope-watchdog" in cmdline


def generic_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    for p in facts.procs:
        if (p.cpu_pct or 0.0) > HOT_CPU_PCT:
            out.append(Finding("warn", f"hot process: {p.comm} pid={p.pid} at {p.cpu_pct:.0f}% cpu"))
    if facts.mem_total_kb > 0 and facts.mem_avail_kb * 100 < facts.mem_total_kb * MEM_AVAIL_FAIL_PCT:
        out.append(
            Finding(
                "fail",
                f"memory pressure: MemAvailable {facts.mem_avail_kb // 1024}MB is under "
                f"{MEM_AVAIL_FAIL_PCT}% of {facts.mem_total_kb // 1024}MB",
            )
        )
    if facts.zombies:
        out.append(Finding("warn", f"{len(facts.zombies)} zombie process(es): {sorted(facts.zombies)}"))
    return out


def gascity_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    watchdogs = sorted(pid for pid, cl in facts.cmdlines.items() if is_watchdog(cl))
    if watchdogs:
        out.append(
            Finding(
                "fail",
                f"orphaned dolt watchdog(s) {watchdogs} — survives `gc stop`, "
                "invisible to `gc cities`; kill the pid or run `gc stop` in the city dir",
            )
        )
    for pid, cwd in sorted(facts.dolt_cwds.items()):
        kind = classify_dolt(cwd)
        if kind == "city":
            out.append(Finding("fail", f"city dolt server pid={pid} {cwd} — gc teardown missed it"))
        elif kind == "unknown":
            out.append(Finding("warn", f"dolt server of unknown scope pid={pid} {cwd}"))
        # beads-repo: bd's own on-demand store, never a leak.
    for pid, sock in sorted(facts.orphan_tmux.items()):
        out.append(
            Finding(
                "fail",
                f"orphaned city tmux server pid={pid} ({sock}) — holds agent sessions "
                f"and credentials in argv; `tmux -L {sock} kill-server`",
            )
        )
    for sock in facts.stale_sockets:
        out.append(Finding("warn", f"stale tmux socket file (no server): {sock}"))
    return out


def _gascity(facts: HostFacts) -> list[Finding]:
    return generic_findings(facts) + gascity_findings(facts)


PROFILES = {
    "generic": generic_findings,
    "gascity": _gascity,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_profiles -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/machine-doctor/tools/profiles.py skills/machine-doctor/tools/test_profiles.py
git commit -m "feat(machine-doctor): generic + gascity leak profiles over HostFacts"
```

---

### Task 6: `machine_doctor.py` — collection + CLI

**Files:**
- Create: `skills/machine-doctor/tools/machine_doctor.py` (executable, uv header)

**Interfaces:**
- Consumes: everything above.
- Produces the CLI: `watch [--interval 30] [--spike-cpu 300] [--db PATH] [--state-dir PATH]`,
  `report [--since 6h] [--top 10]`, `at TIME [--tolerance 60]`,
  `snapshot [--profile generic|gascity] [--json]`.

Behavior requirements (each maps to a spec section):

- **Sampling:** each tick reads `/proc/stat`, `/proc/meminfo`, `/proc/vmstat`,
  `/proc/loadavg`, `/proc/uptime`, and every `/proc/<pid>/stat`. `cpu_pct` from
  jiffy deltas vs the previous tick. Stores `top_n(procs)` minus the sampler's
  own pid (self-exclusion at insert; full dumps still include everything).
- **Spikes:** `detect_spike` on each tick. Transition into spike prints one
  line with reasons + writes a full `render_tree` dump (cmdlines read only
  then). While a spike persists, further dumps are throttled to one per 600s —
  a one-hour build must not flush all 50 retained dumps. Recovery prints one
  line. `is_spike=1` on every spiking sample.
- **Prune:** `prune` + `prune_spike_dumps` at watch start AND end (finally:).
- **Clock:** `insert_sample` IntegrityError → warn and skip the tick; current
  ts < previous ts → print a clock-backwards warning.
- **Walk cost:** measure the /proc walk in ms; include it in the baseline line
  and every spike line.
- **report:** `--since` via `parse_duration` (bare number → clean error, exit 2).
  Zero samples in window → print `no samples in the last <since> — watch was not
  running; an empty window is not a quiet box` and exit 1. Otherwise a table:
  `COMM  ΣCPU%  PEAK-RSS  SAMPLES  SPAN`, plus a spike-count line naming the
  spikes dir when nonzero. Excludes the sampler by comm (`machine_doctor`) —
  belt-and-braces on top of insert-time self-exclusion.
- **at:** `resolve_at` (today-rollback rule); no sample within tolerance →
  `no sample near <resolved time> (nearest allowed gap 60s)` and exit 1. Found →
  print the totals row, the stored top procs, and — if `is_spike` — the dump
  path when `dump_path_for` exists.
- **snapshot:** two /proc walks ~1s apart for real interval cpu_pct; fills
  `HostFacts` (dolt cwds via `pgrep -x dolt` + `/proc/<pid>/cwd` readlink;
  orphan tmux: `pgrep -x tmux`, argv `-L <sock>` not in `USER_SOCKETS`, ppid 1;
  stale sockets from `/tmp/tmux-<uid>/` entries whose `tmux -L <name> ls`
  fails; zombies from PidStat.state == "Z"). Prints totals + top procs
  (redacted cmdlines) + profile findings; `--json` emits the same as JSON.
  Exit 1 if any finding has severity `fail`, else 0. The old `gc cities` call
  is dropped: the snapshot is /proc-only; the runbook covers registry checks.
- All subprocess use (`pgrep`, `tmux`) wrapped with timeouts and empty-string
  fallback, as in the old `_run`; `/bin/ps` is never needed.

- [ ] **Step 1: Write `machine_doctor.py`** — uv header + docstring + collection
  layer + `_build_app()` with the four commands, implementing exactly the
  behavior list above. Port `_run`, `_cmdline`, `_cwd`, `_tmux_socket_dir`,
  `_socket_live` from `gascity_doctor.py` unchanged.

- [ ] **Step 2: Set the executable bit**

```bash
chmod +x skills/machine-doctor/tools/machine_doctor.py
```

- [ ] **Step 3: Smoke-test every command end-to-end against a scratch state dir**

```bash
cd skills/machine-doctor/tools
D=$(mktemp -d)
./machine_doctor.py snapshot --profile gascity          # human report, exit code echoed
./machine_doctor.py watch --interval 2 --db $D/s.db --state-dir $D &  # ~8s then kill
sleep 8; kill %1
./machine_doctor.py report --since 6h --db $D/s.db      # shows the sampled procs
./machine_doctor.py at $(date +%H:%M) --db $D/s.db      # resolves to a sample
./machine_doctor.py report --since 6h --db $(mktemp -d)/empty.db  # exit 1, "not a quiet box" wording
```

Expected: snapshot exits 0 on a clean box; report ranks by ΣCPU%; `at` prints
the nearest sample; empty-window report exits 1 with the required wording.

- [ ] **Step 4: Run the full unit suite**

Run: `python3 -m unittest` — Expected: all tests pass (probe + store + profiles).

- [ ] **Step 5: Commit**

```bash
git add skills/machine-doctor/tools/machine_doctor.py
git commit -m "feat(machine-doctor): machine_doctor CLI — watch/report/at/snapshot"
```

---

### Task 7: Migration — remove gascity_doctor, update docs

**Files:**
- Delete: `skills/machine-doctor/tools/gascity_doctor.py`, `skills/machine-doctor/tools/test_gascity_doctor.py`
- Modify: `skills/machine-doctor/SKILL.md` (tier table row at line ~15, tier section at lines ~240-260, frontmatter description)
- Modify: `skills/machine-doctor/doctor-gascity.md` (lines 40-41, 51: tool invocations)

- [ ] **Step 1: Delete the superseded tool and tests**

```bash
git rm skills/machine-doctor/tools/gascity_doctor.py skills/machine-doctor/tools/test_gascity_doctor.py
```

All reused logic (redact, classify_dolt, is_watchdog, USER_SOCKETS, socket/
tmux collection) now lives in `md_probe.py` / `profiles.py` /
`machine_doctor.py` with equivalent tests; `State`/`diff_state`/
`render_snapshot` die with the old tool — their job is done by spike
transitions and profile findings.

- [ ] **Step 2: Update SKILL.md.** Replace the tier row
`| `/machine-doctor gascity` | Gas City (`gc`) leak hunt — orphaned dolt watchdogs, city tmux servers |`
with rows for the new commands:

```markdown
| `/machine-doctor watch`   | Record resource history — adaptive sampling, spike dumps               |
| `/machine-doctor report`  | Who has been hot over the last N hours (needs a prior `watch`)          |
| `/machine-doctor gascity` | Gas City (`gc`) leak hunt — now `snapshot --profile gascity`            |
```

Rewrite the `## Tier: Gas City` section (lines ~240-260) to a
`## Tier: Forensics (watch / report / at)` section documenting the four
commands, the on-demand limitation ("an empty window is not a quiet box"), and
a `### Gas City profile` subsection pointing at `doctor-gascity.md` with the
new invocation `tools/machine_doctor.py snapshot --profile gascity`.

- [ ] **Step 3: Update doctor-gascity.md** lines 40-41 and 51:
  `gascity_doctor.py snapshot` → `machine_doctor.py snapshot --profile gascity`,
  `gascity_doctor.py snapshot --json` → `machine_doctor.py snapshot --profile gascity --json`,
  `gascity_doctor.py watch --interval 60` → `machine_doctor.py watch --interval 60`
  (with a note that `watch` is now generic resource recording; the gascity
  leak checks live in `snapshot --profile gascity`).

- [ ] **Step 4: Verify no stale references and tests still pass**

```bash
grep -rn "gascity_doctor" skills/ docs/superpowers/plans/ && echo "STALE REFS" || echo clean
cd skills/machine-doctor/tools && python3 -m unittest
```

Expected: `clean` (spec/PR history may mention the old name; only skills/ must
be clean), all tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A skills/machine-doctor
git commit -m "refactor(machine-doctor): retire gascity_doctor; gascity is now a snapshot profile"
```

---

### Task 8: Push and rework PR #197

- [ ] **Step 1: Confirm PR #197 is still open** (never push to a merged PR's branch)

```bash
gh pr list --repo idvorkin/chop-conventions --head delegated/gascity-doctor --state all --json number,state
```

Expected: `[{"number":197,"state":"OPEN"}]`. If MERGED, stop — open a fresh PR instead.

- [ ] **Step 2: Push**

```bash
git push origin delegated/gascity-doctor
```

- [ ] **Step 3: Retitle and rewrite the PR body** to describe the machine
doctor: what it records, the four commands, spike model, gascity-as-profile,
link to the spec and this plan. Keep the PR number.

```bash
gh pr edit 197 --repo idvorkin/chop-conventions --title "machine-doctor: historical resource forensics (watch/report/at) + gascity profile" --body-file <body.md written from the template above>
```

- [ ] **Step 4: Close the bead** (`bd close ic-7bj`) and hand off per the
conservative profile: report changed files, test results, PR link, and the
`bd dolt stop` cleanup.
