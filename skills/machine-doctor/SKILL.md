---
name: machine-doctor
description: Diagnose and fix system health issues on Linux and macOS — rogue processes, memory pressure and swap, VMs/containers starving the host, full disks, thermal throttling, unexplained sleeps, macOS DNS/network failures, Gas Town and Gas City runaway agents, stale dev servers, and orphaned git state — plus historical forensics ("who was hot at 7:16?", "why did it sleep at 22:22?") via the vendored machine_doctor.py recorder. Use when the machine is slow, unresponsive, keeps sleeping, or something feels wrong — now or earlier.
allowed-tools: Bash, Read, Glob, Grep
---

# Machine Doctor

Diagnose and repair system health. Tiers:

| Invocation                | Scope                                                                  |
| ------------------------- | ---------------------------------------------------------------------- |
| `/machine-doctor`         | Quick vitals — CPU hogs, memory, disk                                  |
| `/machine-doctor watch`   | Record resource history — adaptive sampling, spike dumps               |
| `/machine-doctor report`  | Who has been hot over the last N hours (needs a prior `watch`)         |
| `/machine-doctor gastown` | Gas Town (`gt`) agent shutdown and cleanup                             |
| `/machine-doctor gascity` | Gas City (`gc`) leak hunt — now `snapshot --profile gascity`           |
| `/machine-doctor guards`  | Set up / verify two-layer CPU guard (OrbStack VM cap + in-VM watchdog) |
| `/machine-doctor network` | macOS DNS and API connectivity diagnosis and recovery                  |
| `/machine-doctor deep`    | Full probe — git locks, orphaned worktrees, stale servers, MCP         |
| `/machine-doctor sleeps`  | macOS: why it slept; who asked for it (`caffeinate` can't block that)  |
| `/machine-doctor mac`     | macOS runbook — memory pressure, VM memory reclaim, disk, sleep        |

Always start with **Step 0: Platform Detection**, then run the requested tier.

### What machine-doctor keeps healthy

Every row is checked by `machine_doctor.py snapshot` unless marked. The runbook
column is where the fix lives.

| Area                  | Healthy                                               | Usual culprit                                          | Fix in                          |
| --------------------- | ----------------------------------------------------- | ------------------------------------------------------ | ------------------------------- |
| CPU                   | idle ≥25%; no process >300% for long                  | agent swarms, builds, a VM's host process              | Tier 1, `doctor-deep.md` 3f     |
| Memory                | Linux MemAvailable ≥10%; macOS pressure `normal`      | a VM allowed most of host RAM; browsers; leaks         | Tier 1b, `doctor-macos.md` §1–3 |
| Swap                  | not near full; no sustained swap-out                  | memory pressure (swap is the symptom)                  | same as memory                  |
| Disk                  | data volumes <90%                                     | caches, VM images, build output                        | Tier 1c, `doctor-macos.md` §4   |
| VMs / containers      | VM memory cap ≤ half of host RAM; no stale servers    | OrbStack/Docker defaults; forgotten dev servers inside | `doctor-macos.md` §2            |
| Thermal               | no CPU speed limit                                    | sustained load                                         | find the load                   |
| Sleep / wake          | no thermal sleeps; forced ones named (often the user) | heat; a script calling `pmset sleepnow`                | `sleeps`, `doctor-macos.md` §5  |
| Zombies               | none                                                  | a parent not reaping                                   | Tier 1d                         |
| Agent orchestrators   | none running unless intended                          | Gas Town / Gas City leftovers                          | Tier 2, `doctor-gascity.md`     |
| CPU guards            | watchdog running, VM CPU cap set (_manual_)           | shell never opened since boot                          | Tier 1e, `doctor-guards.md`     |
| Dev servers, MCP, npm | responsive or gone (_manual, deep_)                   | days-old servers                                       | `doctor-deep.md` 3c–3e          |
| Git state             | no stale locks or orphaned worktrees (_manual, deep_) | crashed git processes                                  | `doctor-deep.md` 3a–3b          |

---

## Step 0: Platform Detection

```bash
OS=$(uname -s)  # "Darwin" = Mac, "Linux" = Linux
echo "Platform: $OS"
```

Set these aliases for the rest of the skill:

| Task              | Mac                                                                                                        | Linux                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Top CPU processes | `ps aux -r \| head -20`                                                                                    | `procs --sortd cpu \| head -20` (falls back to `ps aux --sort=-%cpu \| head -20`) |
| Memory overview   | `sysctl kern.memorystatus_vm_pressure_level vm.swapusage` + `top -l 1 -o mem -n 15 -stats pid,command,mem` | `free -h` or `cat /proc/meminfo \| head -5`                                       |
| Disk usage        | `df -h / /System/Volumes/Data`                                                                             | `df -h /`                                                                         |
| Process search    | `pgrep -af '<pattern>'`                                                                                    | `pgrep -af '<pattern>'`                                                           |
| Process tree      | `ps -o pid,ppid,comm -p <PID>`                                                                             | `/usr/bin/ps -o pid,ppid,comm -p <PID>`                                           |

**Linux note:** Many machines alias `ps` to `procs` and `top` to `btm`. Use `/usr/bin/ps` when you need standard flags like `--ppid` or `-o`.

### Environment Detection

CPU/memory cap recommendations (Tier 3f, [`doctor-deep.md`](./doctor-deep.md)) depend on _where_ you are — a bare-metal box with systemd behaves nothing like a rootless container with a read-only cgroup fs, or a Mac host.

```bash
if [ "$OS" = "Linux" ]; then
  INIT=$(cat /proc/1/comm 2>/dev/null)
  ORBSTACK=$(uname -r | grep -q orbstack && echo "yes" || echo "no")

  if [ "$INIT" != "systemd" ]; then
    ENV="linux-container"   # no systemd, cgroup2 likely read-only
  elif [ "$ORBSTACK" = "yes" ]; then
    ENV="orbstack-vm"       # OrbStack Linux machine, has systemd
  else
    ENV="linux-host"        # real VM or bare metal with systemd
  fi
else
  ENV="darwin"              # Mac host
fi
echo "ENV=$ENV"
```

**If `ENV=linux-container`, resource caps cannot be applied from inside** — `/sys/fs/cgroup` is read-only and there is no systemd. They must be set on the host (see Tier 3f in `doctor-deep.md`).

---

## Tier 1: Quick Vitals (`/machine-doctor`)

Run the vendored snapshot first — it checks CPU, memory/pressure, swap, disks,
zombies, VM memory caps, thermal limits, recent thermal sleeps (macOS; forced ones are a neutral note)
and per-container usage, on both Linux and macOS:

```bash
skills/machine-doctor/tools/machine_doctor.py snapshot     # exit 1 on any failure
```

Use the manual commands below to dig into what it flags, and present a summary
table:

### 1a. CPU Hogs

Find anything above 20% CPU:

```bash
# Mac
ps aux -r | awk 'NR<=1 || $3 > 20'

# Linux (try procs first, fall back to /usr/bin/ps)
procs --sortd cpu | head -20
# or: /usr/bin/ps aux --sort=-%cpu | head -20
```

Flag Claude processes, node processes, and dolt/jekyll servers specifically.

**Load far above `nproc` with high `%sy` and lots of niced CPU is a fan-out, not one hog.** No single row looks guilty, so attribute by parent chain: `/usr/bin/ps -o pid,ppid,etime,args -p <PID>` and walk `ppid` up until you hit the thing that started it. Typical culprits: a pre-commit hook running `pytest -n N` plus a JS test runner, or a loop of `bd` calls hammering the repo's `dolt sql-server` — for the latter, find who is calling `bd` rather than blaming dolt.

### 1b. Memory

```bash
# Mac — free pages are near zero by design; read the kernel's verdict instead
sysctl kern.memorystatus_vm_pressure_level   # 1 normal, 2 warn, 4 critical
sysctl vm.swapusage
top -l 1 -o mem -n 15 -stats pid,command,mem  # footprint, compressed pages included

# Linux: RSS grouped by app, top rows + "everything else" + TOTAL
skills/machine-doctor/tools/machine_doctor.py mem
```

Flag Linux if available memory is under 500MB; flag macOS on pressure warn or
critical. On macOS rank by footprint (`top`'s MEM), not RSS — RSS hides
compressed memory, which is exactly where a VM's guest memory ends up. If the
top footprint is a VM (OrbStack Helper, Docker), go to
[`doctor-macos.md`](./doctor-macos.md) §2.

On Linux, when asked "what's using memory", answer **by app, not by PID**, with a TOTAL row: `mem` collapses claude sessions, pytest workers, jekyll previews and `dolt sql-server`s into one row each and names bare interpreters by their script (`python3:serve.py`).

- **`used` in `free -h` exceeding summed RSS is kernel memory, not a leak** — mostly `SReclaimable` slab (dentry/inode cache), which the kernel drops under pressure. `mem` prints the gap and the slab size side by side.
- **"Can we compact memory?" — not from inside an OrbStack container.** `/proc/sys` is mounted read-only, so `compact_memory` / `drop_caches` fail even with sudo, and `swapoff`/`swapon` are restricted too. The only in-VM lever is stopping processes; returning memory to the Mac is OrbStack's job. Swap still in use after a spike has passed is harmless residue.

### 1c. Disk

```bash
df -h / /tmp                       # Linux
df -h / /System/Volumes/Data       # Mac: `/` is the sealed system volume; data lives here
```

Flag if any data filesystem is above 90%.

### 1d. Zombie / Orphan Processes

```bash
# Zombie processes (both platforms)
/usr/bin/ps aux | awk '$8 ~ /Z/'
```

### 1e. CPU Guards

Igor's OrbStack VM runs a two-layer CPU guard. Verify both layers are in place.

**Layer 1 — OrbStack Mac-side VM cap (hypervisor ceiling):** set from the Mac with `orb config set cpu <N>` or the OrbStack GUI. You cannot fully verify this from inside the VM — `nproc` shows how many cores are allocated. If it's less than the Mac's physical core count, the cap is set; otherwise trust documented config.

```bash
nproc  # cores allocated to the VM
```

**Layer 2 — In-VM watchdog:** `~/bin/cpu-watchdog.sh` polls `top` and attaches `cpulimit` to runaway processes.

```bash
pgrep -af 'bin/cpu-watchdog.sh$' >/dev/null && echo ok || echo MISSING
tail -1 /tmp/cpu-watchdog.log 2>/dev/null
```

Flag if the watchdog is **not running**. The boot hook lives in `~/.zshrc`, but it only fires once an interactive shell has started — if no shell has opened since reboot, or if the watchdog was manually killed, it will be missing. Recovery: run `setsid ~/bin/cpu-watchdog.sh &>/dev/null &`, or open any shell. If the script itself is missing, see `/machine-doctor guards` for the recovery template.

### Output Format

Present results as:

| Check      | Status           | Detail                       |
| ---------- | ---------------- | ---------------------------- |
| CPU        | ok / **high**    | List processes >20%          |
| Memory     | ok / **low**     | Available RAM                |
| Disk       | ok / **full**    | Usage %                      |
| Zombies    | ok / **found**   | Count                        |
| CPU guards | ok / **missing** | watchdog running, VM cap set |
| VM memory  | ok / **too big** | cap vs host RAM (snapshot)   |
| Sleep      | ok / **thermal** | thermal sleeps, 24h          |

If everything is clean, say so and stop. If problems found, offer to kill the offenders. If the same process class repeatedly shows up as a hog (e.g., multiple Claude/node processes summing to >80% of cores), also suggest running `/machine-doctor deep` for a CPU cap recommendation (Tier 3f in `doctor-deep.md`).

---

## Tier 2: Gas Town Shutdown (`/machine-doctor gastown`)

Gas Town is a multi-agent orchestration system that runs many Claude processes, a dolt database, and various supervisors. When it goes rogue, it can consume 400%+ CPU.

### 2a. Detect Gas Town

```bash
# Check for ANY Gas Town processes
pgrep -af 'GAS TOWN' 2>&1
pgrep -af 'gastown' 2>&1

# Check for gt workspace
ls ~/gt/rigs.json 2>/dev/null && echo "Gas Town workspace found at ~/gt"
```

If no Gas Town processes are found, report clean and stop.

### 2b. Graceful Shutdown

**You must run gt commands from the Gas Town workspace directory.**

```bash
cd ~/gt  # or wherever rigs.json lives

# Step 1: Emergency stop (freezes agents in place, preserves context)
gt estop --reason "doctor: system health"

# Step 2: Full shutdown with force
gt down --all --force --polecats

# Step 3: Verify
pgrep -af 'GAS TOWN' 2>&1 || echo "All Gas Town processes stopped"
```

### 2c. Rogue Tmux Sockets (if processes survive)

Gas Town runs agents in tmux sessions on **separate sockets** — not the default socket. This is why `tmux list-sessions` won't show them and `gt down` may miss them.

```bash
# Find Gas Town tmux sockets
# Mac & Linux:
find /tmp/tmux-$(id -u) -type s -name "gt*" 2>/dev/null
```

For each socket found:

```bash
# List what's running on it
tmux -L <socket-name> list-sessions 2>&1

# Kill the entire tmux server on that socket
tmux -L <socket-name> kill-server
```

**Common socket names:** `gt`, `gt-<hash>` (e.g., `gt-3d766d`)

### 2d. Scorched Earth (if still alive)

If processes survive after killing tmux sockets:

```bash
# Force kill all GAS TOWN claude processes
pgrep -f 'GAS TOWN' | xargs -r kill -9

# Kill any remaining gastown binaries
pgrep -f 'gastown' | xargs -r kill -9

# Kill orphaned dolt servers
pgrep -f 'dolt sql-server' | xargs -r kill -9
```

### 2e. Final Verification

```bash
pgrep -af 'GAS TOWN' 2>&1 || echo "Clean"
pgrep -af 'gastown' 2>&1 || echo "Clean"
pgrep -af 'dolt sql-server' 2>&1 || echo "Clean"
find /tmp/tmux-$(id -u) -type s -name "gt*" 2>/dev/null || echo "No rogue sockets"
```

Report results. If anything survived, escalate to user — something unexpected is respawning them.

### Why Gas Town Is Hard to Kill

1. **Supervisor respawning** — the deacon/mayor restart killed agents. You must kill the supervisor first or use `gt estop` to freeze everything.
2. **Separate tmux sockets** — `gt` uses its own tmux socket (`gt-<hash>`), so standard `tmux list-sessions` won't see them.
3. **Orphan reparenting** — killed processes get reparented to the tmux server (PPID becomes the tmux server PID), making parent tracking difficult.

---

## Tier: Forensics (`/machine-doctor watch` / `report` / `at`)

Point-in-time tools cannot answer "why was the box slow twenty minutes ago" — the
evidence expires before anyone looks. The vendored `machine_doctor.py` records history
while it runs (SQLite samples + full-tree spike dumps under
`~/.local/state/machine-doctor/`) and answers retroactively:

```bash
skills/machine-doctor/tools/machine_doctor.py watch                 # 30s samples; spike -> full process tree dump
skills/machine-doctor/tools/machine_doctor.py report --since 6h     # who has been hot, grouped by comm
skills/machine-doctor/tools/machine_doctor.py at 07:16              # what was running then
skills/machine-doctor/tools/machine_doctor.py snapshot              # right now + generic leak checks
skills/machine-doctor/tools/machine_doctor.py mem                   # RSS by app with TOTAL (Tier 1b)
skills/machine-doctor/tools/machine_doctor.py sleeps --since 24h    # macOS: why it slept, who asked for it
```

Linux and macOS both work. Linux reads `/proc`; macOS reads Mach CPU ticks,
`ps`, `top`, `sysctl` and `vm_stat`, and its memory column is the **footprint**
(labelled `MEM`), not RSS.

Key behaviors:

- **On-demand only** — no daemon, zero idle cost. Start `watch` at session start (or hand
  it to a Monitor); it prints only on state transitions, so silence means no change.
- **An empty window is not a quiet box.** If `report`/`at` find no samples, they say so
  and exit 1 — never treat that as "nothing happened".
- **Interval CPU%, not lifetime averages** — a long-lived process that starts spinning
  shows up immediately.
- Spike triggers (any proc >300% CPU; idle <25% or swap-out sustained 2 samples;
  MemAvailable <10%; macOS pressure critical) write a full redacted process tree to `spikes/`; samples keep 7 days,
  dumps keep the newest 50.

### Gas City profile (`/machine-doctor gascity`)

Gas City (`gc`) is a different product from Gas Town (`gt`) — different binary, different
socket naming, different teardown. The Gas Town tier does not cover it.

`gc` leaves side-processes that reparent to PID 1 and **outlive its own teardown commands**,
and `gc cities` cannot see them — it reports "No cities registered" while a managed-dolt
watchdog and a per-city tmux server are still up. A read-only `gc doctor` is enough to
create one.

Diagnose with the vendored tool rather than by eye — it separates city-scoped leaks from
`.beads/` repo servers that `bd` legitimately starts on demand:

```bash
skills/machine-doctor/tools/machine_doctor.py snapshot --profile gascity   # exits nonzero on a leak
```

**The runbook lives in a separate file to keep SKILL.md lean.** When the user invokes
`/machine-doctor gascity` — or Tier 1a shows `gc`/`dolt` processes on a box where
no city should be running — Read [`doctor-gascity.md`](./doctor-gascity.md) for the
shutdown order, orphaned-tmux cleanup, the credentials-in-argv exposure, what _not_ to
kill, and the gotchas (`ps` alias, self-matching `pkill`, load-average vs CPU-idle).

---

## Tier: Guards (`/machine-doctor guards`)

Set up or verify the two-layer CPU guard for Igor's OrbStack Linux VM. Layer 1 is a Mac-side hypervisor cap (`orb config set cpu <N>`). Layer 2 is an in-VM reactive watchdog (`cpu-watchdog.sh` from [idvorkin/Settings](https://github.com/idvorkin/Settings/blob/main/shared/cpu-watchdog.sh)) that attaches `cpulimit` to runaway processes.

**This tier lives in a separate file to keep SKILL.md lean.** When the user invokes `/machine-doctor guards`, or when Tier 1e reports the guards as missing, Read [`doctor-guards.md`](./doctor-guards.md) in this directory for the full runbook — why the canonical `systemd-run --scope` approach doesn't work on OrbStack, Layer 1 / Layer 2 setup recipes, the `~/.zshrc` boot hook, the smoke test, and caveats.

---

## Tier: macOS Networking (`/machine-doctor network`)

For network failures on a Mac, including Claude Code API lookup failures while other
sites work, read [doctor-network.md](./doctor-network.md). It covers the read-only
`tools/network_doctor.py` diagnostic, local DNS cache recovery, sandbox restrictions,
and verification after repair. Route networking complaints here after platform
detection, even when the user does not name this tier.

---

## Tier: macOS (`/machine-doctor mac`, `/machine-doctor sleeps`)

A Mac fails differently: memory pressure hides in the compressor, a dev VM can be
allowed most of the host's RAM, `/` is not the disk that fills, and a Mac can be
put to sleep through `caffeinate`.

**The runbook lives in a separate file to keep SKILL.md lean.** When the host is
a Mac and `snapshot` flags memory, a VM cap, the Data volume, thermal limits or
sleeps — or the user says the Mac is laggy or keeps sleeping — Read
[`doctor-macos.md`](./doctor-macos.md) for reading pressure/footprint correctly,
finding the container behind a busy VM, handing guest memory back
(`drop_caches` + `compact_memory`), quitting apps under pressure, safe cache
cleanup, caffeinate modes, and catching the process behind a forced sleep with
`tools/sleep-catcher.sh`.

---

## Tier 3: Deep Probe (`/machine-doctor deep`)

**This tier lives in a separate file to keep SKILL.md lean.** When the user invokes
`/machine-doctor deep`, or Tier 1 suggests a CPU cap, Read
[`doctor-deep.md`](./doctor-deep.md) for stale git locks (3a), orphaned worktrees
(3b), stale dev servers (3c), MCP servers (3d), hung npm/node (3e) and the CPU cap
recommendation per environment (3f).

---

## Safety Rules

- **Never kill processes without reporting what they are first.** Show the user what you found and ask before killing (except Gas Town when explicitly requested).
- **Never remove git locks without checking lsof.** A held lock means a process is actively using it.
- **Never prune worktrees with uncommitted changes.** Report and let the user decide.
- **Prefer graceful shutdown over kill -9.** Escalate TERM → INT → KILL, checking between steps; some servers (jekyll) only honor INT.
