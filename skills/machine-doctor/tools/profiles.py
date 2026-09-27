"""Known-leak profiles for machine-doctor snapshot.

Pure: each profile consumes a seeded HostFacts and returns Findings. The
collection I/O that fills HostFacts lives in machine_doctor.py. Two profiles is
a dict, not a plugin framework (N=2).
"""

from dataclasses import dataclass, field

from md_probe import JEKYLL_RE, ProcSample, SleepEvent

# The human's own tmux sockets. Never a Gas City leak — flagging these trains
# the reader to ignore the tool.
USER_SOCKETS = frozenset({"default", "ssh"})

HOT_CPU_PCT = 300.0
MEM_AVAIL_FAIL_PCT = 10
# A jekyll preview older than this was started by an agent that moved on:
# one per blog worktree, 100-250MB each, and nothing ever stops them.
STALE_JEKYLL_S = 24 * 3600
# Interactive holders of a deleted cwd are a person's pane, not a leak; and
# wrappers (just, bash -c) carry the server's argv without being the server.
SHELL_COMMS = frozenset(
    {"bash", "sh", "zsh", "dash", "fish", "tmux", "just", "nvim", "vim"}
)
SWAP_USED_WARN_PCT = 90
DISK_WARN_PCT = 90
DISK_FAIL_PCT = 95
# A VM allowed more than this share of host RAM starves the host once the
# guest fills it: its pages land in the host's compressor and swap.
VM_MEM_WARN_PCT = 50
# WindowServer footprint past this is the long-uptime leak; logout resets it.
WINDOWSERVER_WARN_KB = 2 * 1024 * 1024


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
    # pid -> cwd for processes whose cwd was deleted (worktree removed under
    # a still-running server).
    deleted_cwds: dict[int, str] = field(default_factory=dict)
    jekyll_pids: list[int] = field(default_factory=list)
    load1: float = 0.0
    idle_pct: int | None = None
    mem_total_kb: int = 0
    mem_avail_kb: int = 0
    swap_total_kb: int = 0
    swap_free_kb: int = 0
    pressure: str | None = None  # macOS: normal | warn | critical
    compressor_kb: int | None = None  # macOS
    disks: dict[str, int] = field(default_factory=dict)  # mount -> used %
    speed_limit: int | None = None  # macOS thermal CPU_Speed_Limit %
    vm_mem_mib: dict[str, int] = field(default_factory=dict)  # VM name -> memory cap
    sleeps: list[SleepEvent] = field(default_factory=list)  # macOS, recent window


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


def is_jekyll_server(comm: str, cmdline: str) -> bool:
    m = JEKYLL_RE.search(cmdline)
    return comm not in SHELL_COMMS and bool(m) and m.group(2) != "build"


def is_deleted_cwd(link: str) -> bool:
    """The kernel appends ' (deleted)' to a /proc/<pid>/cwd whose dir is gone."""
    return link.endswith(" (deleted)")


def generic_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    for p in facts.procs:
        if (p.cpu_pct or 0.0) > HOT_CPU_PCT:
            out.append(
                Finding(
                    "warn", f"hot process: {p.comm} pid={p.pid} at {p.cpu_pct:.0f}% cpu"
                )
            )
    if (
        facts.mem_total_kb > 0
        and facts.mem_avail_kb * 100 < facts.mem_total_kb * MEM_AVAIL_FAIL_PCT
    ):
        out.append(
            Finding(
                "fail",
                f"memory pressure: MemAvailable {facts.mem_avail_kb // 1024}MB is under "
                f"{MEM_AVAIL_FAIL_PCT}% of {facts.mem_total_kb // 1024}MB",
            )
        )
    if facts.zombies:
        out.append(
            Finding(
                "warn",
                f"{len(facts.zombies)} zombie process(es): {sorted(facts.zombies)}",
            )
        )
    by_pid = {p.pid: p for p in facts.procs}
    for pid, cwd in sorted(facts.deleted_cwds.items()):
        p = by_pid.get(pid)
        name = p.comm if p else "?"
        if name in SHELL_COMMS:
            continue
        out.append(
            Finding(
                "warn",
                f"orphaned by a removed dir: {name} pid={pid} cwd={cwd} — "
                "nothing can reach its worktree any more; strong kill candidate",
            )
        )
    for pid in sorted(facts.jekyll_pids):
        p = by_pid.get(pid)
        if p and p.etime_s > STALE_JEKYLL_S:
            out.append(
                Finding(
                    "warn",
                    f"stale jekyll preview pid={pid} up {p.etime_s // 3600}h, "
                    f"{p.rss_kb // 1024}MB — stop with SIGINT (it ignores SIGTERM)",
                )
            )
    out += memory_findings(facts)
    out += disk_findings(facts)
    out += vm_findings(facts)
    out += power_findings(facts)
    return out


def memory_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    if facts.pressure in ("warn", "critical"):
        comp = (
            f", compressor holds {facts.compressor_kb // 1024}MB"
            if facts.compressor_kb is not None
            else ""
        )
        out.append(
            Finding(
                "fail" if facts.pressure == "critical" else "warn",
                f"macOS memory pressure is {facts.pressure}{comp} — rank by footprint "
                "(top -o mem), not RSS: compressed pages are invisible to RSS",
            )
        )
    if facts.swap_total_kb > 0:
        used_pct = (
            100 * (facts.swap_total_kb - facts.swap_free_kb) // facts.swap_total_kb
        )
        if used_pct >= SWAP_USED_WARN_PCT:
            out.append(
                Finding(
                    "warn",
                    f"swap {used_pct}% used ({facts.swap_free_kb // 1024}MB free of "
                    f"{facts.swap_total_kb // 1024}MB)",
                )
            )
    for p in facts.procs:
        if p.comm == "WindowServer" and p.rss_kb > WINDOWSERVER_WARN_KB:
            out.append(
                Finding(
                    "warn",
                    f"WindowServer footprint {p.rss_kb // 1024}MB — long-uptime leak; "
                    "logging out and back in resets it",
                )
            )
    return out


def disk_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    for mount, pct in sorted(facts.disks.items()):
        if pct >= DISK_FAIL_PCT:
            out.append(Finding("fail", f"disk {mount} is {pct}% full"))
        elif pct >= DISK_WARN_PCT:
            out.append(Finding("warn", f"disk {mount} is {pct}% full"))
    return out


def vm_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    if facts.mem_total_kb <= 0:
        return out
    host_mib = facts.mem_total_kb // 1024
    for name, mib in sorted(facts.vm_mem_mib.items()):
        if mib * 100 > host_mib * VM_MEM_WARN_PCT:
            half = host_mib // 2 // 1024 * 1024
            out.append(
                Finding(
                    "warn",
                    f"{name} may take {mib}MiB of {host_mib}MiB host RAM "
                    f"({100 * mib // host_mib}%) — when the guest fills it, the host "
                    f"swaps; e.g. `orb config set memory_mib {half}`",
                )
            )
    return out


def power_findings(facts: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    if facts.speed_limit is not None and facts.speed_limit < 100:
        out.append(
            Finding(
                "warn", f"thermal throttling: CPU speed limited to {facts.speed_limit}%"
            )
        )
    odd = [e for e in facts.sleeps if e.unexpected]
    if odd:
        last = odd[-1]
        out.append(
            Finding(
                "warn",
                f"{len(odd)} forced/thermal sleep(s) recently, last {last.when} "
                f"({last.reason}) — `caffeinate` cannot block these; run `sleeps` to "
                "name the requester",
            )
        )
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
            out.append(
                Finding(
                    "fail", f"city dolt server pid={pid} {cwd} — gc teardown missed it"
                )
            )
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
