#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""
Machine doctor — historical resource forensics: who was hot, and when?

Point-in-time tools (`ps aux --sort=-%cpu`) cannot answer "why was the box slow
twenty minutes ago" — the evidence expires before anyone looks. This tool
records history while it runs and answers retroactively:

    machine_doctor.py watch                    # sample every 30s; spike -> full tree dump
    machine_doctor.py report --since 6h        # who has been hot, ranked by comm
    machine_doctor.py at 07:16                 # what was running then
    machine_doctor.py snapshot                 # right now + generic leak checks
    machine_doctor.py snapshot --profile gascity   # + Gas City leak hunt

On-demand only: no daemon, zero idle cost. An incident nobody was watching
leaves no history — `report` and `at` say so plainly rather than implying the
box was quiet.

All cpu%% figures are measured over the sampling interval (never ps-style
lifetime averages). Every printed or persisted command line is redacted first.

Exit codes: 0 ok; 1 findings/no-data; 2 bad arguments.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import md_store as store
from md_probe import (
    ProcSample,
    SpikeConfig,
    SpikeState,
    detect_spike,
    etime_s,
    idle_pct_between,
    interval_cpu_pct,
    parse_cpu_totals,
    parse_duration,
    parse_loadavg,
    parse_meminfo,
    parse_pid_stat,
    parse_pswpout,
    redact,
    render_tree,
    resolve_at,
    swap_out_kb_s,
    top_n,
)
from profiles import PROFILES, USER_SOCKETS, HostFacts

OK = "✓"
WARN = "⚠"
BAD = "✗"

HERTZ = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
SELF_PID = os.getpid()

# While a spike persists, at most one full dump per this many seconds — a
# one-hour build must not flush all 50 retained dumps.
DUMP_THROTTLE_S = 600

DEFAULT_DB = str(store.DEFAULT_STATE_DIR / "samples.db")
DEFAULT_STATE = str(store.DEFAULT_STATE_DIR)

# The sampler must not appear in its own rankings. Exclusion is by pid at
# insert time (the comm is just "python3.x", which would over-match).
REPORT_EXCLUDE = frozenset({"machine_doctor"})


# ---------------------------------------------------------------------------
# /proc + subprocess collection (all I/O lives here)
# ---------------------------------------------------------------------------


def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


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


def _uptime_s() -> float:
    text = _read("/proc/uptime")
    return float(text.split()[0]) if text else 0.0


def walk_procs(
    prev_jiffies: dict[int, int], dt_s: float, uptime_s: float
) -> tuple[list[ProcSample], dict[int, int], list[int]]:
    """One pass over /proc/<pid>/stat. Returns (samples, jiffies-by-pid, zombies)."""
    samples: list[ProcSample] = []
    jmap: dict[int, int] = {}
    zombies: list[int] = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        text = _read(f"/proc/{pid}/stat")
        if not text:
            continue  # exited between scandir and read
        try:
            st = parse_pid_stat(text, PAGE_KB)
        except (ValueError, IndexError):
            continue
        jmap[pid] = st.cpu_jiffies
        if st.state == "Z":
            zombies.append(pid)
        samples.append(
            ProcSample(
                pid=pid,
                ppid=st.ppid,
                comm=st.comm,
                cpu_pct=interval_cpu_pct(prev_jiffies.get(pid), st.cpu_jiffies, dt_s, HERTZ),
                rss_kb=st.rss_kb,
                etime_s=etime_s(st, uptime_s, HERTZ),
            )
        )
    return samples, jmap, zombies


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


def collect_facts(procs: list[ProcSample], zombies: list[int], *, load1: float,
                  idle_pct: int | None, mem_total_kb: int, mem_avail_kb: int) -> HostFacts:
    """Fill HostFacts for the profiles: gc cmdlines, dolt cwds, tmux orphans."""
    facts = HostFacts(
        procs=procs,
        zombies=zombies,
        load1=load1,
        idle_pct=idle_pct,
        mem_total_kb=mem_total_kb,
        mem_avail_kb=mem_avail_kb,
    )
    facts.cmdlines = {pid: _cmdline(pid) for pid in _pids_named("gc")}
    facts.dolt_cwds = {pid: _cwd(pid) for pid in _pids_named("dolt")}

    sock_dir = _tmux_socket_dir()
    if sock_dir.is_dir():
        for sock in sorted(p.name for p in sock_dir.iterdir()):
            if sock not in USER_SOCKETS and not _socket_live(sock):
                facts.stale_sockets.append(sock)

    # An orphaned city tmux server: PPID 1, argv names a -L socket that is not
    # the user's own default/ssh socket.
    for pid in _pids_named("tmux"):
        cl = _cmdline(pid)
        m = re.search(r"-L\s+(\S+)", cl)
        if not m or m.group(1) in USER_SOCKETS:
            continue
        try:
            ppid = parse_pid_stat(_read(f"/proc/{pid}/stat"), PAGE_KB).ppid
        except (ValueError, IndexError):
            continue
        if ppid == 1:
            facts.orphan_tmux[pid] = m.group(1)
    return facts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_app():
    import typer

    app = typer.Typer(
        add_completion=False,
        help="Machine doctor — record resource history; answer 'who was hot at time T'.",
    )

    def _ts_label(ts: int) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    @app.command()
    def watch(
        interval: int = typer.Option(30, "--interval", help="Seconds between samples."),
        spike_cpu: float = typer.Option(300.0, "--spike-cpu", help="Per-process cpu%% spike trigger."),
        db: str = typer.Option(DEFAULT_DB, "--db"),
        state_dir: str = typer.Option(DEFAULT_STATE, "--state-dir"),
    ) -> None:
        """Sample into the store; print only on state transitions."""
        conn = store.connect(db)
        sdir = Path(state_dir)
        store.prune(conn, int(time.time()))
        store.prune_spike_dumps(sdir)

        cfg = SpikeConfig(cpu_pct=spike_cpu)
        sstate = SpikeState()
        t0 = time.monotonic()
        cpu_prev = parse_cpu_totals(_read("/proc/stat"))
        pswp_prev = parse_pswpout(_read("/proc/vmstat"))
        procs, jmap, _ = walk_procs({}, 0.0, _uptime_s())
        walk_ms = (time.monotonic() - t0) * 1000
        print(
            f"[{time.strftime('%H:%M:%S')}] watching: interval={interval}s "
            f"nproc={len(procs)} walk={walk_ms:.0f}ms db={db}",
            flush=True,
        )

        in_spike = False
        last_dump_ts = 0
        last_ts = int(time.time())
        last_mono = time.monotonic()
        try:
            while True:
                time.sleep(interval)
                t0 = time.monotonic()
                dt = t0 - last_mono
                cpu_cur = parse_cpu_totals(_read("/proc/stat"))
                pswp_cur = parse_pswpout(_read("/proc/vmstat"))
                mem = parse_meminfo(_read("/proc/meminfo"))
                load1 = parse_loadavg(_read("/proc/loadavg"))[0]
                uptime = _uptime_s()
                procs, jmap, _ = walk_procs(jmap, dt, uptime)
                walk_ms = (time.monotonic() - t0) * 1000

                ts = int(time.time())
                if ts < last_ts:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] clock moved backwards "
                        f"({_ts_label(last_ts)} -> {_ts_label(ts)}); history around this point is suspect",
                        flush=True,
                    )
                idle = idle_pct_between(cpu_prev, cpu_cur)
                so = swap_out_kb_s(pswp_prev, pswp_cur, dt, PAGE_KB)
                sstate, reasons = detect_spike(
                    sstate, cfg, idle_pct=idle, mem=mem, swap_out=so, procs=procs
                )
                spiking = bool(reasons)

                stored = [p for p in top_n(procs) if p.pid != SELF_PID]
                try:
                    store.insert_sample(
                        conn,
                        ts,
                        load1=load1,
                        idle_pct=idle,
                        mem_avail_kb=mem.mem_avail_kb,
                        swap_used_kb=max(0, mem.swap_total_kb - mem.swap_free_kb),
                        swap_out=so,
                        nproc=len(procs),
                        is_spike=spiking,
                        procs=stored,
                    )
                except sqlite3.IntegrityError:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] duplicate sample ts={ts} "
                        "(clock step?) — tick skipped, not overwritten",
                        flush=True,
                    )

                if spiking and (not in_spike or ts - last_dump_ts >= DUMP_THROTTLE_S):
                    cmdlines = {p.pid: _cmdline(p.pid) for p in procs}
                    header = (
                        f"# spike at {_ts_label(ts)}  reasons: {'; '.join(reasons)}\n"
                        f"# load1={load1} idle={idle}% mem_avail={mem.mem_avail_kb // 1024}MB "
                        f"swap_out={so}KB/s nproc={len(procs)} walk={walk_ms:.0f}ms\n\n"
                    )
                    dump = store.write_spike_dump(sdir, ts, header + render_tree(procs, cmdlines))
                    last_dump_ts = ts
                    if not in_spike:
                        print(
                            f"[{time.strftime('%H:%M:%S')}] SPIKE: {'; '.join(reasons)} "
                            f"(walk={walk_ms:.0f}ms) dump={dump}",
                            flush=True,
                        )
                elif in_spike and not spiking:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] recovered: idle={idle}% load1={load1}",
                        flush=True,
                    )
                in_spike = spiking
                cpu_prev, pswp_prev = cpu_cur, pswp_cur
                last_ts, last_mono = ts, t0
        except KeyboardInterrupt:
            print("watch stopped.", file=sys.stderr)
        finally:
            store.prune(conn, int(time.time()))
            store.prune_spike_dumps(sdir)

    @app.command()
    def report(
        since: str = typer.Option("6h", "--since", help="Window: <int><s|m|h|d>, e.g. 30m, 6h, 2d."),
        top: int = typer.Option(10, "--top"),
        db: str = typer.Option(DEFAULT_DB, "--db"),
        state_dir: str = typer.Option(DEFAULT_STATE, "--state-dir"),
    ) -> None:
        """Who has been hot over the window — grouped by comm, ranked by ΣCPU%."""
        import typer as t

        try:
            seconds = parse_duration(since)
        except ValueError as e:
            print(e, file=sys.stderr)
            raise t.Exit(2)
        conn = store.connect(db)
        since_ts = int(time.time()) - seconds
        n = store.sample_count(conn, since_ts)
        if n == 0:
            print(
                f"no samples in the last {since} — watch was not running; "
                "an empty window is not a quiet box"
            )
            raise t.Exit(1)

        print(f"{n} samples in the last {since} (ΣCPU% ∝ CPU-seconds at a fixed interval)\n")
        print(f"{'COMM':<24} {'ΣCPU%':>10} {'PEAK-RSS':>10} {'SAMPLES':>8}  SPAN")
        for r in store.report(conn, since_ts, top=top, exclude=REPORT_EXCLUDE):
            span = f"{_ts_label(r.first_ts)[11:]} → {_ts_label(r.last_ts)[11:]}"
            print(
                f"{r.comm[:24]:<24} {r.cpu_sum:>10.0f} {r.peak_rss_kb // 1024:>8}MB "
                f"{r.samples:>8}  {span}"
            )
        spikes = store.spike_count(conn, since_ts)
        if spikes:
            print(f"\n{spikes} spike sample(s) in window — dumps in {state_dir}/spikes/")

    @app.command()
    def at(
        when: str = typer.Argument(..., help="'07:16', '07:16:38', or ISO-8601."),
        tolerance: int = typer.Option(60, "--tolerance", help="Max seconds to the nearest sample."),
        db: str = typer.Option(DEFAULT_DB, "--db"),
        state_dir: str = typer.Option(DEFAULT_STATE, "--state-dir"),
    ) -> None:
        """What was running then — nearest sample, plus the dump if it spiked."""
        import typer as t

        try:
            target = resolve_at(when, datetime.now())
        except ValueError as e:
            print(f"bad time {when!r}: {e}", file=sys.stderr)
            raise t.Exit(2)
        conn = store.connect(db)
        row = store.nearest_sample(conn, int(target.timestamp()), tolerance_s=tolerance)
        if row is None:
            print(
                f"no sample near {target:%Y-%m-%d %H:%M:%S} (tolerance {tolerance}s) — "
                "an empty window is not a quiet box"
            )
            raise t.Exit(1)

        idle = "?" if row.idle_pct is None else f"{row.idle_pct}%"
        spike = "  SPIKE" if row.is_spike else ""
        print(
            f"{_ts_label(row.ts)}  load1={row.load1} idle={idle} "
            f"mem_avail={row.mem_avail_kb // 1024}MB swap_out={row.swap_out}KB/s "
            f"nproc={row.nproc}{spike}\n"
        )
        print(f"{'PID':>8} {'CPU%':>6} {'RSS':>8} {'ETIME':>8}  COMM")
        for p in store.procs_at(conn, row.ts):
            cpu = "-" if p.cpu_pct is None else f"{p.cpu_pct:.0f}"
            print(f"{p.pid:>8} {cpu:>6} {p.rss_kb // 1024:>6}MB {p.etime_s:>7}s  {p.comm}")
        if row.is_spike:
            dump = store.dump_path_for(Path(state_dir), row.ts)
            if dump.exists():
                print(f"\nfull tree at that instant: {dump}")

    @app.command()
    def snapshot(
        profile: str = typer.Option("generic", "--profile", help="generic | gascity"),
        as_json: bool = typer.Option(False, "--json"),
    ) -> None:
        """Point-in-time state + known-leak checks. Exit 1 on any failure."""
        import typer as t

        if profile not in PROFILES:
            print(f"unknown profile {profile!r}; have: {', '.join(sorted(PROFILES))}", file=sys.stderr)
            raise t.Exit(2)

        # Two walks ~1s apart: interval cpu%, not lifetime averages.
        cpu0 = parse_cpu_totals(_read("/proc/stat"))
        _, jmap, _ = walk_procs({}, 0.0, _uptime_s())
        time.sleep(1.0)
        dt = 1.0
        cpu1 = parse_cpu_totals(_read("/proc/stat"))
        procs, _, zombies = walk_procs(jmap, dt, _uptime_s())
        idle = idle_pct_between(cpu0, cpu1)
        mem = parse_meminfo(_read("/proc/meminfo"))
        load1 = parse_loadavg(_read("/proc/loadavg"))[0]

        facts = collect_facts(
            procs, zombies, load1=load1, idle_pct=idle,
            mem_total_kb=mem.mem_total_kb, mem_avail_kb=mem.mem_avail_kb,
        )
        findings = PROFILES[profile](facts)
        failures = sum(1 for f in findings if f.severity == "fail")
        hot = [p for p in top_n(procs, 10) if p.pid != SELF_PID]

        if as_json:
            print(
                json.dumps(
                    {
                        "profile": profile,
                        "load1": load1,
                        "idle_pct": idle,
                        "mem_avail_kb": mem.mem_avail_kb,
                        "mem_total_kb": mem.mem_total_kb,
                        "nproc": len(procs),
                        "top": [
                            {
                                "pid": p.pid,
                                "comm": p.comm,
                                "cpu_pct": p.cpu_pct,
                                "rss_kb": p.rss_kb,
                                "cmdline": redact(_cmdline(p.pid))[:200],
                            }
                            for p in hot
                        ],
                        "findings": [
                            {"severity": f.severity, "message": f.message} for f in findings
                        ],
                    },
                    indent=2,
                )
            )
            raise t.Exit(1 if failures else 0)

        print(f"=== Machine Doctor ({profile}) ===")
        idle_s = "?" if idle is None else f"{idle}%"
        print(
            f"load1={load1} idle={idle_s} mem_avail={mem.mem_avail_kb // 1024}MB"
            f"/{mem.mem_total_kb // 1024}MB nproc={len(procs)}\n"
        )
        print(f"{'PID':>8} {'CPU%':>6} {'RSS':>8}  COMMAND")
        for p in hot:
            cpu = "-" if p.cpu_pct is None else f"{p.cpu_pct:.0f}"
            cl = redact(_cmdline(p.pid))[:80] or p.comm
            print(f"{p.pid:>8} {cpu:>6} {p.rss_kb // 1024:>6}MB  {cl}")
        print()
        if not findings:
            print(f"{OK} no findings")
        for f in findings:
            mark = BAD if f.severity == "fail" else WARN
            print(f"{mark} {f.message}")
        raise t.Exit(1 if failures else 0)

    return app


if __name__ == "__main__":
    _build_app()()
