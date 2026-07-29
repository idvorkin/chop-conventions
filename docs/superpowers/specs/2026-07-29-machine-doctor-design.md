# Machine Doctor: historical resource forensics

**Date:** 2026-07-29
**Status:** Approved, pending implementation
**Skill:** `skills/machine-doctor`

## Problem

`machine-doctor` can only answer questions about *now*. Tier 1a is
`ps aux --sort=-%cpu | head -20` — a point-in-time list. When the box was slow
twenty minutes ago, there is nothing to consult.

The reactive layer does not fill the gap. `cpu-watchdog.sh` (running on this box,
started from `~/.zshrc`) scans every 30s but writes a line only when a process
exceeds **400% CPU**, and only at the moment it throttles: **23 lines in two
months**. Anything below that threshold, all memory pressure, and all swap
activity leave no trace.

Four incidents in one session, none answerable after the fact:

| Symptom | What it actually was | Why it was hard |
|---|---|---|
| load average 26 | other tenants + registration cruft, CPU 92% idle | gone before inspection; load ≠ CPU |
| `idle=1%`, load 21 | a local Go build | had to be reconstructed from memory |
| 116 MB/s swap-out | transient reclaim during that build | sampled once; misread as sustained thrash |
| orphaned tmux server, 2d5h old | a city teardown that missed a process tree | found only by manually enumerating sockets |

The common failure: **the evidence expires before anyone looks.**

## Goals

1. Answer "who was hot at time T" retroactively.
2. Answer "who has been hot over the last N hours" — ranked, aggregated.
3. Preserve enough detail at the interesting moments to name a culprit, including
   what spawned it.
4. Keep the Gas City leak-hunting already built, as one profile among many.

## Non-goals (YAGNI)

- No always-on daemon and no `~/.zshrc` hook. Zero idle cost is a hard
  requirement; the operator on this box is actively hostile to background
  processes, with cause.
- No replacement for `cpu-watchdog.sh`. That stays the **reactive** layer
  (throttling); this is the **observational** layer. They are complementary and
  must not be merged — see "Relationship to cpu-watchdog" below.
- No per-process disk I/O, no network monitoring, no alerting integration.

## Architecture

```
skills/machine-doctor/tools/
  machine_doctor.py     Typer CLI + orchestration
  md_probe.py           pure: /proc parsing, spike detection, aggregation
  md_store.py           SQLite read/write, spike dumps, pruning
  profiles.py           known-leak checks: generic + gascity
```

Pure logic is separated from I/O so spike detection and aggregation are testable
in system Python without subprocess mocking, matching `up-to-date/diagnose.py`
and the existing `gascity_doctor.py`. Typer stays behind `_build_app()` so tests
import the pure layer without `uv`.

Four files rather than one because the units have genuinely different
dependencies: `md_probe` touches only `/proc` and strings, `md_store` only
sqlite3 and the filesystem, `profiles` only the probe output. Each can be
understood and tested without reading the others.

## Sampling model: adaptive

Every interval (default 30s), write a **cheap sample**: system totals plus the
top-N processes by CPU and RSS. When a spike trigger fires, *additionally* write
a **full process-tree dump** for that instant.

Steady state stays small; full forensic detail exists exactly where something
went wrong. Always-full-tree was rejected as too costly for ~700 processes;
always-light was rejected because it cannot tell you what spawned the hot
process, which is usually the actual answer.

### Spike triggers

All configurable; defaults chosen from the incidents above:

| Trigger | Default | Rationale |
|---|---|---|
| any process CPU | > 300% | below `cpu-watchdog`'s 400% throttle, so spikes are captured *before* the reactive layer fires |
| system idle | < 25% for 2 consecutive samples | 2 samples suppresses single-sample noise |
| MemAvailable | < 10% of total | memory pressure precedes OOM kills |
| swap-out | non-zero for 2 consecutive samples | the signal misread as sustained thrash when it was one transient burst |

Two consecutive samples is the general rule: a single sample is an artifact, not
an event.

### CPU% semantics

`cpu_pct` is measured **over the sampling interval** — the delta of
utime+stime from `/proc/<pid>/stat` between consecutive reads — not the
process-lifetime average `ps` reports. A long-lived process that starts
spinning shows the spike immediately; its lifetime average would dilute it
toward zero, masking exactly the incidents this tool exists for. Consequences:

- The first sample of a `watch` run has no previous read: it records NULL
  `cpu_pct` and ranks its top-N by RSS only. No spike trigger can fire on it.
- `snapshot` is one-shot, so it takes two reads ~1s apart to compute a usable
  interval.

## Data model

### SQLite — `~/.local/state/machine-doctor/samples.db`

`sqlite3` is stdlib; no new dependency. Aggregation is the core query, and that
is what SQL is for.

```sql
CREATE TABLE sample (
    ts            INTEGER PRIMARY KEY,   -- unix seconds
    load1         REAL,
    idle_pct      INTEGER,
    mem_avail_kb  INTEGER,
    swap_used_kb  INTEGER,
    swap_out      INTEGER,               -- KB/s swapped out, vmstat `so`
    nproc         INTEGER,
    is_spike      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE proc_sample (
    ts        INTEGER NOT NULL,
    pid       INTEGER NOT NULL,
    ppid      INTEGER,
    comm      TEXT,
    cpu_pct   REAL,
    rss_kb    INTEGER,
    etime_s   INTEGER,
    PRIMARY KEY (ts, pid)
);
CREATE INDEX idx_proc_ts   ON proc_sample(ts);
CREATE INDEX idx_proc_comm ON proc_sample(comm);
```

`proc_sample` holds top-N only (default N=10 by CPU, union top-10 by RSS).

### Spike dumps — `~/.local/state/machine-doctor/spikes/<iso8601>.txt`

Human-readable full process tree for one instant: pid, ppid, comm, cpu, rss,
etime, and the parent chain. Written only on a spike.

**Command lines are redacted before they are written.** Agent sessions carry
credentials in argv (`-e ANTHROPIC_API_KEY=…`), world-readable via `/proc`; a
2d5h orphaned session on this box exposed exactly that. Reuse the existing
`redact()` from `gascity_doctor.py`.

### Retention

Samples 7 days; spike dumps the 50 most recent. Both configurable, both pruned
at the **start and end** of each `watch` run. End-only pruning was rejected: a
`watch` killed by a monitor teardown never reaches its end, and a box whose
watches always die that way would never prune at all.

## Commands

| Command | Answers |
|---|---|
| `watch [--interval 30] [--spike-cpu 300]` | samples into the store; prints only on state transitions |
| `report [--since 6h] [--top 10]` | who has been hot over that window, ranked |
| `at <time>` | what was running then — nearest sample, plus the full tree if it was a spike |
| `snapshot [--profile gascity]` | point-in-time state plus known-leak checks |

### Time and duration formats

Specified here because "6h" and "07:16" each admit more than one reading:

- `--since` takes a **relative duration**: an integer followed by `s`, `m`, `h`,
  or `d` (`30m`, `6h`, `2d`). No bare numbers — `--since 6` is an error, not
  six of something.
- `at` takes either a **wall-clock time today** (`07:16`, `07:16:38`) or a full
  **ISO-8601 timestamp** (`2026-07-29T07:16:38`). A bare time that is in the
  future for today resolves to yesterday, so `at 23:50` works at 00:10.
- `at` resolves to the **nearest sample within a tolerance** — default 60
  seconds, `--tolerance` to widen. A fixed tolerance rather than "half the
  sample interval" because the interval in effect at record time is not stored;
  there is nothing to derive it from at query time. No sample in range is
  reported as "no sample near that time", never as an empty or quiet result.

### Report ranking

`report` groups by `comm` and ranks by **summed interval CPU** across the
window's samples (proportional to CPU-seconds at a fixed interval), with peak
RSS as the secondary ranking. Grouping by pid was rejected: build-style
workloads — the Go-build incident — split their load across hundreds of
short-lived pids, and no per-pid ranking would surface any of them. The
`idx_proc_comm` index exists for this query.

`watch` prints one line per transition and nothing otherwise, so it is safe to
hand to a long-running monitor; silence means no change. This is the existing
`gascity_doctor.py watch` contract, preserved.

## Runtime

On-demand only. `watch` runs when started — by the operator, or by an agent via
a monitor at session start. History accrues while work is happening, which is
when the box gets hot, at zero cost when idle.

**Accepted limitation:** an incident nobody was watching leaves no history. This
is a deliberate trade for zero idle footprint. `report` and `at` state plainly
when the requested window has no samples, rather than implying nothing happened —
an empty result must never be mistaken for a quiet box.

## Profiles

Two, registered in one module — `N=2` justifies a small registry, not a plugin
framework:

- **generic** — hot processes, memory pressure, swap, zombies/orphans.
- **gascity** — orphaned `__gc-managed-dolt-scope-watchdog` processes, orphaned
  per-city tmux servers, city-scoped vs `.beads/`-repo dolt classification.
  Carries over unchanged from `gascity_doctor.py`, including the rule that a
  `bd`-owned repo store is never reported as a leak.

## Testing

Pure functions in `md_probe.py` and the aggregation in `md_store.py` are
unit-tested with stdlib `unittest`, runnable as
`python3 -m unittest` from the tools directory:

- `/proc` parsing against captured fixture text (no live `/proc`).
- Spike detection: each trigger, plus the two-consecutive-samples rule, plus the
  recovery edge.
- Redaction: keys stripped, benign env preserved.
- Aggregation: ranking over a seeded in-memory SQLite database.
- Retention: pruning drops old rows and keeps the newest N dumps.

## Relationship to `cpu-watchdog.sh`

Deliberately separate processes, in separate repos, with separate jobs.
`cpu-watchdog.sh` (settings repo) *reacts* — it attaches `cpulimit` to runaways.
This tool *observes*. Folding sampling into the watchdog was rejected: it would
couple chop-conventions to the settings repo, put Python logic into a bash
script, and mix throttling with observation in one process. A diagnostic should
not share a lifetime with the thing that mutates the system it is measuring.

## Migration from PR #197

PR #197 currently adds a Gas City-specific tier. Rework:

| Now | After |
|---|---|
| `tools/gascity_doctor.py` | `tools/machine_doctor.py` + `md_probe.py` + `md_store.py` + `profiles.py` |
| `tools/test_gascity_doctor.py` | tests split alongside their modules |
| `doctor-gascity.md` | kept — the Gas City runbook is still correct, now described as a profile |
| SKILL.md tier `/machine-doctor gascity` | `/machine-doctor watch` / `report`, with gascity as `--profile` |

The 29 existing tests carry over; the classification and redaction logic is
reused rather than rewritten.

## Risks

1. **`/proc` walk cost at 30s.** ~700 processes per sample. Mitigation: read only
   the fields needed, top-N filter before any string formatting; measure and
   report the walk's own duration in `watch` output.
2. **Sampler perturbs what it measures.** It appears in its own samples. It must
   be identifiable and excludable from `report` rankings by default.
3. **Clock changes** break `ts`-ordered queries. `ts` is wall-clock unix seconds
   — required, since `at 07:16` must map to human time — so a backward step can
   produce out-of-order or colliding rows. Mitigations: `ts` is the primary key,
   so a collision fails loudly rather than silently overwriting; `at` resolves to
   the nearest sample rather than assuming an exact match; `watch` logs a line
   when it observes time moving backwards between samples.
