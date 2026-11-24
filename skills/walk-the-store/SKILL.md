---
name: walk-the-store
description: Walk the store — test every feature of an app the way its docs/stories say it works, nightly or on demand. Turns each implemented story's Status line into checks (host tests, Playwright web audits, simulator, device), runs them through the repo's `just walk`, and writes a per-story green/red/skipped/untestable report with screenshots, a diff against the last walk, and beads for new reds. Use when asked to walk the store, run every feature, check what still works, or set up a nightly feature walk for a repo with docs/stories.
allowed-tools: Bash, Read
---

# Walk the store

A store walk opens every aisle: for each story the repo says is implemented, run the check its Status line names
and say whether the feature still works. The stories are the inventory (`docs/stories/*.md`, Mike Cohn use case
plus Gherkin, a `Status:` line each), so the walk needs no second feature list and drifts only when the stories do.

The walk does not invent tests. A story whose Status names no runnable check is reported **untestable**, and that
list is the walk's second product: the stories nobody can prove today.

## The pieces

| Piece                               | Owned by              | Does                                                                                    |
| ----------------------------------- | --------------------- | --------------------------------------------------------------------------------------- |
| [`walk.py`](walk.py)                | this skill            | parses stories, lists the checks to run, judges verdicts, diffs walks, files reds       |
| `just walk [OUT]`                   | each repo             | runs its rungs once, writes `OUT/checks.json`, calls `walk.py judge`                    |
| `docs/stories/walk.toml` (optional) | each repo             | overrides: explicit checks, untestable reasons, refs to ignore, kind → surface          |
| the schedule                        | the machine that runs | a nightly `command` job on the VM's scheduler daemon, or a person asking for a walk now |

`walk.py` is stdlib-only and repo-agnostic: two story dialects, any field order, tested by `test_walk.py`.
Running checks is per repo because every repo's rungs differ (pytest + Playwright here, `swift test` + XCUITest
there). Extract a shared runner only when a second repo's `just walk` wants the same code.

```bash
W=~/.claude/skills/walk-the-store/walk.py
$W stories docs/stories --implemented   # every implemented story: id, status class, refs, Given/When/Then
$W refs docs/stories --kind pytest      # the pytest node ids the stories name, one per line
$W judge --stories docs/stories --checks OUT/checks.json --out OUT \
   [--manifest docs/stories/walk.toml] [--previous LAST/walk.json]
$W file-reds OUT/walk.json [--apply] [--parent <bead>]
```

## From story to check

**Status class** comes from the Status line's lead clause (before the first `;`), so "implemented in X; the old
flow superseded" is implemented and "superseded by 140; before that implemented in X" is retired:

| Lead clause                                                                    | Class        | Walked?                        |
| ------------------------------------------------------------------------------ | ------------ | ------------------------------ |
| `implemented …`, `Done …`, `already true …`, `started …`, a leading commit SHA | implemented  | yes                            |
| `planned …`, `not implemented …`, `parked …`, `In progress …`                  | planned      | no, counted                    |
| `superseded …`, `replaced …`, `removed …`                                      | retired      | no, counted                    |
| anything else                                                                  | unclassified | no, listed for the repo to fix |

A scope prefix (`native app only:`, `app side`) is skipped before the lead clause is read.

**Refs** are the backticked names in the Status line, normalised to `<kind>:<target>`:

| Written in the Status                            | Ref                                     | Default surface                            |
| ------------------------------------------------ | --------------------------------------- | ------------------------------------------ |
| `test_x.py::test_y`, then `::test_z` or `test_z` | `pytest:test_x.py::test_y`, `…::test_z` | host                                       |
| `test_x.py` alone                                | `pytest:test_x.py` (worst of its cases) | host                                       |
| `route_smoke.cjs` `card-open` (a label after it) | `landmark:route_smoke.cjs#card-open`    | web                                        |
| `talk_audit.cjs`, `test_ops.mjs`                 | `node:talk_audit.cjs`                   | web                                        |
| `BreathPlanTests`, `BreathPlanTests.testLeft`    | `xctest:BreathPlanTests[.testLeft]`     | host                                       |
| `just native-test-sim`                           | `just:native-test-sim`                  | host (override per recipe in the manifest) |

Prose rungs with no name ("verified on the simulator", "on the phone", "by hand", "X's check pending") are recorded
as `rungs` claims but are not checks: the walk cannot rerun an eye.

**Verdicts**, one per story; with the three not-walked classes they sum to the story count, and `judge` asserts it:

| Verdict        | Means                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| **green**      | every ref that ran passed (`partial` when some refs were skipped as their surface did not run)                |
| **red**        | a ref failed, or a named check was **missing** from the results (the story's proof is gone: renamed, deleted) |
| red, `known`   | the only failures are known-red (pytest `xfail`, an allow-list): not new, not filed                           |
| **skipped**    | refs exist but none of their surfaces ran this walk (simulator refs on the VM, device refs with no go-ahead)  |
| **untestable** | implemented, but the Status names no runnable check (or the manifest says why it can't be automated)          |

## The `just walk` contract

`just walk [OUT]` (default `~/tmp/agent/walks/<repo>/<YYYY-MM-DD-HHMM>/`) runs every rung the machine can climb
**once**, never story by story, then writes `OUT/checks.json` and calls `walk.py judge`:

```json
{
  "repo": "cockpit",
  "sha": "74f7834",
  "dirty": false,
  "started": "…",
  "finished": "…",
  "kinds": ["pytest", "node", "landmark"],
  "checks": {
    "pytest:test_ask_expiry.py::test_the_chip_says_the_default": {
      "outcome": "pass"
    },
    "landmark:route_smoke.cjs#ask-lightbox": {
      "outcome": "fail",
      "detail": "…",
      "screenshot": "OUT/…png"
    },
    "node:talk_audit.cjs": { "outcome": "known-red", "detail": "…" }
  }
}
```

- `kinds` is what ran. A ref whose kind is absent is **skipped**, not red: that is how one report covers a VM walk
  and a Mac walk honestly.
- `outcome` is `pass`, `fail`, `known-red` or `skip`. A ref the stories name that the runner never reports is judged
  `missing`, which is red.
- Key pytest results by both `path/test_x.py::…` and `test_x.py::…`, since stories name the bare file.
- Exit codes: 0 no new red, 1 new red, 2 the walk could not run (no browser, no toolchain). A scheduler alerts on 2
  differently from 1.
- Walk a clean tree: a detached worktree of the canonical main, not the primary checkout someone is editing. Record
  the SHA and dirty flag either way.
- Under load: `-n 2` at most, everything under `nice -n 19` (and `ionice -c 3` on Linux). A walk is batch work.
- Cap every check and every stage, so one stuck story can't stall the walk: a per-test timeout (`pytest-timeout`,
  `--timeout 300`; longer for browser audits) makes a hung check a red with `detail: Timeout`, and a stage cap kills
  the stage's whole process group (servers and browsers it started) and makes the walk exit 2. A runner idling at
  0% CPU is usually waiting on a child: read its process tree before calling it hung.
- Wait on a running walk by PID (`while kill -0 <pid>; do sleep 15; done`), never with `pgrep -f <name>`, which
  matches the wait loop's own command line and never exits.

Each `node:` audit usually runs behind a pytest wrapper. The runner resolves the script to the wrapper's result
(e.g. `test_<stem>.py`, or the parametrized case of a hand-audit list) rather than running it a second time.

### The manifest

Most stories need nothing: their Status already names the check. `docs/stories/walk.toml` covers the rest:

```toml
ignore_refs = ["^xctest:Chat"]            # names from another repo, mentioned for parity

[surfaces]                                # kind → surface, for the dispatch table below
just = "simulator"

[stories.002]
checks = ["pytest:test_promote.py"]       # replaces the Status-derived refs

[stories.160]
untestable = "the sound of the session needs ears"
```

A story that keeps landing in untestable is a prompt to write its test and name it in its Status, not to add a
manifest line: the manifest is for what the Status can't say.

## The report

`judge` writes `OUT/walk.json` (every story, its verdict, each ref's outcome) and `OUT/report.md`:

1. one line of counts: green · red (known) · skipped · untestable, of N implemented; planned/retired/unclassified;
2. the diff against `--previous`: new reds, fixed, still red, stories added or removed;
3. sections for new reds, still red, known red, fixed, skipped, untestable, unclassified, then the greens.

Screenshots: a web runner saves one per failing landmark and may shoot the main routes for the eye. **A screenshot of
real data stays local** under `~/tmp/agent/`: never attach it to a public PR, gist or issue. A fixture stage's shots
are safe to share.

Keep the last walk where the next can find it (`~/tmp/agent/walks/<repo>/latest` → the newest run) and pass it as
`--previous`. The first walk has nothing to diff, so every red counts as new.

### Reds become beads

`file-reds` makes one bead per (new red story, failing ref) in the repo's own bead store, with
`--external-ref walk:<repo>:<story>:<ref>` as the dedupe key and the label `walk-the-store`. It prints the `bd create`
commands by default; `--apply` files them, skipping any key with a bead still open, and `--parent` nests them under
the repo's walk epic. A known red is never filed: it already has its bead.

## When it runs

**On demand.** Someone asks "walk the cockpit": run `just walk` in the repo (or the reference runner while the
repo has none), read `report.md`, say the counts and the new reds in a sentence, link the report.

**Nightly.** One `command` job per repo on the VM's scheduler daemon, at a quiet hour, staggered so two walks never
overlap. Command jobs there are synchronous and capped (`timeout_s`, 120 s by default), so either raise the cap past
the walk's length or launch the walk detached and let it report on its own:

```yaml
- name: walk-cockpit
  kind: command
  at: ":13"
  hours: [2]
  slow: true
  action: silent
  options:
    timeout_s: 3600
    argv:
      [
        bash,
        -c,
        "cd ~/gits/<repo> && just walk && ~/.claude/skills/walk-the-store/walk.py file-reds ~/tmp/agent/walks/<repo>/latest/walk.json --apply --parent <epic>",
      ]
```

A silent job stays silent on green. A new red arrives as beads, read at the next session's start; exit 2 (could not
run) deserves the daemon's out-of-band alert, because a walk that silently stopped running looks exactly like a
green one.

## Where a walk runs

Walks follow the [`ios-codev`](../ios-codev/SKILL.md) head/builder model and its dispatch rules. The walk is
another kind of work lane: the head (or the scheduler) dispatches each surface to a machine that can climb it.

| Surface                               | Runs on                                               | Notes                                                                                                                       |
| ------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| host: pytest, node, Linux Swift, wasm | the Linux VM                                          | the nightly default; caps above                                                                                             |
| web: Playwright audits, route smoke   | the Linux VM                                          | Playwright borrowed from a checkout that has it (`NODE_PATH`); fixture stage, never the live server                         |
| host: `swift test`, Mac-only Swift    | a Mac builder                                         | in a host slot                                                                                                              |
| simulator: XCUITest, sim recipes      | a Mac builder                                         | inside a build lane, through the repo's lane wrapper; never boot a simulator outside one                                    |
| device: anything installed on a phone | a Mac builder, **only with the owner's own go-ahead** | a walk never installs a build by itself; device refs are skipped until the owner says go, in their own words, for that walk |

One repo can be walked by two machines: the VM writes `checks.json` with `kinds: [pytest, node, landmark]`, a Mac adds
`[xctest, just]`, and `judge` runs over the merged results. Until a Mac walk exists, the simulator and device stories
of an iOS repo are honestly **skipped**, which is the point of the column.

## A repo joins

1. Its stories have a Status line per story that names the check (the repos below already do).
2. `walk.py stories docs/stories` parses every story and the unclassified list is empty or explained.
3. A `just walk` recipe that follows the contract above, exit codes included.
4. A manifest only if the Status lines can't carry it.
5. A nightly job on the VM, and a walk epic for reds.

Where the reference consumers stood on 2026-11-24, by `walk.py stories`:

| Repo              | Stories | Implemented | of those, no named check | Named check kinds      | `just walk`                |
| ----------------- | ------- | ----------- | ------------------------ | ---------------------- | -------------------------- |
| cockpit           | 279     | 276         | 5                        | pytest, node, landmark | dry run (reference runner) |
| context-grabber   | 1233    | 1155        | 223                      | xctest, just, node     | —                          |
| exercise-analyzer | 72      | 67          | 36                       | xctest, just           | —                          |
| audio-debugger    | 12      | 7           | 5                        | xctest, just           | —                          |

The iOS repos name XCTest classes and `just` recipes; their VM walk is the Linux rung, and the rest waits on a Mac
walk. Their high untestable counts are stories verified "on the phone" or "on the simulator" with no test named.
