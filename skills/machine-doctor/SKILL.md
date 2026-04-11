---
name: machine-doctor
description: Diagnose and fix system health issues — rogue processes, Gas Town runaway agents, resource exhaustion, stale dev servers, and orphaned git state. Use when the machine is slow, unresponsive, or something feels wrong.
allowed-tools: Bash, Read, Glob, Grep
---

# Doctor

Diagnose and repair system health. Three tiers:

| Invocation | Scope |
|---|---|
| `/doctor` | Quick vitals — CPU hogs, memory, disk |
| `/doctor gastown` | Gas Town agent shutdown and cleanup |
| `/doctor guards` | Set up / verify two-layer CPU guard (OrbStack VM cap + in-VM watchdog) |
| `/doctor deep` | Full probe — git locks, orphaned worktrees, stale servers, MCP |

Always start with **Step 0: Platform Detection**, then run the requested tier.

---

## Step 0: Platform Detection

```bash
OS=$(uname -s)  # "Darwin" = Mac, "Linux" = Linux
echo "Platform: $OS"
```

Set these aliases for the rest of the skill:

| Task | Mac | Linux |
|---|---|---|
| Top CPU processes | `ps aux -r \| head -20` | `procs --sortd cpu \| head -20` (falls back to `ps aux --sort=-%cpu \| head -20`) |
| Memory overview | `vm_stat` | `free -h` or `cat /proc/meminfo \| head -5` |
| Disk usage | `df -h /` | `df -h /` |
| Process search | `pgrep -af '<pattern>'` | `pgrep -af '<pattern>'` |
| Process tree | `ps -o pid,ppid,comm -p <PID>` | `/usr/bin/ps -o pid,ppid,comm -p <PID>` |

**Linux note:** Many machines alias `ps` to `procs` and `top` to `btm`. Use `/usr/bin/ps` when you need standard flags like `--ppid` or `-o`.

### Environment Detection (Linux only)

CPU/memory cap recommendations (Tier 3f) depend on *where* you are — a bare-metal box with systemd behaves nothing like a rootless container with a read-only cgroup fs.

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
  echo "ENV=$ENV"
fi
```

**If `ENV=linux-container`, resource caps cannot be applied from inside** — `/sys/fs/cgroup` is read-only and there is no systemd. They must be set on the host (see Tier 3f).

---

## Tier 1: Quick Vitals (`/doctor`)

Run these checks and present a summary table:

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

### 1b. Memory

```bash
# Mac
vm_stat | head -10
sysctl hw.memsize

# Linux
free -h
```

Flag if available memory is under 500MB.

### 1c. Disk

```bash
df -h / /tmp
```

Flag if any filesystem is above 90%.

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

Flag if the watchdog is **not running**. The boot hook lives in `~/.zshrc`, but it only fires once an interactive shell has started — if no shell has opened since reboot, or if the watchdog was manually killed, it will be missing. Recovery: run `setsid ~/bin/cpu-watchdog.sh &>/dev/null &`, or open any shell. If the script itself is missing, see `/doctor guards` for the recovery template.

### Output Format

Present results as:

| Check | Status | Detail |
|---|---|---|
| CPU | ok / **high** | List processes >20% |
| Memory | ok / **low** | Available RAM |
| Disk | ok / **full** | Usage % |
| Zombies | ok / **found** | Count |
| CPU guards | ok / **missing** | watchdog running, VM cap set |

If everything is clean, say so and stop. If problems found, offer to kill the offenders. If the same process class repeatedly shows up as a hog (e.g., multiple Claude/node processes summing to >80% of cores), also suggest running `/doctor deep` for a CPU cap recommendation (Tier 3f).

---

## Tier 2: Gas Town Shutdown (`/doctor gastown`)

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

## Tier: Guards (`/doctor guards`)

Set up or verify the **two-layer CPU guard** for Igor's OrbStack Linux VM. Layer 1 is a Mac-side hypervisor cap. Layer 2 is an in-VM reactive watchdog that attaches `cpulimit` to runaway processes. Run this when Layer 1e reports the guards as missing, or when first configuring a new machine.

### Why this shape (OrbStack-specific)

The canonical 2026 best practice on Linux is `systemd-run --scope -p CPUQuota=N%`, which creates a transient cgroup v2 scope covering a process tree. **That does not work inside an OrbStack container**, for two reasons:

1. **No systemd.** PID 1 is `sh -c 'while true; do tmux...'`, not systemd. `systemd-run --user` fails with `DBUS_SESSION_BUS_ADDRESS` missing, and `sudo systemd-run` fails with "System has not been booted with systemd as init system".
2. **Read-only cgroup2 fs.** `/sys/fs/cgroup` is mounted `ro,nsdelegate`. `sudo mount -o remount,rw /sys/fs/cgroup` returns "permission denied" — OrbStack strips the container's capability to remount. Direct writes like `echo "400000 100000" > /sys/fs/cgroup/cpu.max` therefore also fail.

Userspace `cpulimit` is the fallback. It polls (~2s), has a fork-window gap, uses blunt SIGSTOP/SIGCONT duty-cycle throttling, and can only signal processes owned by the user running it. Pairing it with a Mac-side OrbStack cap gives you a hard ceiling *and* an early reactive throttle, which is good enough for a dev VM.

### Layer 1: OrbStack VM cap (runs on the Mac host)

Set from the **Mac**, not from inside the VM:

```bash
# On the Mac
orb config set cpu <N>
# or: OrbStack menu → Settings → System → CPU
```

Pick `N` = physical cores − 1 (leaves one core for the Mac itself). Restart OrbStack if prompted.

Verify from inside the VM:

```bash
nproc  # should report N
```

### Layer 2: In-VM watchdog

**Install cpulimit:**

```bash
sudo apt install -y cpulimit
```

Requires cpulimit 3.1+, which is what Ubuntu's apt ships. Homebrew's cpulimit is 0.2 and unrelated — skip it.

**Install the script** at `~/bin/cpu-watchdog.sh`. The canonical copy lives in Igor's settings repo at [`shared/cpu-watchdog.sh`](https://github.com/idvorkin/Settings/blob/main/shared/cpu-watchdog.sh):

```bash
# Preferred: clone settings and symlink (tracks updates)
git clone https://github.com/idvorkin/Settings.git ~/settings
mkdir -p ~/bin
ln -sf ~/settings/shared/cpu-watchdog.sh ~/bin/cpu-watchdog.sh

# Or fetch the file directly if you don't want the whole settings repo
mkdir -p ~/bin
curl -fsSL https://raw.githubusercontent.com/idvorkin/Settings/main/shared/cpu-watchdog.sh \
    -o ~/bin/cpu-watchdog.sh
chmod +x ~/bin/cpu-watchdog.sh
```

**Install the boot hook** in `~/.zshrc` so the watchdog starts with the first interactive shell after reboot. Same idempotent `pgrep`/`setsid` pattern used for tailscaled and etserver:

```bash
if [ -x ~/bin/cpu-watchdog.sh ] && ! pgrep -f 'bin/cpu-watchdog.sh$' > /dev/null; then
    setsid ~/bin/cpu-watchdog.sh &>/dev/null &
fi
```

**Start it now** (don't wait for a new shell):

```bash
setsid ~/bin/cpu-watchdog.sh &>/dev/null &
```

**Verify:**

```bash
pgrep -af 'bin/cpu-watchdog.sh$'
tail -5 /tmp/cpu-watchdog.log
```

You should see a `cpu-watchdog starting ...` line with the current PID.

### Smoke test

Verify end-to-end that the watchdog detects and throttles a runaway. Uses test thresholds (30%/50%) and a 5s interval so you don't have to wait 30s.

```bash
# 1. Launch watchdog with test thresholds against a separate log
CPU_WATCHDOG_LIMIT=30 \
CPU_WATCHDOG_THRESHOLD=50 \
CPU_WATCHDOG_INTERVAL=5 \
CPU_WATCHDOG_LOG=/tmp/cpu-watchdog.test.log \
  ~/bin/cpu-watchdog.sh &
WATCHDOG=$!

# 2. Burn a core
yes >/dev/null & YES=$!

# 3. Wait ~15s for one scan cycle + cpulimit settle (loaded machines need the slack)
sleep 15

# 4. Verify
cat /tmp/cpu-watchdog.test.log          # should contain: throttle pid=$YES ... → cap 30%
/usr/bin/ps -p $YES -o pid,pcpu,comm,state  # %CPU ~30, state T (SIGSTOP duty cycle)
pgrep -af cpulimit                      # should show a live cpulimit -p $YES

# 5. Cleanup
kill $YES 2>/dev/null
kill $WATCHDOG 2>/dev/null
rm -f /tmp/cpu-watchdog.test.log
```

A passing test shows a `throttle pid=... → cap 30%` log line, `%CPU` dropped to roughly 30, and process state `T` (caught mid SIGSTOP). If `%CPU` is still ~100, check that `cpulimit` is installed (`which cpulimit`) and that the test `yes` is owned by the same user as the watchdog.

### Caveats

- **Polling gap.** The watchdog scans every `INTERVAL` seconds (30s in production, 5s in the smoke test). A process can burn a core unchecked until the next scan, and newly forked children run unconstrained until discovered.
- **Blunt throttle.** `cpulimit` uses SIGSTOP/SIGCONT duty cycling, not CFS bandwidth. It's juddery, not smooth. Fine for runaway loops; not appropriate for latency-sensitive workloads.
- **Per-process, not cumulative.** Ten processes each sitting just under the threshold are invisible to the watchdog even though they sum to 10 cores. Layer 1 is what catches that case.
- **Not a hard ceiling.** The watchdog is reactive. The OrbStack Mac-side cap (Layer 1) is the actual ceiling — don't remove it thinking the watchdog replaces it.
- **Must run as the process owner.** `cpulimit` signals processes it can `kill()`. Root-owned processes need `sudo cpulimit`, which the watchdog does not do; they are silently skipped along with the exclusion list.

---

## Tier 3: Deep Probe (`/doctor deep`)

Run Tier 1 vitals first, then these additional checks. Run Gas Town checks only if Gas Town processes are detected.

### 3a. Stale Git Locks

```bash
# Find .git lock files in common project directories
find ~/gits -name "*.lock" -path "*/.git/*" -mmin +5 2>/dev/null
find ~/gt -name "*.lock" -path "*/.git/*" -mmin +5 2>/dev/null
```

If found, check if the owning process is still running. If not, offer to remove:

```bash
# Check if lock is stale (no process holds it)
lsof <lock-file> 2>/dev/null || echo "Stale — safe to remove"
```

**Never remove a lock without checking lsof first.**

### 3b. Orphaned Git Worktrees

```bash
# Check all known project roots
for dir in ~/gits/*/  ~/gt/*/; do
  [ -d "$dir/.git" ] || continue
  git -C "$dir" worktree list 2>/dev/null | grep -v "bare\|$(basename $dir)"
done
```

Report any worktrees and whether their branch still exists. Offer `git worktree prune` for stale entries.

### 3c. Stale Dev Servers

```bash
# Jekyll servers
pgrep -af 'jekyll serve' 2>&1
# Check if they're actually responding
for port in 4000 4001; do
  curl -s -o /dev/null -w "localhost:$port → %{http_code}" http://localhost:$port/ 2>/dev/null || echo "localhost:$port → dead"
done

# Dolt servers
pgrep -af 'dolt sql-server' 2>&1

# Node dev servers (webpack, vite, etc.)
pgrep -af 'node.*serve' 2>&1
```

Report running servers and whether they're responding. Offer to kill unresponsive ones.

### 3d. MCP Servers

```bash
# Find running MCP server processes
pgrep -af 'mcp-server\|mcp_server\|start-mcp-server' 2>&1

# Serena (common MCP server)
pgrep -af 'serena' 2>&1
```

Report count and resource usage. MCP servers are generally fine unless they're consuming excessive CPU/memory.

### 3e. Stale npm/node Processes

```bash
# npm install that's been running too long
pgrep -af 'npm install' 2>&1

# TypeScript servers
pgrep -af 'tsserver' 2>&1
```

Flag any `npm install` running longer than 10 minutes.

### 3f. CPU Cap Recommendation

If Tier 1 found repeated CPU hogs, or you're here because "the machine keeps getting hammered," recommend a cap for the current environment. **Do not apply automatically** — these change global resource policy and need explicit user approval.

**Key gotcha (all Linux/systemd):** `CPUQuota=` is percent **of one core**, not of the whole machine. This trips everyone up the first time. On an N-core box:

| You want | Set |
|---|---|
| 80% of one core | `CPUQuota=80%` |
| 80% of the whole machine | `CPUQuota=$((N * 80))%` |
| **Leave 1 core free (recommended)** | **`CPUQuota=$(((N - 1) * 100))%`** |

"Leave 1 core free" is the default recommendation — 80% rounds ugly on small-core boxes, and one free core keeps the OS responsive.

#### `ENV=linux-host` or `ENV=orbstack-vm` (systemd available)

```bash
CORES=$(nproc)
QUOTA=$(((CORES - 1) * 100))    # leave 1 core free

# One-shot (resets on reboot)
sudo systemctl set-property user.slice CPUQuota=${QUOTA}%

# Persistent drop-in
sudo mkdir -p /etc/systemd/system/user.slice.d
sudo tee /etc/systemd/system/user.slice.d/cpu.conf <<EOF
[Slice]
CPUQuota=${QUOTA}%
EOF
sudo systemctl daemon-reload
```

Verify:

```bash
systemctl show user.slice -p CPUQuotaPerSecUSec
systemctl status user.slice | grep -E 'CPU|Tasks'
```

#### `ENV=linux-container`

You cannot set a true cgroup cap from inside — `/sys/fs/cgroup` is read-only and there is no systemd. The hard ceiling must be set on the **host**:

- **OrbStack on macOS:** `orb config set cpu <N>` on the mac, or OrbStack → Settings → System → CPU.
- **Docker container:** `docker update --cpus="<N>"` on the host.
- **k8s pod:** edit `resources.limits.cpu` on the pod spec.

**For OrbStack specifically:** after setting the Mac-side cap above, run `/doctor guards` (see the Guards tier earlier in this doc) to install the in-VM `cpu-watchdog` reactive layer. That's the two-layer pattern — Layer 1 ceiling from the host, Layer 2 early throttle from inside. For Docker/k8s with no in-container fallback, report to the user and stop.

#### `ENV=darwin` (Mac host)

macOS has no native per-user CPU cap. Options:

- **OrbStack is the culprit (most common):** `orb config set cpu <N>` then restart OrbStack. E.g. on a 10-core Mac: `orb config set cpu 9` leaves 1 core free.
- **Per-process throttle:** `cpulimit -p <PID> -l <percent>` (Homebrew: `brew install cpulimit`).
- **Background-class throttling:** `taskpolicy -b <cmd>` runs a command under App Nap / background QoS.

Verify with `top -o cpu` or Activity Monitor.

---

### Output Format

| Check | Status | Detail |
|---|---|---|
| CPU | ok / **high** | Processes >20% |
| Memory | ok / **low** | Available RAM |
| Disk | ok / **full** | Usage % |
| Zombies | ok / **found** | Count |
| CPU guards | ok / **missing** | watchdog running, VM cap set |
| Gas Town | clean / **running** | Process count |
| Git locks | ok / **stale** | Files found |
| Worktrees | ok / **orphaned** | Count |
| Dev servers | ok / **stale** | Unresponsive servers |
| MCP servers | ok / **heavy** | High resource usage |
| npm/node | ok / **hung** | Long-running processes |

---

## Safety Rules

- **Never kill processes without reporting what they are first.** Show the user what you found and ask before killing (except Gas Town when explicitly requested).
- **Never remove git locks without checking lsof.** A held lock means a process is actively using it.
- **Never prune worktrees with uncommitted changes.** Report and let the user decide.
- **Prefer graceful shutdown over kill -9.** Escalate force only when graceful fails.
