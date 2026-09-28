# Machine Doctor — macOS runbook

Loaded from SKILL.md when the host is a Mac and something is slow, full, hot, or
asleep when it should not be. Start with the tool; this file is for reading its
findings and fixing them.

```bash
skills/machine-doctor/tools/machine_doctor.py snapshot        # everything below, checked
skills/machine-doctor/tools/machine_doctor.py sleeps --since 24h
```

---

## 1. Memory: read pressure, not free pages

macOS keeps free pages near zero by design, so `vm_stat`'s "Pages free" says
nothing. What says something:

| Signal                    | Where                                          | Bad when                  |
| ------------------------- | ---------------------------------------------- | ------------------------- |
| Pressure level            | `sysctl kern.memorystatus_vm_pressure_level`   | 2 = warn, 4 = critical    |
| Compressor size           | `top -l 1 -n 0` → `PhysMem: … (N compressor)`  | many GB, growing          |
| Swap                      | `sysctl vm.swapusage`                          | used near total           |
| Per-process **footprint** | `top -l 1 -o mem -n 15 -stats pid,command,mem` | one process holds many GB |

**Rank memory by footprint, never RSS.** Footprint counts compressed pages; RSS
does not. A VM whose guest memory sits in the compressor shows ~3 GB RSS and a
12 GB footprint. `snapshot`/`watch`/`report` on macOS already use footprint (the
column is labelled `MEM`, not `RSS`).

**Swap "free" can fall while things improve.** macOS shrinks the swap file as
pressure eases (8 GB → 6 GB → 4 GB), so free swap drops with it. Trust the
pressure level and swap _used_, not free.

Symptoms of memory pressure look like CPU trouble: high `sys` CPU (the kernel
compressing and swapping), load averages in the dozens or hundreds, and
beach-balls, while no single user process is especially hot.

---

## 2. VMs and containers (OrbStack, Docker)

The most common cause of a slow Mac running dev containers: **the VM is allowed
most of the host's RAM**, the guest fills it (builds, test runners, file
watchers), and the host swaps. `snapshot` warns when a VM cap exceeds half of
host RAM.

`snapshot` never starts OrbStack: it runs `orb status` first and reads the cap
(`orb config show`) and `docker stats` only when that says Running. A stopped
VM is skipped silently. Those commands can boot it, so run them by hand only
when a running VM is acceptable.

```bash
orb config show | grep -E '^(cpu|memory_mib):'     # the caps
docker stats --no-stream                           # which container
docker exec <name> ps -eo pid,etime,rss,args --sort=-rss | head -20   # what inside it
```

**Look inside the container for stale servers.** One container can hold a
dozen forgotten dev servers — e.g. `jekyll serve --incremental --livereload`
per worktree, days old. Each keeps watching its tree, which keeps the guest's
file-metadata cache (slab) growing. List them with their age and port:

```bash
docker exec <name> sh -c 'for p in $(pgrep -f "jekyll serve"); do echo "$p $(ps -o etime= -p $p) $(readlink /proc/$p/cwd)"; done'
```

### Handing guest memory back to the Mac (balloon reclaim)

The VM returns memory to macOS only in large free, contiguous blocks, and only
gradually. Two guest-kernel knobs speed it up. They are kernel-wide, so any
privileged container works; it must run as root:

```bash
docker run --rm --privileged --user root --entrypoint sh <any-local-image> -c \
  'sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory'
```

- `drop_caches=3` frees page cache and reclaimable slab (dentries/inodes). Safe:
  files are re-read on demand. `sync` first so nothing dirty is lost.
- `compact_memory=1` moves in-use pages together so free memory forms the
  contiguous blocks the VM's free-page reporting can hand back.
- Measured effect: guest slab 2.2 GB → 0.2 GB, host VM footprint 9.1 → 7.9 GB
  within a minute.

**It is temporary.** A workload that scans files refills slab in minutes. Two
follow-ups, in order of permanence:

1. `echo 200 > /proc/sys/vm/vfs_cache_pressure` (same privileged container) —
   the guest reclaims dentry/inode cache harder. Resets when the VM restarts.
2. **Lower the cap** (the real fix): `orb config set memory_mib <≈ half of host RAM>`.
   This **restarts the VM and every container in it** — ask first, and name what
   is running there (agents, servers, test runs) that will be interrupted.

---

## 3. Freeing memory on the host

Report first, ask, then act (Safety Rules in SKILL.md).

| Target             | How                                                   | Note                                                                                                           |
| ------------------ | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Any app            | `osascript -e 'tell application "X" to quit'`         | Under pressure a swapped-out app can time out (AppleEvent -1712); then `kill -TERM <pid>` — still a clean quit |
| Finder             | `killall Finder`                                      | Relaunches itself                                                                                              |
| Browser            | quit, or `pkill -9 -f '/Applications/<Browser>.app/'` | Force-kill only on request; it offers tab restore on relaunch                                                  |
| WindowServer (GBs) | log out and back in                                   | Long-uptime leak; killing it logs the user out anyway                                                          |

Bash-tool note: an `osascript quit` that hangs blocks the whole command until its
timeout. Run app quits one per command, or give them a short `timeout`.

---

## 4. Disk

**Check `/System/Volumes/Data`, not `/`.** On modern macOS `/` is the sealed,
read-only system snapshot (tens of percent); user data lives on the Data volume.
`df -h /` alone reports a healthy disk on a full machine. `snapshot` checks the
Data volume and every top-level `/Volumes/<drive>`.

Usually safe to clear (re-downloaded or rebuilt on demand) — still list sizes
and ask:

```bash
du -sh ~/Library/Caches/* ~/.cache/* ~/.npm ~/.Trash 2>/dev/null | sort -hr | head -20
brew cleanup -s            # Homebrew downloads
uv cache prune             # uv
npm cache clean --force    # npm
```

Ask about anything whose owner you cannot name from its path.

---

## 5. Sleep: "the Mac keeps falling asleep" (or waking)

### caffeinate modes

| Flag | Keeps awake             | Notes                                           |
| ---- | ----------------------- | ----------------------------------------------- |
| `-i` | the system (idle sleep) | the default with no flags; **screen may sleep** |
| `-d` | the display             | also keeps the system up                        |
| `-s` | the system, on AC only  |                                                 |

`pmset -g assertions` shows who holds what (`PreventUserIdleSystemSleep`,
`PreventUserIdleDisplaySleep`) — confirm the intended mode is actually held.

**caffeinate cannot block a forced sleep.** It only prevents _idle_ sleep. A
process calling `pmset sleepnow` (or `IOPMSleepSystem`), and thermal
emergencies, go straight through it. A forced sleep is not a problem in itself —
a process asked, and that is often the user (the Apple menu, a sleep shortcut,
their own `pmset sleepnow` alias). `snapshot` lists forced sleeps as a neutral
note; only thermal sleeps are a warning.

### Reading why it slept

```bash
skills/machine-doctor/tools/machine_doctor.py sleeps --since 24h
```

It parses `pmset -g log` and prints every sleep except maintenance ones:

| Reason in `pmset -g log`          | Meaning                                                   |
| --------------------------------- | --------------------------------------------------------- |
| `Idle Sleep`                      | nothing held an assertion — expected                      |
| `Software Sleep pid=N`            | forced: process N asked — often the user; listed, neutral |
| `Dark Wake Thermal Emergency`     | too hot to stay awake during a dark wake — check CPU load |
| `Clamshell Sleep`, `Power Button` | the user                                                  |
| `Maintenance Sleep`               | returning to sleep after a network dark wake — noise      |

`sleeps` marks thermal sleeps `⚠` (and exits 1 only for those) and forced
sleeps `·`. For a forced sleep it names pid N from the unified log (`log show
--predicate 'processID == N'`). It is often `bash`: a `bash -c "... pmset
sleepnow"` execs `pmset` in place, keeping bash's pid. The _parent_ that ran
that bash is what you want, and it is gone by the time anyone looks. If the
user did not ask for the sleep, that parent is the thing to find.

### Catching the next one live

`tools/sleep-catcher.sh` is a launchd agent that streams powerd's sleep events.
powerd posts `kIOMessageSystemWillSleep` ~25 ms after the request and seconds
before the actual sleep; the catcher snapshots the whole process table at that
instant, then resolves pid N against it and logs the parent chain. `sleeps`
folds those catches into its output.

```bash
skills/machine-doctor/tools/sleep-catcher.sh install   # agent + copy on the internal disk
skills/machine-doctor/tools/sleep-catcher.sh status
```

- `install` copies the script to `~/.local/share/machine-doctor/`: a launchd
  agent cannot exec a script on an external volume without a TCC grant (exit
  126, "Operation not permitted"), and a checkout or worktree may move.
- If the requester still exits before the snapshot, the catcher logs every
  process started in the preceding 10 s instead. The definitive escalation is
  `sudo eslogger exec` (Endpoint Security; the terminal needs Full Disk Access),
  which records every exec with its parent.

### Things that are _not_ forcing sleep (rule out fast)

Display-fix watchers (e.g. a BetterDisplay-based refresh-rate fixer), Hammerspoon
`hs.caffeinate.lockScreen()`, and screen locks do not put the system to sleep.
Grep automation configs for the calls that do: `pmset sleepnow`,
`systemSleep`, `IOPMSleepSystem`, `tell application "System Events" to sleep`.

---

## 6. Watching over time on a Mac

`machine_doctor.py watch` works on macOS (Mach CPU ticks, ps/top, sysctl). Hand
it to a Monitor for the session; it prints only on transitions. On macOS a
spike also fires on **critical** memory pressure; warn-level pressure alone does
not (it can last for hours and would bury real spikes).
