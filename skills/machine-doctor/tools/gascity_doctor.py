#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""
Gas City (`gc`) runtime doctor — what is actually still running, and is it hurting?

Gas City leaves side-processes behind that its own teardown commands do not reap:
managed-dolt scope watchdogs and per-city tmux servers both reparent to PID 1 and
outlive `gc stop`. `gc cities` reporting "No cities registered" says nothing about
them. This tool looks at the process table instead of asking gc.

    gascity_doctor.py snapshot            # one-shot: what's running right now
    gascity_doctor.py snapshot --json     # ...as JSON
    gascity_doctor.py watch               # poll; print only when state CHANGES
    gascity_doctor.py watch --interval 30

`watch` emits one line per transition and nothing otherwise, so it is safe to hand
to a long-running monitor. Silence means nothing changed.

Exit codes: snapshot returns 1 if any check failed, else 0.
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

OK = "✓"
WARN = "⚠"
BAD = "✗"

# `ps` is aliased to a pager wrapper on some dev boxes and fails silently with
# positional args. Always use the real binary.
PS = "/bin/ps"

CPU_IDLE_ALERT_PCT = 50  # sustained idle below this is worth reporting
CPU_ALERT_SAMPLES = 2  # consecutive low samples before alerting

# The human's own tmux sockets. Never a Gas City leak, so never reported as
# orphaned or stale — flagging these trains the reader to ignore the tool.
USER_SOCKETS = frozenset({"default", "ssh"})


# ---------------------------------------------------------------------------
# Pure logic (no subprocess, no /proc) — unit-tested in test_gascity_doctor.py
# ---------------------------------------------------------------------------

SECRET_ENV_RE = re.compile(
    r"(-e\s+)([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=\S+"
)


def redact(cmdline: str) -> str:
    """Strip secret values out of a process command line.

    gc spawns agent sessions with credentials in argv (`tmux ... -e
    ANTHROPIC_API_KEY=sk-...`), so anything that prints a cmdline must scrub it
    first — argv is world-readable via /proc.
    """
    return SECRET_ENV_RE.sub(r"\1\2=<redacted>", cmdline)


def classify_dolt(cwd: str) -> str:
    """Classify a dolt server by its working directory.

    `city` servers belong to a Gas City scope and are the ones gc should have
    reaped. `beads-repo` servers are spawned on demand by `bd` for an ordinary
    repo's own store and are none of gc's business.
    """
    if not cwd:
        return "unknown"
    if "/.gc/" in cwd or cwd.endswith("/.gc"):
        return "city"
    if "/.beads/" in cwd:
        return "beads-repo"
    return "unknown"


def is_watchdog(cmdline: str) -> bool:
    """True for gc's managed-dolt scope watchdog.

    This is the process that survives `gc stop` + `gc supervisor stop`, keeps a
    dolt server alive, and is invisible to `gc cities`.
    """
    return "__gc-managed-dolt-scope-watchdog" in cmdline


def parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.split()
    return (float(parts[0]), float(parts[1]), float(parts[2]))


def parse_vmstat_idle(text: str) -> int | None:
    """Extract idle% from the last data row of `vmstat`.

    Load average counts runnable *and* uninterruptible tasks, so it can sit high
    while the CPU is mostly idle. Idle% is the number that answers "is the CPU
    actually busy" — report both or the reader draws the wrong conclusion.
    """
    rows = [r for r in text.strip().splitlines() if r.strip()]
    if not rows:
        return None
    fields = rows[-1].split()
    if len(fields) < 15:
        return None
    try:
        return int(fields[14])
    except ValueError:
        return None


@dataclass
class State:
    """A point-in-time picture of Gas City's runtime footprint."""

    gc_pids: list[int] = field(default_factory=list)
    watchdog_pids: list[int] = field(default_factory=list)
    supervisor_pids: list[int] = field(default_factory=list)
    dolt: dict[int, str] = field(default_factory=dict)  # pid -> cwd
    live_sockets: list[str] = field(default_factory=list)
    stale_sockets: list[str] = field(default_factory=list)
    orphan_tmux: dict[int, str] = field(default_factory=dict)  # pid -> label
    cities: list[str] = field(default_factory=list)
    load1: float = 0.0
    idle_pct: int | None = None

    @property
    def running(self) -> bool:
        """Is any Gas City runtime process alive?

        Deliberately includes watchdogs and orphaned per-city tmux servers: those
        are exactly the things a naive `gc cities` check misses.
        """
        return bool(
            self.gc_pids
            or self.supervisor_pids
            or self.watchdog_pids
            or self.orphan_tmux
            or self.city_dolt
        )

    @property
    def city_dolt(self) -> dict[int, str]:
        return {p: c for p, c in self.dolt.items() if classify_dolt(c) == "city"}


def diff_state(prev: State, cur: State) -> list[str]:
    """Transitions worth telling a human about.

    Returns [] when nothing meaningful changed — `watch` relies on that to stay
    quiet. Only edges are reported, never steady state, so a monitor consuming
    this does not spam.
    """
    events: list[str] = []

    if cur.running and not prev.running:
        events.append(
            f"GAS CITY RUNNING: gc={len(cur.gc_pids)} supervisors={len(cur.supervisor_pids)} "
            f"watchdogs={len(cur.watchdog_pids)} city-dolt={len(cur.city_dolt)}"
        )
    elif prev.running and not cur.running:
        events.append("gas city fully stopped (no gc/supervisor/watchdog/city-dolt)")

    new_wd = set(cur.watchdog_pids) - set(prev.watchdog_pids)
    if new_wd:
        events.append(
            f"ORPHANED DOLT WATCHDOG appeared: pid(s) {sorted(new_wd)} "
            "— survives `gc stop`; reap with `gc stop` in the city dir or kill the pid"
        )

    new_dolt = set(cur.dolt) - set(prev.dolt)
    for pid in sorted(new_dolt):
        events.append(f"new dolt server pid={pid} [{classify_dolt(cur.dolt[pid])}] {cur.dolt[pid]}")

    new_orphans = set(cur.orphan_tmux) - set(prev.orphan_tmux)
    for pid in sorted(new_orphans):
        events.append(
            f"ORPHANED CITY TMUX SERVER pid={pid} ({cur.orphan_tmux[pid]}) "
            "— holds agent sessions + credentials; `tmux -L <socket> kill-server`"
        )

    return events


def render_snapshot(state: State) -> tuple[str, int]:
    """Human-readable report. Returns (text, failure_count)."""
    lines: list[str] = ["=== Gas City Doctor ==="]
    failures = 0

    lines.append("\nREGISTERED CITIES:")
    if state.cities:
        for c in state.cities:
            lines.append(f"  · {c}")
        lines.append(f"  {WARN} {len(state.cities)} registered — supervisor reconciles each one")
    else:
        lines.append(f"  {OK} none registered")

    lines.append("\nRUNTIME PROCESSES:")
    if state.gc_pids:
        lines.append(f"  {WARN} {len(state.gc_pids)} gc process(es): {state.gc_pids}")
    else:
        lines.append(f"  {OK} no gc processes")
    if state.supervisor_pids:
        lines.append(f"  {WARN} supervisor running: {state.supervisor_pids}")
    else:
        lines.append(f"  {OK} no supervisor")
    if state.watchdog_pids:
        failures += 1
        lines.append(
            f"  {BAD} {len(state.watchdog_pids)} orphaned dolt watchdog(s): {state.watchdog_pids}"
        )
        lines.append("      survives `gc stop`/`gc supervisor stop`; invisible to `gc cities`")
    else:
        lines.append(f"  {OK} no orphaned dolt watchdogs")

    lines.append("\nDOLT SERVERS:")
    if not state.dolt:
        lines.append(f"  {OK} none running")
    for pid, cwd in sorted(state.dolt.items()):
        kind = classify_dolt(cwd)
        if kind == "city":
            failures += 1
            lines.append(f"  {BAD} pid={pid} [city] {cwd}")
        elif kind == "beads-repo":
            lines.append(f"  {OK} pid={pid} [beads-repo, not gc's] {cwd}")
        else:
            lines.append(f"  {WARN} pid={pid} [unknown] {cwd}")

    lines.append("\nTMUX:")
    if state.orphan_tmux:
        failures += 1
        for pid, label in sorted(state.orphan_tmux.items()):
            lines.append(f"  {BAD} orphaned city tmux server pid={pid} ({label})")
        lines.append("      holds agent sessions and credentials in argv")
    else:
        lines.append(f"  {OK} no orphaned city tmux servers")
    for s in state.stale_sockets:
        lines.append(f"  {WARN} stale socket file (no server): {s}")
    for s in state.live_sockets:
        lines.append(f"  · live socket: {s}")

    lines.append("\nCPU:")
    idle = "unknown" if state.idle_pct is None else f"{state.idle_pct}%"
    lines.append(f"  · load1={state.load1}  idle={idle}")
    if state.idle_pct is not None and state.idle_pct < CPU_IDLE_ALERT_PCT:
        lines.append(f"  {WARN} CPU genuinely busy (idle {idle})")
    else:
        lines.append(f"  {OK} CPU not saturated — a high load average here is queueing, not CPU")

    lines.append("")
    lines.append("=" * 60)
    lines.append(f"{OK} clean" if failures == 0 else f"{BAD} {failures} problem area(s)")
    return "\n".join(lines), failures


# ---------------------------------------------------------------------------
# Subprocess / /proc layer (thin wrappers, kept out of the pure logic above)
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def _pids_named(name: str) -> list[int]:
    out = _run(["pgrep", "-x", name])
    return [int(p) for p in out.split() if p.isdigit()]


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _tmux_socket_dir() -> Path:
    return Path(f"/tmp/tmux-{os.getuid()}")


def _socket_live(name: str) -> bool:
    try:
        r = subprocess.run(
            ["tmux", "-L", name, "ls"], capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def collect_state(gc_bin: str = "gc") -> State:
    """Gather the current picture. All the messy I/O lives here."""
    st = State()

    for pid in _pids_named("gc"):
        cl = _cmdline(pid)
        if is_watchdog(cl):
            st.watchdog_pids.append(pid)
        else:
            st.gc_pids.append(pid)

    # `pgrep -f 'supervisor run'` would also match the shell running this script,
    # so match on the gc binary's own argv instead.
    for pid in _pids_named("gc"):
        if "supervisor run" in _cmdline(pid):
            st.supervisor_pids.append(pid)
            if pid in st.gc_pids:
                st.gc_pids.remove(pid)

    for pid in _pids_named("dolt"):
        st.dolt[pid] = _cwd(pid)

    sock_dir = _tmux_socket_dir()
    if sock_dir.is_dir():
        for sock in sorted(p.name for p in sock_dir.iterdir()):
            if sock in USER_SOCKETS:
                continue
            if _socket_live(sock):
                st.live_sockets.append(sock)
            else:
                st.stale_sockets.append(sock)

    # An orphaned city tmux server: PPID 1, argv names a -L socket that is not
    # the user's own default/ssh socket.
    for pid in _pids_named("tmux"):
        cl = _cmdline(pid)
        m = re.search(r"-L\s+(\S+)", cl)
        if not m:
            continue
        sock = m.group(1)
        if sock in USER_SOCKETS:
            continue
        try:
            ppid = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
        except (OSError, IndexError, ValueError):
            continue
        if ppid == 1:
            st.orphan_tmux[pid] = sock

    cities_out = _run([gc_bin, "cities"], timeout=45)
    for line in cities_out.splitlines():
        line = line.strip()
        if not line or line.startswith("NAME") or "No cities registered" in line:
            continue
        st.cities.append(line)

    try:
        st.load1 = parse_loadavg(Path("/proc/loadavg").read_text())[0]
    except (OSError, ValueError, IndexError):
        pass
    st.idle_pct = parse_vmstat_idle(_run(["vmstat", "1", "2"], timeout=15))

    return st


def state_to_dict(st: State) -> dict:
    return {
        "gc_pids": st.gc_pids,
        "watchdog_pids": st.watchdog_pids,
        "supervisor_pids": st.supervisor_pids,
        "dolt": {str(p): {"cwd": c, "kind": classify_dolt(c)} for p, c in st.dolt.items()},
        "live_sockets": st.live_sockets,
        "stale_sockets": st.stale_sockets,
        "orphan_tmux": {str(p): s for p, s in st.orphan_tmux.items()},
        "cities": st.cities,
        "load1": st.load1,
        "idle_pct": st.idle_pct,
        "running": st.running,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_app():
    import typer

    app = typer.Typer(
        add_completion=False,
        help="Gas City runtime doctor — find what gc's own teardown left behind.",
    )

    @app.command()
    def snapshot(
        as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a report."),
        gc_bin: str = typer.Option("gc", "--gc-bin", help="Path to the gc binary."),
    ) -> None:
        """One-shot: what Gas City has running right now."""
        st = collect_state(gc_bin)
        if as_json:
            print(json.dumps(state_to_dict(st), indent=2))
            raise typer.Exit(0)
        text, failures = render_snapshot(st)
        print(text)
        raise typer.Exit(1 if failures else 0)

    @app.command()
    def watch(
        interval: int = typer.Option(60, "--interval", help="Seconds between polls."),
        gc_bin: str = typer.Option("gc", "--gc-bin", help="Path to the gc binary."),
    ) -> None:
        """Poll and print only when something CHANGES. Silence means no change."""
        st = collect_state(gc_bin)
        idle = "unknown" if st.idle_pct is None else f"{st.idle_pct}%"
        print(
            f"[{time.strftime('%H:%M:%S')}] watching — baseline: "
            f"gc={len(st.gc_pids)} supervisors={len(st.supervisor_pids)} "
            f"watchdogs={len(st.watchdog_pids)} dolt={len(st.dolt)} "
            f"orphan-tmux={len(st.orphan_tmux)} idle={idle}",
            flush=True,
        )
        low = 0
        while True:
            try:
                time.sleep(interval)
                cur = collect_state(gc_bin)
                for ev in diff_state(st, cur):
                    print(f"[{time.strftime('%H:%M:%S')}] {ev}", flush=True)
                if cur.idle_pct is not None and cur.idle_pct < CPU_IDLE_ALERT_PCT:
                    low += 1
                    if low == CPU_ALERT_SAMPLES:
                        print(
                            f"[{time.strftime('%H:%M:%S')}] CPU PRESSURE: idle={cur.idle_pct}% "
                            f"over {low} samples, load1={cur.load1}",
                            flush=True,
                        )
                else:
                    if low >= CPU_ALERT_SAMPLES:
                        print(
                            f"[{time.strftime('%H:%M:%S')}] cpu recovered: idle={cur.idle_pct}%",
                            flush=True,
                        )
                    low = 0
                st = cur
            except KeyboardInterrupt:
                print("watch stopped.", file=sys.stderr)
                return

    return app


if __name__ == "__main__":
    _build_app()()
