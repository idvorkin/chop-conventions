"""Known-leak profiles for machine-doctor snapshot.

Pure: each profile consumes a seeded HostFacts and returns Findings. The
collection I/O that fills HostFacts lives in machine_doctor.py. Two profiles is
a dict, not a plugin framework (N=2).
"""

import os
import re
from dataclasses import dataclass, field, replace

from md_probe import (
    JEKYLL_RE,
    DevServer,
    ProcSample,
    SleepEvent,
    app_name,
    fmt_age,
    thermal_name,
)

# The human's own tmux sockets. Never a Gas City leak — flagging these trains
# the reader to ignore the tool.
USER_SOCKETS = frozenset({"default", "ssh"})

HOT_CPU_PCT = 300.0
MEM_AVAIL_FAIL_PCT = 10
# A dev server older than this was started by an agent that moved on: one
# jekyll preview per blog worktree, 100-250MB each, and nothing stops them.
# 47 of them once held 7.4GB of a 13GB swap on a 12GB VM.
STALE_DEV_SERVER_S = 24 * 3600
# More listening previews than this at once is a pile-up, not a workflow
# (lavish pollers hold no port and are not counted).
DEV_SERVERS_WARN = 10
# RSS + swap across every dev server.
DEV_SERVER_MEM_WARN_KB = 2 * 1024 * 1024
DEV_SERVER_MEM_WARN_PCT = 15
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
# Each booted iOS/watchOS simulator runs a whole OS worth of daemons; eight
# booted at once meant 2,400 processes and load ~980 on a 16GB laptop.
BOOTED_SIMS_WARN = 3
# iOS 27 simulators keep ~300 MB of wallpaper snapshots per boot and never evict
# them; 26 GB across two test-ladder devices in a month.
SIM_POSTERS_WARN_KB = 5 * 1024 * 1024
# Footprint of everything under CoreSimulator. It spreads over hundreds of
# small processes, so no per-process sort shows it; 10+ GB has been seen.
SIM_MEM_WARN_KB = 6 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    severity: str  # "note" | "warn" | "fail"; a note is never a problem
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
    dev_servers: list[DevServer] = field(default_factory=list)
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
    thermal: int | None = None  # macOS thermal pressure 0 nominal .. 4 sleeping
    vm_mem_mib: dict[str, int] = field(default_factory=dict)  # VM name -> memory cap
    sleeps: list[SleepEvent] = field(default_factory=list)  # macOS, recent window
    booted_sims: list[str] = field(default_factory=list)  # macOS simulator names
    sim_posters_kb: dict[str, int] = field(default_factory=dict)  # sim name -> KB
    sim_mem_kb: int = 0  # macOS: footprint of everything under CoreSimulator


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


_LAVISH_RE = re.compile(r"lavish-axi\S*\s+(server|poll)\b")


def server_kind(comm: str, cmdline: str) -> str | None:
    """Classify a dev/preview server, or None. Shell wrappers carry the
    server's argv without being it; `uv run serve.py` is folded into its
    python child later by fold_wrappers."""
    if comm in SHELL_COMMS:
        return None
    if is_jekyll_server(comm, cmdline):
        return "jekyll"
    m = _LAVISH_RE.search(cmdline)
    if m:
        return "lavish" if m.group(1) == "server" else "lavish-poll"
    app = app_name(comm, cmdline)
    for kind in ("http.server", "serve.py"):
        if app.endswith(":" + kind):
            return kind
    return None


def server_target(kind: str, cmdline: str, cwd: str) -> str:
    """The directory whose git branch the server serves: the script's own dir
    for an absolute `serve.py`, the reviewed file's dir for a lavish poller,
    else the working directory (jekyll and http.server serve their cwd)."""
    argv = cmdline.split()
    if kind == "serve.py":
        for a in argv:
            if a.endswith("serve.py") and a.startswith("/"):
                return os.path.dirname(a)
    if kind == "lavish-poll" and "poll" in argv:
        i = argv.index("poll")
        if i + 1 < len(argv):
            target = os.path.join(cwd, argv[i + 1]) if cwd else argv[i + 1]
            if target.startswith("/"):
                return os.path.dirname(target)
    return cwd


def fold_wrappers(servers: list[DevServer], ppid_of: dict[int, int]) -> list[DevServer]:
    """`uv run --script serve.py` forks the python that holds the port; both
    classify as serve.py. Count one server: the child, carrying the parent's
    memory and ports."""
    by_pid = {s.pid: s for s in servers}
    merged = dict(by_pid)
    for s in servers:
        parent = by_pid.get(ppid_of.get(s.pid, 0))
        if parent is None or parent.kind != s.kind or parent.pid not in merged:
            continue
        child = merged[s.pid]
        merged[s.pid] = replace(
            child,
            rss_kb=child.rss_kb + parent.rss_kb,
            swap_kb=child.swap_kb + parent.swap_kb,
            ports=tuple(sorted(set(child.ports) | set(parent.ports))),
            etime_s=max(child.etime_s, parent.etime_s),
        )
        del merged[parent.pid]
    return [merged[s.pid] for s in servers if s.pid in merged]


def where(s: DevServer) -> str:
    """`repo@branch` for a finding; the cwd when it is not in a repo."""
    if s.repo:
        return f"{os.path.basename(s.repo)}@{s.branch or '?'}"
    return s.cwd or "?"


def dev_server_findings(servers: list[DevServer], mem_total_kb: int) -> list[Finding]:
    """Report only: never a fail, never a kill. Each finding names the branch,
    so the reader can check whether its PR merged before stopping anything."""
    out: list[Finding] = []
    listening = [s for s in servers if s.kind != "lavish-poll"]
    if len(listening) >= DEV_SERVERS_WARN:
        kinds: dict[str, int] = {}
        for s in listening:
            kinds[s.kind] = kinds.get(s.kind, 0) + 1
        mix = ", ".join(
            f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])
        )
        out.append(Finding("warn", f"{len(listening)} dev servers running ({mix})"))
    total = sum(s.mem_kb for s in servers)
    swap = sum(s.swap_kb for s in servers)
    if total >= DEV_SERVER_MEM_WARN_KB or (
        mem_total_kb and total * 100 >= mem_total_kb * DEV_SERVER_MEM_WARN_PCT
    ):
        out.append(
            Finding(
                "warn",
                f"dev servers hold {total // 1024}MB (of which {swap // 1024}MB in swap) "
                "— idle previews get swapped out, so RSS alone undercounts them",
            )
        )
    old = sorted(
        (s for s in servers if s.etime_s > STALE_DEV_SERVER_S), key=lambda s: -s.etime_s
    )
    if old:
        shown = ", ".join(
            f"{s.kind} pid={s.pid} {fmt_age(s.etime_s)} {where(s)}" for s in old[:5]
        )
        more = f" (+{len(old) - 5} more)" if len(old) > 5 else ""
        hint = (
            "; jekyll stops on SIGINT, not SIGTERM"
            if any(s.kind == "jekyll" for s in old)
            else ""
        )
        out.append(
            Finding(
                "warn",
                f"{len(old)} dev server(s) up over {STALE_DEV_SERVER_S // 3600}h: "
                f"{shown}{more} — stop the ones whose branch has merged{hint}",
            )
        )
    return out


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
    out += dev_server_findings(facts.dev_servers, facts.mem_total_kb)
    out += memory_findings(facts)
    out += disk_findings(facts)
    out += vm_findings(facts)
    out += power_findings(facts)
    out += sim_findings(facts)
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


def sim_findings(facts: HostFacts) -> list[Finding]:
    """No single simulator process looks guilty in a CPU sort; the cost is the
    count. Test pipelines that boot a fresh device per run and never shut it
    down are the usual source."""
    out: list[Finding] = []
    n = len(facts.booted_sims)
    if n >= BOOTED_SIMS_WARN or facts.sim_mem_kb >= SIM_MEM_WARN_KB:
        names = ", ".join(facts.booted_sims) or "none listed"
        mem = (
            f", {facts.sim_mem_kb / 1048576:.1f}GB in memory"
            if facts.sim_mem_kb
            else ""
        )
        out.append(
            Finding(
                "warn",
                f"{n} simulators booted ({names}){mem} — each is a whole OS; "
                "`xcrun simctl shutdown <udid>` the ones no build is using",
            )
        )
    total = sum(facts.sim_posters_kb.values())
    if total >= SIM_POSTERS_WARN_KB:
        name, kb = max(facts.sim_posters_kb.items(), key=lambda kv: kv[1])
        out.append(
            Finding(
                "warn",
                f"simulator wallpaper caches hold {total / 1048576:.1f}GB "
                f"(most: {name} {kb / 1048576:.1f}GB), ~300MB more per boot — "
                "`machine_doctor.py sims --prune` clears the shut-down ones",
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
    if facts.thermal:
        out.append(
            Finding("warn", f"thermal pressure is {thermal_name(facts.thermal)}")
        )
    hot = [e for e in facts.sleeps if e.thermal]
    if hot:
        out.append(
            Finding(
                "warn",
                f"{len(hot)} thermal sleep(s) recently, last {hot[-1].when} "
                f"({hot[-1].reason}) — find the load",
            )
        )
    forced = [e for e in facts.sleeps if e.forced]
    if forced:
        out.append(
            Finding(
                "note",
                f"{len(forced)} sleep(s) requested by a process (pmset sleepnow, "
                f"Apple menu, a script), last {forced[-1].when} — `sleeps` names who",
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
