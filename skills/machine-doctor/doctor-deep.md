# Tier 3: Deep Probe (`/machine-doctor deep`)

> This file is loaded on demand by the `machine-doctor` skill. If the user invokes
> `/machine-doctor deep` — or Tier 1 suggests a CPU cap recommendation (3f) — Read
> this file after completing Step 0 (Platform Detection) in `SKILL.md`. The Safety
> Rules at the end of `SKILL.md` apply here too.

Run Tier 1 vitals (`SKILL.md`) first, then these additional checks. Run Gas Town checks only if Gas Town processes are detected.

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

Report running servers and whether they're responding. Offer to kill unresponsive ones. `snapshot` flags the first two leak patterns below automatically:

- **Server whose `/proc/<PID>/cwd` reads `… (deleted)`** — its worktree was removed under it; nothing can reach it. Strongest kill signal.
- **`jekyll serve` previews more than a day old** — agents start one per blog worktree (100–250MB each) and never stop them.
- **Duplicate concurrent `pytest -n N` runs in the same worktree** — one is abandoned; compare `etime` and kill the older.
- **Idle per-repo `dolt sql-server` for scratch or deleted dirs** — `bd` starts these on demand and leaves them running.

**jekyll ignores SIGTERM; SIGINT stops it** (and its `just`/`bash` wrappers exit with it). Kill escalation is TERM → INT → KILL.

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

| You want                            | Set                                |
| ----------------------------------- | ---------------------------------- |
| 80% of one core                     | `CPUQuota=80%`                     |
| 80% of the whole machine            | `CPUQuota=$((N * 80))%`            |
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

**For OrbStack specifically:** after setting the Mac-side cap above, run `/machine-doctor guards` ([`doctor-guards.md`](./doctor-guards.md)) to install the in-VM `cpu-watchdog` reactive layer. That's the two-layer pattern — Layer 1 ceiling from the host, Layer 2 early throttle from inside. For Docker/k8s with no in-container fallback, report to the user and stop.

#### `ENV=darwin` (Mac host)

macOS has no native per-user CPU cap. Options:

- **OrbStack is the culprit (most common):** `orb config set cpu <N>` then restart OrbStack. E.g. on a 10-core Mac: `orb config set cpu 9` leaves 1 core free.
- **Per-process throttle:** `cpulimit -p <PID> -l <percent>` (Homebrew: `brew install cpulimit`).
- **Background-class throttling:** `taskpolicy -b <cmd>` runs a command under App Nap / background QoS.

Verify with `top -o cpu` or Activity Monitor.

**If the VM's pressure is memory, not CPU**, a CPU cap will not help: its guest
memory lands in the host's compressor and swap. Cap `memory_mib` instead —
[`doctor-macos.md`](./doctor-macos.md) §2.

---

### Output Format

| Check       | Status              | Detail                       |
| ----------- | ------------------- | ---------------------------- |
| CPU         | ok / **high**       | Processes >20%               |
| Memory      | ok / **low**        | Available RAM                |
| Disk        | ok / **full**       | Usage %                      |
| Zombies     | ok / **found**      | Count                        |
| CPU guards  | ok / **missing**    | watchdog running, VM cap set |
| VM memory   | ok / **too big**    | cap vs host RAM              |
| Sleep       | ok / **thermal**    | thermal sleeps, 24h          |
| Gas Town    | clean / **running** | Process count                |
| Git locks   | ok / **stale**      | Files found                  |
| Worktrees   | ok / **orphaned**   | Count                        |
| Dev servers | ok / **stale**      | Unresponsive servers         |
| MCP servers | ok / **heavy**      | High resource usage          |
| npm/node    | ok / **hung**       | Long-running processes       |
