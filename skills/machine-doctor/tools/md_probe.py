"""Pure probe logic for machine-doctor: /proc and macOS tool parsing, spike detection.

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
    # Reclaimable slab (dentry/inode cache). Counts as "used" in `free` but is
    # not in any process's RSS, so it is most of the used-minus-RSS gap.
    sreclaimable_kb: int = 0
    # macOS only: the kernel's own verdict (normal | warn | critical). Linux has
    # no equivalent single number; MemAvailable is the signal there.
    pressure: str | None = None


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
        sreclaimable_kb=vals.get("SReclaimable", 0),
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


# ---------------------------------------------------------------------------
# Memory by app: RSS grouped by what a human would call the program
# ---------------------------------------------------------------------------

# Interpreters whose comm says nothing: name them by the script they run.
_INTERP_RE = re.compile(r"^(python3?|node|bun|deno|ruby|uv|uvx)[\d.]*$")
JEKYLL_RE = re.compile(r"(^|/)jekyll\s+(serve|server|build)\b")


def _interp_script(argv: list[str]) -> str:
    """First non-flag argument after the interpreter, skipping `uv run` and
    taking the module name for `-m mod`."""
    rest = argv[1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "-m" and i + 1 < len(rest):
            return rest[i + 1]
        if a in ("-c", "-e"):  # inline code: no script to name
            return ""
        if a in ("run", "tool", "exec") or a.startswith("-"):
            i += 1
            continue
        return a.rsplit("/", 1)[-1]
    return ""


def app_name(comm: str, cmdline: str) -> str:
    """Collapse one process to an app label. Families that fan out into many
    PIDs (claude, jekyll, dolt servers) collapse to one row; bare interpreters
    are named by their script (`python3:serve.py`), else by comm."""
    if comm.startswith("claude"):
        return "claude"
    if "dolt sql-server" in cmdline:
        return "dolt sql-server"
    if JEKYLL_RE.search(cmdline):
        return "jekyll"
    if "pytest" in cmdline:
        return "pytest"
    argv = cmdline.split()
    base = argv[0].rsplit("/", 1)[-1] if argv else comm
    m = _INTERP_RE.match(base) or _INTERP_RE.match(comm)
    if m:
        script = _interp_script(argv)
        return f"{m.group(1)}:{script}" if script else m.group(1)
    return comm


@dataclass(frozen=True)
class AppMem:
    name: str
    rss_kb: int
    count: int


def mem_by_app(
    procs: list[ProcSample], cmdlines: dict[int, str], top: int = 15
) -> tuple[list[AppMem], int, int]:
    """(top rows by RSS, everything-else KB, total KB). Total is the sum of ALL
    RSS, so rows + everything-else == total exactly. pytest-xdist workers run
    as anonymous `python -c ...`; they inherit the parent's `pytest` label.
    RSS double-counts shared pages, so treat this as a ranking, not a ledger."""
    by_pid = {p.pid: p for p in procs}
    labels = {p.pid: app_name(p.comm, cmdlines.get(p.pid, "")) for p in procs}
    for p in procs:
        parent = labels.get(p.ppid)
        if parent == "pytest" and labels[p.pid].startswith("python"):
            labels[p.pid] = "pytest"
    rss: dict[str, int] = {}
    count: dict[str, int] = {}
    for pid, name in labels.items():
        rss[name] = rss.get(name, 0) + by_pid[pid].rss_kb
        count[name] = count.get(name, 0) + 1
    total = sum(rss.values())
    ranked = sorted(rss, key=lambda n: -rss[n])[:top]
    rows = [AppMem(n, rss[n], count[n]) for n in ranked]
    return rows, total - sum(r.rss_kb for r in rows), total


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

    nxt.low_idle = (
        nxt.low_idle + 1 if (idle_pct is not None and idle_pct < cfg.idle_pct) else 0
    )
    if nxt.low_idle >= cfg.consecutive:
        reasons.append(f"idle {idle_pct}% for {nxt.low_idle} samples")

    nxt.swapping = nxt.swapping + 1 if swap_out > 0 else 0
    if nxt.swapping >= cfg.consecutive:
        reasons.append(f"swap-out {swap_out} KB/s for {nxt.swapping} samples")

    if (
        mem.mem_total_kb > 0
        and mem.mem_avail_kb * 100 < mem.mem_total_kb * cfg.mem_avail_pct
    ):
        reasons.append(
            f"MemAvailable {mem.mem_avail_kb // 1024}MB is under "
            f"{cfg.mem_avail_pct}% of {mem.mem_total_kb // 1024}MB"
        )
    if mem.pressure == "critical":
        reasons.append("macOS memory pressure is critical")

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
            f"{'  ' * depth}{p.pid} {p.comm} cpu={cpu} mem={p.rss_kb // 1024}MB "
            f"etime={p.etime_s}s ppid={p.ppid} {cl}".rstrip()
        )
        for k in sorted(kids.get(p.pid, []), key=lambda x: x.pid):
            emit(k, depth + 1)

    for r in sorted(roots, key=lambda x: x.pid):
        emit(r, 0)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# macOS: no /proc, so the same facts come from ps / top / sysctl / vm_stat /
# pmset text. Same contract as above: text in, values out.
# ---------------------------------------------------------------------------


def cpu_totals_from_ticks(user: int, system: int, idle: int, nice: int) -> CpuTotals:
    """host_statistics(HOST_CPU_LOAD_INFO) ticks -> the /proc/stat shape, so
    idle_pct_between works unchanged."""
    return CpuTotals(busy=user + system + nice, total=user + system + idle + nice)


def parse_ps_duration(text: str) -> float:
    """ps TIME / ETIME: '[dd-][hh:]mm:ss[.cc]' -> seconds. TIME on macOS runs
    minutes past 59 ('3480:00.89'), which the positional sum handles."""
    days = 0
    t = text.strip()
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return days * 86400 + secs


@dataclass(frozen=True)
class PsRow:
    pid: int
    ppid: int
    state: str
    cpu_s: float  # cumulative user+system CPU seconds
    etime_s: int
    rss_kb: int
    comm: str


def parse_ps_rows(text: str) -> list[PsRow]:
    """`ps -Ao pid=,ppid=,stat=,time=,etime=,rss=,comm=`. comm is last because
    it is a full executable path that may contain spaces; keep its basename to
    match Linux's short comm."""
    rows: list[PsRow] = []
    for line in text.splitlines():
        f = line.split(None, 6)
        if len(f) < 7 or not f[0].isdigit():
            continue
        try:
            rows.append(
                PsRow(
                    pid=int(f[0]),
                    ppid=int(f[1]),
                    state=f[2][0],
                    cpu_s=parse_ps_duration(f[3]),
                    etime_s=int(parse_ps_duration(f[4])),
                    rss_kb=int(f[5]),
                    comm=f[6].rstrip("/").rsplit("/", 1)[-1],
                )
            )
        except ValueError:
            continue
    return rows


_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([BKMGT])[+-]?$")
_SIZE_KB = {"B": 1 / 1024, "K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}


def parse_top_size(text: str) -> int | None:
    """top's '12G+', '3074M-', '750K', '0B' -> KB."""
    m = _SIZE_RE.match(text.strip())
    return round(float(m.group(1)) * _SIZE_KB[m.group(2)]) if m else None


def parse_top_mem(text: str) -> dict[int, int]:
    """`top -l 1 -stats pid,mem` -> {pid: footprint_kb}.

    On macOS this is the number that matters, not RSS: it includes compressed
    pages. A VM whose memory sits in the compressor shows a few hundred MB of
    RSS and a 12G footprint.
    """
    out: dict[int, int] = {}
    body = False
    for line in text.splitlines():
        f = line.split()
        if f[:2] == ["PID", "MEM"]:
            body = True
            continue
        if body and len(f) >= 2 and f[0].isdigit():
            kb = parse_top_size(f[1])
            if kb is not None:
                out[int(f[0])] = kb
    return out


_SWAP_RE = re.compile(r"(total|used|free)\s*=\s*([\d.]+)([MG])")


def parse_swapusage(text: str) -> tuple[int, int]:
    """`sysctl vm.swapusage` -> (total_kb, free_kb). macOS grows and shrinks the
    swap file on demand, so 'free' can fall while pressure is easing."""
    vals = {
        k: float(v) * (1024 if u == "M" else 1024**2)
        for k, v, u in _SWAP_RE.findall(text)
    }
    return round(vals.get("total", 0)), round(vals.get("free", 0))


def parse_vm_stat(text: str) -> dict[str, int]:
    """`vm_stat` -> {'page_kb', 'swapouts', 'compressor_kb'} (cumulative swapouts
    in pages; compressor_kb is physical memory the compressor occupies)."""
    out: dict[str, int] = {}
    m = re.search(r"page size of (\d+) bytes", text)
    page_kb = int(m.group(1)) // 1024 if m else 16
    out["page_kb"] = page_kb
    for line in text.splitlines():
        key, _, val = line.partition(":")
        val = val.strip().rstrip(".")
        if not val.isdigit():
            continue
        if key.strip() == "Swapouts":
            out["swapouts"] = int(val)
        elif key.strip() == "Pages occupied by compressor":
            out["compressor_kb"] = int(val) * page_kb
    return out


def pressure_name(level: int | None) -> str | None:
    """kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical."""
    return (
        {1: "normal", 2: "warn", 4: "critical"}.get(level)
        if level is not None
        else None
    )


def parse_speed_limit(text: str) -> int | None:
    """`pmset -g therm` -> CPU_Speed_Limit percent; None when never throttled."""
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", text)
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class SleepEvent:
    when: str  # 'YYYY-MM-DD HH:MM:SS' local, as pmset prints it
    reason: str
    pid: int | None  # set for 'Software Sleep pid=N' (a forced sleep)

    @property
    def forced(self) -> bool:
        """A process asked for sleep (`pmset sleepnow`, the Apple menu, a
        script) - often the user. Gets through `caffeinate`; worth naming."""
        return self.pid is not None

    @property
    def thermal(self) -> bool:
        """Too hot to stay awake - a problem."""
        return "Thermal" in self.reason


_SLEEP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) [+-]\d{4} Sleep\s+Entering Sleep state due to '([^']*)'"
)


def parse_pmset_sleeps(text: str) -> list[SleepEvent]:
    """Every non-maintenance sleep in `pmset -g log`. Maintenance sleeps are
    the dark-wake network chatter (every minute or so overnight) - noise."""
    out: list[SleepEvent] = []
    for line in text.splitlines():
        m = _SLEEP_RE.match(line)
        if not m or m.group(2) == "Maintenance Sleep":
            continue
        pid = re.search(r"Software Sleep pid=(\d+)", m.group(2))
        out.append(
            SleepEvent(m.group(1), m.group(2), int(pid.group(1)) if pid else None)
        )
    return out


def parse_df(text: str) -> dict[str, int]:
    """`df -Pk` -> {mount: used %}. The mount is the last field and may hold
    spaces, so split off exactly the five numeric columns first."""
    out: dict[str, int] = {}
    for line in text.splitlines()[1:]:
        m = re.match(r"^\S.*?\s+\d+\s+\d+\s+\d+\s+(\d+)%\s+(/.*)$", line)
        if m:
            out[m.group(2)] = int(m.group(1))
    return out


_DATA_MOUNTS = ("/", "/System/Volumes/Data", "/home", "/tmp", "/var")


def select_mounts(disks: dict[str, int]) -> dict[str, int]:
    """Keep the volumes a person fills: the system/data volumes and top-level
    /Volumes/<name> drives. Everything else is full by design and would only
    train the reader to ignore disk warnings: read-only simulator images, /dev,
    autofs `home`, macOS's sealed system sub-volumes."""
    return {
        m: pct
        for m, pct in disks.items()
        if m in _DATA_MOUNTS or re.fullmatch(r"/Volumes/[^/]+", m)
    }


def parse_catcher_log(text: str) -> dict[str, str]:
    """sleep-catcher.sh's log -> {event time: the indented detail lines under
    its 'FORCED sleep (...) at <time>' header}."""
    out: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        m = re.search(
            r"FORCED sleep \(.*\) at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line
        )
        if m:
            current = m.group(1)
            out[current] = ""
        elif current and (line.startswith("    ") or re.match(r"^\S+ \S+   ", line)):
            detail = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", "", line)
            out[current] += detail + "\n"
        else:
            current = None
    return out
