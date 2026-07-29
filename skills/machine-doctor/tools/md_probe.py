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
    by_cpu = sorted(
        procs, key=lambda p: p.cpu_pct if p.cpu_pct is not None else -1.0, reverse=True
    )[:n]
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
