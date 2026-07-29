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
