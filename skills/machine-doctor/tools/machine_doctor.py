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
    machine_doctor.py mem                      # RSS grouped by app, with a TOTAL row
    machine_doctor.py snapshot --profile gascity   # + Gas City leak hunt
    machine_doctor.py sleeps --since 24h       # macOS: why it slept; who forced it

Linux reads /proc; macOS reads ps/top/sysctl/vm_stat and one Mach call, and
ranks memory by footprint (compressed pages included), not RSS.

On-demand only: no daemon, zero idle cost. An incident nobody was watching
leaves no history — `report` and `at` say so plainly rather than implying the
box was quiet.

All cpu%% figures are measured over the sampling interval (never ps-style
lifetime averages). Every printed or persisted command line is redacted first.

Exit codes: 0 ok; 1 findings/no-data; 2 bad arguments.
"""

import ctypes
import ctypes.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import md_store as store
from md_probe import (
    CpuTotals,
    MemInfo,
    ProcSample,
    mem_by_app,
    SleepEvent,
    SpikeConfig,
    SpikeState,
    cpu_totals_from_ticks,
    detect_spike,
    etime_s,
    idle_pct_between,
    interval_cpu_pct,
    parse_cpu_totals,
    parse_catcher_log,
    parse_df,
    parse_duration,
    parse_loadavg,
    parse_meminfo,
    parse_pid_stat,
    parse_pmset_sleeps,
    parse_ps_rows,
    parse_pswpout,
    parse_speed_limit,
    parse_swapusage,
    parse_top_mem,
    parse_vm_stat,
    pressure_name,
    redact,
    render_tree,
    resolve_at,
    select_mounts,
    swap_out_kb_s,
    top_n,
)
from profiles import PROFILES, USER_SOCKETS, HostFacts, is_deleted_cwd, is_jekyll_server

OK = "✓"
WARN = "⚠"
BAD = "✗"
NOTE = "·"

IS_DARWIN = sys.platform == "darwin"
HERTZ = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
SELF_PID = os.getpid()
# On macOS the per-process memory column is the footprint (compressed pages
# included), not RSS: label it so nobody compares it with a Linux RSS.
MEM_LABEL = "MEM" if IS_DARWIN else "RSS"

# While a spike persists, at most one full dump per this many seconds — a
# one-hour build must not flush all 50 retained dumps.
DUMP_THROTTLE_S = 600

DEFAULT_DB = str(store.DEFAULT_STATE_DIR / "samples.db")
DEFAULT_STATE = str(store.DEFAULT_STATE_DIR)

# The sampler must not appear in its own rankings. Exclusion is by pid at
# insert time (the comm is just "python3.x", which would over-match).
REPORT_EXCLUDE = frozenset({"machine_doctor"})

# How far back snapshot looks for forced/thermal sleeps.
SLEEP_LOOKBACK = timedelta(hours=24)
CATCHER = Path(__file__).resolve().parent / "sleep-catcher.sh"
CATCHER_LOG = Path("~/Library/Logs/sleep-catcher/sleep-catcher.log").expanduser()


# ---------------------------------------------------------------------------
# Collection (all I/O lives here). Linux reads /proc; macOS has no /proc, so
# the same facts come from ps/top/sysctl/vm_stat and one Mach call.
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


def _sysctl_int(name: str) -> int | None:
    out = _run(["sysctl", "-n", name]).strip()
    return int(out) if out.lstrip("-").isdigit() else None


def _pids_named(name: str) -> list[int]:
    out = _run(["pgrep", "-x", name])
    return [int(p) for p in out.split() if p.isdigit()]


def _cmdline(pid: int) -> str:
    if IS_DARWIN:
        return _run(["ps", "-o", "command=", "-p", str(pid)]).strip()
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _cmdlines(pids: list[int]) -> dict[int, str]:
    """Batch form for spike dumps: one ps call on macOS, not one per process."""
    if not IS_DARWIN:
        return {pid: _cmdline(pid) for pid in pids}
    want = set(pids)
    out: dict[int, str] = {}
    for line in _run(["ps", "-Ao", "pid=,command="]).splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if pid_s.isdigit() and int(pid_s) in want:
            out[int(pid_s)] = cmd.strip()
    return out


def _cwd(pid: int) -> str:
    if IS_DARWIN:
        out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        return next((ln[1:] for ln in out.splitlines() if ln.startswith("n")), "")
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _uptime_s() -> float:
    text = _read("/proc/uptime")
    return float(text.split()[0]) if text else 0.0


if IS_DARWIN:
    _LIBC = ctypes.CDLL(ctypes.util.find_library("c"))
    _LIBC.mach_host_self.restype = ctypes.c_uint
    _MACH_HOST = _LIBC.mach_host_self()


def _darwin_cpu_totals() -> CpuTotals:
    """host_statistics(HOST_CPU_LOAD_INFO): cumulative user/system/idle/nice
    ticks, the Mach twin of /proc/stat's `cpu ` line. Includes kernel time,
    which matters: memory compression and swap show up as system CPU."""
    ticks = (ctypes.c_uint * 4)()
    count = ctypes.c_uint(4)  # HOST_CPU_LOAD_INFO_COUNT
    kr = _LIBC.host_statistics(_MACH_HOST, 3, ticks, ctypes.byref(count))
    if kr != 0:
        raise OSError(f"host_statistics failed: kern_return={kr}")
    user, system, idle, nice = ticks
    return cpu_totals_from_ticks(user, system, idle, nice)


def read_cpu_totals() -> CpuTotals:
    return _darwin_cpu_totals() if IS_DARWIN else parse_cpu_totals(_read("/proc/stat"))


def read_swapout() -> tuple[int | None, int]:
    """(cumulative pages swapped out, page size in KB)."""
    if IS_DARWIN:
        vm = parse_vm_stat(_run(["vm_stat"]))
        return vm.get("swapouts"), vm["page_kb"]
    return parse_pswpout(_read("/proc/vmstat")), PAGE_KB


def read_mem() -> MemInfo:
    if not IS_DARWIN:
        return parse_meminfo(_read("/proc/meminfo"))
    total_kb = (_sysctl_int("hw.memsize") or 0) // 1024
    # kern.memorystatus_level is the kernel's "memory free percentage" — the
    # figure `memory_pressure` prints; vm_stat's free pages are misleadingly low.
    free_pct = _sysctl_int("kern.memorystatus_level")
    swap_total, swap_free = parse_swapusage(_run(["sysctl", "vm.swapusage"]))
    return MemInfo(
        mem_total_kb=total_kb,
        mem_avail_kb=total_kb * free_pct // 100 if free_pct is not None else 0,
        swap_total_kb=swap_total,
        swap_free_kb=swap_free,
        pressure=pressure_name(_sysctl_int("kern.memorystatus_vm_pressure_level")),
    )


def read_load1() -> float:
    if IS_DARWIN:
        return parse_loadavg(_run(["sysctl", "-n", "vm.loadavg"]).strip("{} \n"))[0]
    return parse_loadavg(_read("/proc/loadavg"))[0]


def _walk_procs_linux(
    prev_jiffies: dict[int, int], prev_t: float | None
) -> tuple[list[ProcSample], dict[int, int], list[int], float]:
    t_read = time.monotonic()
    dt_s = t_read - prev_t if prev_t is not None else 0.0
    uptime_s = _uptime_s()
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
                cpu_pct=interval_cpu_pct(
                    prev_jiffies.get(pid), st.cpu_jiffies, dt_s, HERTZ
                ),
                rss_kb=st.rss_kb,
                etime_s=etime_s(st, uptime_s, HERTZ),
            )
        )
    return samples, jmap, zombies, t_read


def _walk_procs_darwin(
    prev_ticks: dict[int, int], prev_t: float | None
) -> tuple[list[ProcSample], dict[int, int], list[int], float]:
    """ps gives cumulative CPU time (differenced into interval CPU%, as on
    Linux); top gives each process's footprint, which unlike RSS counts
    compressed pages — the only way a VM hogging the compressor shows up.

    top takes ~0.4s, so it runs first and the interval is timed at the ps
    read itself; timing around the whole walk would skew CPU% by that much."""
    footprint = parse_top_mem(_run(["top", "-l", "1", "-stats", "pid,mem"]))
    t_read = time.monotonic()
    dt_s = t_read - prev_t if prev_t is not None else 0.0
    rows = parse_ps_rows(
        _run(["ps", "-Ao", "pid=,ppid=,stat=,time=,etime=,rss=,comm="])
    )
    samples: list[ProcSample] = []
    tmap: dict[int, int] = {}
    zombies: list[int] = []
    for r in rows:
        ticks = round(r.cpu_s * 100)  # centiseconds, so interval_cpu_pct(hertz=100)
        tmap[r.pid] = ticks
        if r.state == "Z":
            zombies.append(r.pid)
        samples.append(
            ProcSample(
                pid=r.pid,
                ppid=r.ppid,
                comm=r.comm,
                cpu_pct=interval_cpu_pct(prev_ticks.get(r.pid), ticks, dt_s, 100),
                rss_kb=footprint.get(r.pid, r.rss_kb),
                etime_s=r.etime_s,
            )
        )
    return samples, tmap, zombies, t_read


def walk_procs(
    prev: dict[int, int], prev_t: float | None
) -> tuple[list[ProcSample], dict[int, int], list[int], float]:
    """One pass over every process. Returns (samples, cpu-counter-by-pid,
    zombies, read-time); feed counters and read-time back in next call for
    interval CPU%. First call: ({}, None), cpu_pct is None for everything."""
    return (
        _walk_procs_darwin(prev, prev_t)
        if IS_DARWIN
        else _walk_procs_linux(prev, prev_t)
    )


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


def _orbstack_mem_mib() -> dict[str, int]:
    if not shutil.which("orb"):
        return {}
    m = re.search(
        r"^memory_mib:\s*(\d+)", _run(["orb", "config", "show"], timeout=10), re.M
    )
    return {"OrbStack VM": int(m.group(1))} if m and int(m.group(1)) > 0 else {}


def read_sleeps(since: datetime) -> list[SleepEvent]:
    cutoff = since.strftime("%Y-%m-%d %H:%M:%S")
    return [
        e
        for e in parse_pmset_sleeps(_run(["pmset", "-g", "log"], timeout=60))
        if e.when >= cutoff
    ]


def unified_log_process(pid: int, when: str) -> str:
    """Name a pid from whatever it wrote to the unified log in the 30s before
    `when` — the requester of a forced sleep has exited long before anyone
    asks, but its log lines survive (for days, not forever)."""
    try:
        end = datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    start = end - timedelta(seconds=30)
    out = _run(
        [
            "/usr/bin/log",
            "show",
            "--style",
            "compact",
            "--start",
            start.strftime("%Y-%m-%d %H:%M:%S"),
            "--end",
            end.strftime("%Y-%m-%d %H:%M:%S"),
            "--predicate",
            f"processID == {pid}",
        ],
        timeout=60,
    )
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) > 3 and "[" in f[3]:  # 'bash[40700:117b267]'
            return f[3].split("[", 1)[0]
    return ""


def read_containers() -> list[str]:
    """One line per running container (name cpu mem). A VM's host process only
    says the VM is busy; this says which container inside it."""
    if not shutil.which("docker"):
        return []
    out = _run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}",
        ],
        timeout=15,
    )
    return [ln for ln in out.splitlines() if ln.strip()]


def collect_facts(
    procs: list[ProcSample],
    zombies: list[int],
    *,
    load1: float,
    idle_pct: int | None,
    mem: MemInfo,
) -> HostFacts:
    """Fill HostFacts for the profiles: host health plus gc cmdlines, dolt
    cwds and tmux orphans for the gascity profile."""
    facts = HostFacts(
        procs=procs,
        zombies=zombies,
        load1=load1,
        idle_pct=idle_pct,
        mem_total_kb=mem.mem_total_kb,
        mem_avail_kb=mem.mem_avail_kb,
        swap_total_kb=mem.swap_total_kb,
        swap_free_kb=mem.swap_free_kb,
        pressure=mem.pressure,
    )
    facts.disks = select_mounts(parse_df(_run(["df", "-Pkl"])))
    facts.vm_mem_mib = _orbstack_mem_mib()
    if IS_DARWIN:
        facts.compressor_kb = parse_vm_stat(_run(["vm_stat"])).get("compressor_kb")
        facts.speed_limit = parse_speed_limit(_run(["pmset", "-g", "therm"]))
        facts.sleeps = read_sleeps(datetime.now() - SLEEP_LOOKBACK)

    facts.cmdlines = {pid: _cmdline(pid) for pid in _pids_named("gc")}
    for p in procs:
        cwd = _cwd(p.pid)  # unreadable for other users' procs: "" -> skipped
        if is_deleted_cwd(cwd):
            facts.deleted_cwds[p.pid] = cwd
        if is_jekyll_server(p.comm, _cmdline(p.pid)):
            facts.jekyll_pids.append(p.pid)
    facts.dolt_cwds = {pid: _cwd(pid) for pid in _pids_named("dolt")}

    sock_dir = _tmux_socket_dir()
    if sock_dir.is_dir():
        for sock in sorted(p.name for p in sock_dir.iterdir()):
            if sock not in USER_SOCKETS and not _socket_live(sock):
                facts.stale_sockets.append(sock)

    # An orphaned city tmux server: PPID 1, argv names a -L socket that is not
    # the user's own default/ssh socket.
    ppid_of = {p.pid: p.ppid for p in procs}
    for pid in _pids_named("tmux"):
        cl = _cmdline(pid)
        m = re.search(r"-L\s+(\S+)", cl)
        if not m or m.group(1) in USER_SOCKETS:
            continue
        if ppid_of.get(pid) == 1:
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
        spike_cpu: float = typer.Option(
            300.0, "--spike-cpu", help="Per-process cpu%% spike trigger."
        ),
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
        cpu_prev = read_cpu_totals()
        pswp_prev, _ = read_swapout()
        procs, jmap, _, jt = walk_procs({}, None)
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
                cpu_cur = read_cpu_totals()
                pswp_cur, swap_page_kb = read_swapout()
                mem = read_mem()
                load1 = read_load1()
                procs, jmap, _, jt = walk_procs(jmap, jt)
                walk_ms = (time.monotonic() - t0) * 1000

                ts = int(time.time())
                if ts < last_ts:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] clock moved backwards "
                        f"({_ts_label(last_ts)} -> {_ts_label(ts)}); history around this point is suspect",
                        flush=True,
                    )
                idle = idle_pct_between(cpu_prev, cpu_cur)
                so = swap_out_kb_s(pswp_prev, pswp_cur, dt, swap_page_kb)
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
                    cmdlines = _cmdlines([p.pid for p in procs])
                    header = (
                        f"# spike at {_ts_label(ts)}  reasons: {'; '.join(reasons)}\n"
                        f"# load1={load1} idle={idle}% mem_avail={mem.mem_avail_kb // 1024}MB "
                        f"pressure={mem.pressure or '-'} "
                        f"swap_out={so}KB/s nproc={len(procs)} walk={walk_ms:.0f}ms\n\n"
                    )
                    dump = store.write_spike_dump(
                        sdir, ts, header + render_tree(procs, cmdlines)
                    )
                    last_dump_ts = ts
                    if not in_spike:
                        print(
                            f"[{time.strftime('%H:%M:%S')}] SPIKE: {'; '.join(reasons)} "
                            f"(walk={walk_ms:.0f}ms) dump={dump}",
                            flush=True,
                        )
                elif in_spike and not spiking:
                    # idle can be None (unmeasurable tick); never claim a
                    # recovery measurement that was not made.
                    idle_s = "?" if idle is None else f"{idle}%"
                    print(
                        f"[{time.strftime('%H:%M:%S')}] recovered: idle={idle_s} load1={load1}",
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
        since: str = typer.Option(
            "6h", "--since", help="Window: <int><s|m|h|d>, e.g. 30m, 6h, 2d."
        ),
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

        print(
            f"{n} samples in the last {since} (ΣCPU% ∝ CPU-seconds at a fixed interval)\n"
        )
        print(
            f"{'COMM':<24} {'ΣCPU%':>10} {'PEAK-' + MEM_LABEL:>10} {'SAMPLES':>8}  SPAN"
        )
        for r in store.report(conn, since_ts, top=top, exclude=REPORT_EXCLUDE):
            span = f"{_ts_label(r.first_ts)[11:]} → {_ts_label(r.last_ts)[11:]}"
            print(
                f"{r.comm[:24]:<24} {r.cpu_sum:>10.0f} {r.peak_rss_kb // 1024:>8}MB "
                f"{r.samples:>8}  {span}"
            )
        spikes = store.spike_count(conn, since_ts)
        if spikes:
            print(
                f"\n{spikes} spike sample(s) in window — dumps in {state_dir}/spikes/"
            )

    @app.command()
    def at(
        when: str = typer.Argument(..., help="'07:16', '07:16:38', or ISO-8601."),
        tolerance: int = typer.Option(
            60, "--tolerance", help="Max seconds to the nearest sample."
        ),
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
        print(f"{'PID':>8} {'CPU%':>6} {MEM_LABEL:>8} {'ETIME':>8}  COMM")
        for p in store.procs_at(conn, row.ts):
            cpu = "-" if p.cpu_pct is None else f"{p.cpu_pct:.0f}"
            print(
                f"{p.pid:>8} {cpu:>6} {p.rss_kb // 1024:>6}MB {p.etime_s:>7}s  {p.comm}"
            )
        if row.is_spike:
            dump = store.dump_path_for(Path(state_dir), row.ts)
            if dump.exists():
                print(f"\nfull tree at that instant: {dump}")

    @app.command()
    def mem(
        top: int = typer.Option(15, "--top", help="Rows before 'everything else'."),
        as_json: bool = typer.Option(False, "--json"),
    ) -> None:
        """Memory by app (not by PID), with everything-else and TOTAL rows."""
        procs, _, _ = walk_procs({}, 0.0, _uptime_s())
        procs = [p for p in procs if p.pid != SELF_PID]
        cmdlines = {p.pid: _cmdline(p.pid) for p in procs}
        rows, rest_kb, total_kb = mem_by_app(procs, cmdlines, top)
        mi = parse_meminfo(_read("/proc/meminfo"))
        used_kb = mi.mem_total_kb - mi.mem_avail_kb
        if as_json:
            print(
                json.dumps(
                    {
                        "apps": [
                            {"app": r.name, "rss_kb": r.rss_kb, "procs": r.count}
                            for r in rows
                        ],
                        "everything_else_kb": rest_kb,
                        "total_rss_kb": total_kb,
                        "mem_total_kb": mi.mem_total_kb,
                        "used_kb": used_kb,
                        "sreclaimable_kb": mi.sreclaimable_kb,
                    },
                    indent=2,
                )
            )
            return
        mb = lambda kb: f"{kb / 1024:,.0f}MB"  # noqa: E731
        print(f"{'APP':<32} {'RSS':>10} {'PROCS':>6}")
        for r in rows:
            print(f"{r.name[:32]:<32} {mb(r.rss_kb):>10} {r.count:>6}")
        print(f"{'everything else':<32} {mb(rest_kb):>10}")
        print(f"{'TOTAL (sum of RSS)':<32} {mb(total_kb):>10}")
        gap_kb = used_kb - total_kb
        print(
            f"\nused (MemTotal-MemAvailable) {mb(used_kb)} of {mb(mi.mem_total_kb)}; "
            f"used minus RSS {mb(gap_kb)}; reclaimable slab {mb(mi.sreclaimable_kb)}."
        )
        if gap_kb > 0:
            print(
                "used beyond RSS is kernel memory (slab, page tables, shmem), not a "
                "process leak; RSS double-counts shared pages."
            )

    @app.command()
    def snapshot(
        profile: str = typer.Option("generic", "--profile", help="generic | gascity"),
        as_json: bool = typer.Option(False, "--json"),
    ) -> None:
        """Point-in-time state + known-leak checks. Exit 1 on any failure."""
        import typer as t

        if profile not in PROFILES:
            print(
                f"unknown profile {profile!r}; have: {', '.join(sorted(PROFILES))}",
                file=sys.stderr,
            )
            raise t.Exit(2)

        # Two walks ~1s apart: interval cpu%, not lifetime averages.
        cpu0 = read_cpu_totals()
        _, jmap, _, jt = walk_procs({}, None)
        time.sleep(1.0)
        cpu1 = read_cpu_totals()
        procs, _, zombies, _ = walk_procs(jmap, jt)
        idle = idle_pct_between(cpu0, cpu1)
        mem = read_mem()
        load1 = read_load1()

        facts = collect_facts(procs, zombies, load1=load1, idle_pct=idle, mem=mem)
        containers = read_containers()
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
                        "mem_pressure": mem.pressure,
                        "swap_free_kb": mem.swap_free_kb,
                        "disks": facts.disks,
                        "containers": containers,
                        "nproc": len(procs),
                        "top": [
                            {
                                "pid": p.pid,
                                "comm": p.comm,
                                "cpu_pct": p.cpu_pct,
                                "rss_kb": p.rss_kb,
                                "mem_kind": "footprint" if IS_DARWIN else "rss",
                                "cmdline": redact(_cmdline(p.pid))[:200],
                            }
                            for p in hot
                        ],
                        "findings": [
                            {"severity": f.severity, "message": f.message}
                            for f in findings
                        ],
                    },
                    indent=2,
                )
            )
            raise t.Exit(1 if failures else 0)

        print(f"=== Machine Doctor ({profile}) ===")
        idle_s = "?" if idle is None else f"{idle}%"
        pressure = f" pressure={mem.pressure}" if mem.pressure else ""
        print(
            f"load1={load1} idle={idle_s} mem_avail={mem.mem_avail_kb // 1024}MB"
            f"/{mem.mem_total_kb // 1024}MB{pressure} "
            f"swap_free={mem.swap_free_kb // 1024}MB nproc={len(procs)}"
        )
        if facts.disks:
            print(
                "disks: "
                + "  ".join(f"{m}={pct}%" for m, pct in sorted(facts.disks.items()))
            )
        for c in containers:
            print(f"container: {c}")
        print()
        print(f"{'PID':>8} {'CPU%':>6} {MEM_LABEL:>8}  COMMAND")
        for p in hot:
            cpu = "-" if p.cpu_pct is None else f"{p.cpu_pct:.0f}"
            cl = redact(_cmdline(p.pid))[:80] or p.comm
            print(f"{p.pid:>8} {cpu:>6} {p.rss_kb // 1024:>6}MB  {cl}")
        print()
        if not findings:
            print(f"{OK} no findings")
        for f in findings:
            mark = {"fail": BAD, "warn": WARN}.get(f.severity, NOTE)
            print(f"{mark} {f.message}")
        raise t.Exit(1 if failures else 0)

    @app.command()
    def sleeps(
        since: str = typer.Option("24h", "--since", help="Window: <int><s|m|h|d>."),
    ) -> None:
        """Why the Mac slept — every non-maintenance sleep; names who requested one."""
        import typer as t

        if not IS_DARWIN:
            print("sleeps reads pmset's power log: macOS only", file=sys.stderr)
            raise t.Exit(2)
        try:
            seconds = parse_duration(since)
        except ValueError as e:
            print(e, file=sys.stderr)
            raise t.Exit(2)
        events = read_sleeps(datetime.now() - timedelta(seconds=seconds))
        if not events:
            print(
                f"no sleeps in the last {since} (maintenance dark-wake sleeps are not counted)"
            )
            return
        catches = (
            parse_catcher_log(CATCHER_LOG.read_text()) if CATCHER_LOG.exists() else {}
        )
        for e in events:
            mark = WARN if e.thermal else NOTE if e.forced else " "
            print(f"{mark} {e.when}  {e.reason}")
            if e.pid is None:
                continue
            name = unified_log_process(e.pid, e.when)
            print(
                f"      pid {e.pid} was: {name or 'unknown (unified log has rotated past it)'}"
            )
            if e.when in catches:
                print("      caught live by sleep-catcher:")
                for ln in catches[e.when].rstrip().splitlines():
                    print(f"      {ln}")
        forced = [e for e in events if e.forced]
        if forced and not any(e.when in catches for e in forced):
            print(
                "\nThe requester's parent chain died with it. To catch the next one live:\n"
                f"  {CATCHER} install"
            )
        raise t.Exit(1 if any(e.thermal for e in events) else 0)

    return app


if __name__ == "__main__":
    _build_app()()
