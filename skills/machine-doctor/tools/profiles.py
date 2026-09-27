"""Known-leak profiles for machine-doctor snapshot.

Pure: each profile consumes a seeded HostFacts and returns Findings. The
collection I/O that fills HostFacts lives in machine_doctor.py. Two profiles is
a dict, not a plugin framework (N=2).
"""

from dataclasses import dataclass, field

from md_probe import JEKYLL_RE, ProcSample

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
