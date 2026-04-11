# Tier: Guards (`/doctor guards`)

> This file is loaded on demand by the `machine-doctor` skill. If the user
> invokes `/doctor guards`, Read this file after completing Step 0 (Platform
> Detection) from `SKILL.md`. It is not part of the default `/doctor` or
> `/doctor deep` flow — Tier 1e in `SKILL.md` only *checks* whether the guard
> is live, not how to install it.

Set up or verify the **two-layer CPU guard** for Igor's OrbStack Linux VM. Layer 1 is a Mac-side hypervisor cap. Layer 2 is an in-VM reactive watchdog that attaches `cpulimit` to runaway processes. Run this when Tier 1e reports the guards as missing, or when first configuring a new machine.

## Why this shape (OrbStack-specific)

The canonical 2026 best practice on Linux is `systemd-run --scope -p CPUQuota=N%`, which creates a transient cgroup v2 scope covering a process tree. **That does not work inside an OrbStack container**, for two reasons:

1. **No systemd.** PID 1 is `sh -c 'while true; do tmux...'`, not systemd. `systemd-run --user` fails with `DBUS_SESSION_BUS_ADDRESS` missing, and `sudo systemd-run` fails with "System has not been booted with systemd as init system".
2. **Read-only cgroup2 fs.** `/sys/fs/cgroup` is mounted `ro,nsdelegate`. `sudo mount -o remount,rw /sys/fs/cgroup` returns "permission denied" — OrbStack strips the container's capability to remount. Direct writes like `echo "400000 100000" > /sys/fs/cgroup/cpu.max` therefore also fail.

Userspace `cpulimit` is the fallback. It polls (~2s), has a fork-window gap, uses blunt SIGSTOP/SIGCONT duty-cycle throttling, and can only signal processes owned by the user running it. Pairing it with a Mac-side OrbStack cap gives you a hard ceiling *and* an early reactive throttle, which is good enough for a dev VM.

## Layer 1: OrbStack VM cap (runs on the Mac host)

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

## Layer 2: In-VM watchdog

**Install cpulimit:**

```bash
sudo apt install -y cpulimit
```

Requires cpulimit 3.1+, which is what Ubuntu's apt ships (installs to `/usr/bin/cpulimit`). Homebrew's cpulimit is an unrelated fork stuck at v0.2 — it lacks `-q` (quiet) and other flags the watchdog uses, and silently errors out when called.

**Watch out for linuxbrew PATH shadowing.** If Homebrew's cpulimit is already installed on a Linux box (`/home/linuxbrew/.linuxbrew/bin/cpulimit`), it will shadow `/usr/bin/cpulimit` because linuxbrew prepends its own bin to PATH. A bare `cpulimit` call then picks the v0.2 fork, and the watchdog loop logs `throttle pid=...` every scan but the target process never actually drops below 100% CPU — the giveaway is that no `cpulimit` child process survives between scans. The `cpu-watchdog.sh` in Igor's Settings repo resolves `CPULIMIT=/usr/bin/cpulimit` explicitly to avoid this, but if you're debugging a homegrown script or see this failure pattern, that's your cause. Fix options: `brew uninstall cpulimit`, or hardcode the full path.

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

## Smoke test

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

A passing test shows a **single** `throttle pid=... → cap 30%` log line, `%CPU` dropped to roughly 30 (allow ~10% slack — SIGSTOP duty cycle is juddery), process state `R` or `T`, and `pgrep -af '/usr/bin/cpulimit'` shows a live `cpulimit -l 30 -p <pid> -z -q` child.

**Failure patterns:**

- **`%CPU` still ~100 and the log shows the throttle line repeated every scan** → cpulimit is being launched but dying immediately. Almost always linuxbrew's broken v0.2 cpulimit shadowing `/usr/bin/cpulimit` (see the PATH-shadowing warning earlier in this doc). Run `which cpulimit` — if it's under `/home/linuxbrew`, that's your cause.
- **`%CPU` still ~100 and no throttle line at all** → the watchdog isn't scanning. Check `tail /tmp/cpu-watchdog.test.log` for startup errors, and that `/usr/bin/top` exists.
- **Throttle line fires but `ps` shows the wrong user** → cpulimit can't signal processes it doesn't own. Make sure the test `yes` and the watchdog run as the same user.

## Caveats

- **Polling gap.** The watchdog scans every `INTERVAL` seconds (30s in production, 5s in the smoke test). A process can burn a core unchecked until the next scan, and newly forked children run unconstrained until discovered.
- **Blunt throttle.** `cpulimit` uses SIGSTOP/SIGCONT duty cycling, not CFS bandwidth. It's juddery, not smooth. Fine for runaway loops; not appropriate for latency-sensitive workloads.
- **Per-process, not cumulative.** Ten processes each sitting just under the threshold are invisible to the watchdog even though they sum to 10 cores. Layer 1 is what catches that case.
- **Not a hard ceiling.** The watchdog is reactive. The OrbStack Mac-side cap (Layer 1) is the actual ceiling — don't remove it thinking the watchdog replaces it.
- **Must run as the process owner.** `cpulimit` signals processes it can `kill()`. Root-owned processes need `sudo cpulimit`, which the watchdog does not do; they are silently skipped along with the exclusion list.
