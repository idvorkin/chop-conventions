# Tier: Gas City (`/machine-doctor gascity`)

> This file is loaded on demand by the `machine-doctor` skill. If the user invokes
> `/machine-doctor gascity` — or Tier 1a shows `gc`/`dolt` processes on a box where
> no city should be running — Read this file after completing Step 0 (Platform
> Detection) in `SKILL.md`.

Gas City (`gc`) is **not** Gas Town (`gt`). Different binary, different tmux socket
naming, different teardown commands. Tier 2 does not cover it.

## The core problem

`gc` spawns side-processes that **reparent to PID 1 and outlive its own teardown
commands**, and its status command cannot see them:

```
$ gc cities
No cities registered. Use 'gc register' to add a city.   # ← says nothing about processes

$ pgrep -x gc ; pgrep -x dolt
2899915                                                   # watchdog, PPID 1
2899925                                                   # dolt it keeps alive
```

Two offenders, both verified:

| Leak | What it is | Survives |
|---|---|---|
| `gc __gc-managed-dolt-scope-watchdog` | Keeps a managed `dolt sql-server` alive. Anchors liveness on the scope's config file existing, so it never self-exits while the city dir exists. | `gc supervisor stop`, city unregistration |
| Per-city `tmux: server` | Holds agent sessions (`-L <city> ... -s <agent>`). | City unregistration; outlived its supervisor by **2d5h** in one observed case |

**A read-only `gc doctor` is enough to create the first one.** Running it against a
stopped, unregistered city starts a dolt server plus watchdog that persist after the
command exits (upstream: [gastownhall/gascity#4685](https://github.com/gastownhall/gascity/issues/4685);
the persistence mechanism is [#4679](https://github.com/gastownhall/gascity/issues/4679)).

## Step 1: Diagnose with the tool, not by eye

```bash
skills/machine-doctor/tools/machine_doctor.py snapshot --profile gascity     # what's running now
skills/machine-doctor/tools/machine_doctor.py snapshot --profile gascity --json
```

Exits nonzero if it finds a city-scoped leak. It distinguishes **city-scoped** dolt
servers (gc's problem) from **`.beads/`-repo** servers that `bd` starts on demand for
an ordinary repo (not gc's problem — do not kill these blind; see Step 4).

To keep watching, poll for transitions only:

```bash
skills/machine-doctor/tools/machine_doctor.py watch --interval 60
```

`watch` prints a baseline line and then **stays silent until something changes** —
safe to hand to a long-running monitor. Silence means no change, not no output.
Note `watch` is generic resource recording (spikes, history for `report`/`at`);
the gascity leak checks themselves live in `snapshot --profile gascity` — poll
that for leak transitions if that is specifically what you are hunting.

## Step 2: Shut down, in this order

Order matters: the watchdog restarts dolt, so killing dolt first accomplishes nothing.

```bash
cd <city-dir>
gc stop                 # stops sessions AND reaps the scope watchdog + its dolt
gc supervisor stop      # machine-wide supervisor; separate process tree
```

`gc stop` is the one that reaps the watchdog — it works even on a city that was never
started, which is the non-obvious part. If you only ran `gc supervisor stop`, the
watchdog is still up.

Then re-run `snapshot` to confirm. If a watchdog survives, `kill <pid>` it directly
(SIGTERM — it takes its dolt child with it).

## Step 3: Orphaned city tmux servers

Unregistering a city does not touch its tmux server. Find and kill:

```bash
ls /tmp/tmux-$(id -u)/                    # socket files, one per city
tmux -L <socket> ls                       # sessions (exit 0 + empty = server alive, no sessions)
tmux -L <socket> kill-server
rm -f /tmp/tmux-$(id -u)/<socket>         # only after confirming the server is gone
```

⚠️ **These sessions carry credentials in argv** (`-e ANTHROPIC_API_KEY=…`), readable by
anything that can read `/proc`. Treat a long-lived orphan as a secret-exposure window,
not just wasted RAM. Redact before pasting any `cmdline` output into a report — the
tool's `redact()` does this for you.

## Step 4: What NOT to kill

- **`.beads/` repo dolt servers** (`cwd=<repo>/.beads/dolt`) — `bd` starts these on
  demand for an ordinary repo's issue store. If a Claude session is live in that repo,
  killing it breaks `bd` mid-flight. They respawn, so it is not fatal, but it is
  disruptive and it is not a Gas City leak. `snapshot` labels these `[beads-repo, not gc's]`.
- **The user's own tmux sockets** (`default`, `ssh`).

## Gotchas that cost real time

- **Load average is not CPU.** A box reading load 26 was **92% idle, 0 steal, 0 D-state** —
  the load was short-lived process churn plus other tenants, not Gas City. Always read
  `vmstat`'s `id`/`st` columns before blaming a city. `snapshot` prints both and says so.
- **`ps` may be a pager-wrapper alias** on these boxes and fails *silently* with positional
  args — `ps -p <pid>` prints nothing and you conclude the process is gone. Use `/bin/ps`.
- **`pkill -f 'supervisor run'` matches its own shell** and kills the command running it
  (exit 144). Resolve PIDs with `pgrep -x`, then `kill` them by number.
- **`gc start --dry-run` is not dry** — it previews agent starts but performs beads
  provisioning for real.
- **Registered ≠ running, and unregistered ≠ stopped.** Check the process table.
