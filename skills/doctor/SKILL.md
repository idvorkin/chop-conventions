---
name: doctor
description: Diagnose and fix system health issues — rogue processes, Gas Town runaway agents, resource exhaustion, stale dev servers, and orphaned git state. Use when the machine is slow, unresponsive, or something feels wrong.
allowed-tools: Bash, Read, Glob, Grep
---

# Doctor

Diagnose and repair system health. Three tiers:

| Invocation | Scope |
|---|---|
| `/doctor` | Quick vitals — CPU hogs, memory, disk |
| `/doctor gastown` | Gas Town agent shutdown and cleanup |
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

### Output Format

Present results as:

| Check | Status | Detail |
|---|---|---|
| CPU | ok / **high** | List processes >20% |
| Memory | ok / **low** | Available RAM |
| Disk | ok / **full** | Usage % |
| Zombies | ok / **found** | Count |

If everything is clean, say so and stop. If problems found, offer to kill the offenders.

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

### Output Format

| Check | Status | Detail |
|---|---|---|
| CPU | ok / **high** | Processes >20% |
| Memory | ok / **low** | Available RAM |
| Disk | ok / **full** | Usage % |
| Zombies | ok / **found** | Count |
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
