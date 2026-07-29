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
            out.append(
                Finding("warn", f"hot process: {p.comm} pid={p.pid} at {p.cpu_pct:.0f}% cpu")
            )
    if facts.mem_total_kb > 0 and facts.mem_avail_kb * 100 < facts.mem_total_kb * MEM_AVAIL_FAIL_PCT:
        out.append(
            Finding(
                "fail",
                f"memory pressure: MemAvailable {facts.mem_avail_kb // 1024}MB is under "
                f"{MEM_AVAIL_FAIL_PCT}% of {facts.mem_total_kb // 1024}MB",
            )
        )
    if facts.zombies:
        out.append(
            Finding("warn", f"{len(facts.zombies)} zombie process(es): {sorted(facts.zombies)}")
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
                Finding("fail", f"city dolt server pid={pid} {cwd} — gc teardown missed it")
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
