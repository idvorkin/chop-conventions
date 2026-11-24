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
| `scripts/native/test-ladder-map.sh`, `test-x.py` | `script:test-ladder-map.sh` (basename)  | host                                       |

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
- `outcome` is `pass`, `flaky` (failed, then passed a serial retry), `fail`, `known-red` or `skip`. A ref the stories name that the runner never reports is judged
  `missing`, which is red.
- Key pytest results by both `path/test_x.py::…` and `test_x.py::…`, since stories name the bare file.
- Exit codes: 0 no new red, 1 new red, 2 the walk could not run (no browser, no toolchain). A scheduler alerts on 2
  differently from 1.
- Walk a clean tree: a detached worktree of the canonical main, not the primary checkout someone is editing. Record
  the SHA and dirty flag either way.
- A clean tree lacks what the repo builds locally and never commits (a WebAssembly bundle, a generated asset). The
  runner's setup builds or links it before any check runs, or every check that needs it times out as a false red.
  The Cockpit's first dry run lost ~20 ink audits that way.
- Under load: `-n 2` at most, everything under `nice -n 19` (and `ionice -c 3` on Linux). A walk is batch work.
- Retry each failed check once, serially, and report one that passes as `flaky`: it counts as passing and is
  listed in its own section, so load never files a bug.
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
are safe to share, but they are never committed either.

### Screens and their feedback notes

Every story with a screen the walking machine can render gets a screenshot, and a short note beside it. On the VM
that means web pages, the Cockpit's ink board, and a repo's web demo. iOS and macOS screens are **needs Mac**.

- **One Playwright session per walk.** Launch the browser once, take every state at **390 and 1200 wide**, and write
  `OUT/shots/<state>-<width>.png` and `OUT/shots.json` (state, width, file, page errors, sideways scroll, facts read
  off the page). Run it from a checkout that has `node_modules/playwright` (`~/gits/idvorkin.github.io`), on a fixture
  stage, never a live server.
- **A note per screenshot**, two or three sentences:
  - Does the screen match the story's Given/When/Then, or the part of it a still can show?
  - Is anything visibly off: clipping, overlap, an empty state, contrast, sideways scroll?
  - Lead with "Matches" or "Off". The facts in `shots.json` go into the prompt, so the note never contradicts them.
- **The report puts each note beside its screenshot**, in a Screens section grouped by state, with the stories each
  state covers and their verdicts.
- **Nightly, a direct Anthropic API call writes the notes**, one per screenshot, never Claude Code. By the house rule
  it uses the advisor tool: `claude-haiku-5-5` as executor, with
  `{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-5-5"}` on
  `client.beta.messages.create(..., betas=["advisor-tool-2026-03-01"])`. Send the PNG, the story's
  Given/When/Then and the facts.
  - Haiku 5.5 with an Opus 5.5 advisor is a valid pair. An Opus advisor returns encrypted advice: the
    `advisor_tool_result` block's `content` is `advisor_redacted_result` (`encrypted_content`), and it can be replayed
    but not read. Switch on that `content` type (`advisor_result`, `advisor_redacted_result` or
    `advisor_tool_result_error`), never on the block type, and never read advice through `.text`.
  - The note is the executor's own answer: the `text` blocks of `response.content`. For a follow-up turn, such
    as asking for a shorter note, append the whole `response.content`, advisor blocks included. Set the advisor's
    `caching` once per conversation, because changing it later invalidates the thinking already in the history.
  - Check `stop_reason` before taking the text. Haiku has no server-side refusal fallback, so a refused note is
    left empty and the report says so.
- **In a dry run, the agent reads the PNGs** and writes `OUT/notes.json` itself.
- **A note never makes a verdict.** A screen that looks off is a line for a person to read. Only a check that
  fails turns a story red.

Keep the last walk where the next can find it (`~/tmp/agent/walks/<repo>/latest` → the newest run) and pass it as
`--previous`. The first walk has nothing to diff, so the report lists every red as new.

### Reds become beads

`file-reds` makes one bead per (red story, failing ref) in the repo's own bead store, with
`--external-ref walk:<repo>:<story>:<ref>` as the dedupe key and the label `walk-the-store`. It prints the `bd create`
commands by default; `--apply` files them, skipping any key with a bead still open, and `--parent` nests them under
the repo's walk epic. New and still-red alike are filed, so a red that was never filed gets its bead on the next walk;
closing a bead while its check is still red refiles it. A known red is never filed: it already has its bead.

A **first walk files nothing**: its reds are the baseline (the Cockpit's first walk had 149 red checks, most of them
one cause). Read the report, fix the walk's own mistakes, then pass `--baseline` to file what is left.

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
    argv: [bash, -c, "~/gits/<repo>/scripts/walk-nightly.sh"]
```

The job's script does three things the daemon won't:

1. **Files reds even when the walk exits 1.** Never chain `just walk && file-reds`: exit 1 means "new red", which is
   exactly when there is something to file.
2. **Alerts on exit 2 itself.** The daemon only logs a non-zero exit from a command job. A walk that could not run
   looks exactly like a green one, so the script sends its own out-of-band line (the same channel as the
   machine's other alerts).
3. **Moves `latest`** to the new run, so the next walk diffs against it.

```bash
just walk "$OUT"; rc=$?
case $rc in
  0|1) walk.py file-reds "$OUT/walk.json" --apply --parent <epic>; ln -sfn "$OUT" "$WALKS/latest" ;;
  *)   <send one alert line: "walk <repo> could not run (exit $rc), see $OUT"> ;;
esac
```

A silent job stays silent on green. Reds arrive as beads, read at the next session's start.

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

| Repo              | Stories | Implemented | of those, no named check | Named check kinds          | `just walk`                   |
| ----------------- | ------- | ----------- | ------------------------ | -------------------------- | ----------------------------- |
| cockpit           | 279     | 276         | 5                        | pytest, node, landmark     | dry run (reference runner)    |
| context-grabber   | 1234    | 1156        | 196                      | xctest, just, script, node | VM dry run, board stories     |
| exercise-analyzer | 84      | 77          | 34                       | xctest, just               | VM dry run                    |
| audio-debugger    | 12      | 7           | 5                        | xctest, just               | VM dry run (nothing to climb) |

The iOS repos name XCTest classes and `just` recipes; their VM walk is the Linux rung, and the rest waits on a Mac
walk. Their high untestable counts are stories verified "on the phone" or "on the simulator" with no test named.

### Worked example: the Cockpit, 2026-11-24

A dry run on the VM, at `-n 2` under `nice`, on a detached worktree of main. The runner followed the reference
shape for a `just walk`:

- the fast and browser rungs once each, with `--junitxml`;
- the route smoke on its fixture stage, with `--max-fails 0`;
- `node:` audits resolved to the pytest wrapper that executes them;
- one serial retry of every failed test.

Times: 4 min for the fast rung, 9 min for the browser rung (35 min while the wasm was missing and every ink audit
timed out), 37 s for the smoke and 3 min for the retry.

| Walk | Green | Red (known) | Skipped | Untestable | What changed                                                            |
| ---- | ----- | ----------- | ------- | ---------- | ----------------------------------------------------------------------- |
| 1    | 199   | 72 (3)      | 0       | 5          | first walk; the board's untracked WebAssembly was missing from the tree |
| 2    | 233   | 38 (3)      | 0       | 5          | wasm linked in, reds retried; 34 fixed, 0 new, 8 stories flaky          |

The 38 reds in walk 2 come from three places:

- **9 stories only name missing checks**: 13 tests or audits that git history shows deleted or renamed since the
  story cited them. Those are bugs in the stories, and no test run would have found them.
- **3 are known reds**: on the repo's own red list.
- **26 trace to four checks** that also fail when run alone: three WebKit ink audits (one is cited by 13 stories)
  and a hand audit that cannot stage its board. Several stories name the whole `test_ink.py`, so one red case
  turns all of them red.

That is why reds are filed per check, not per story. The retry mattered too: three reds from the parallel run passed
alone (a speed assertion, a boot audit and a WebKit playback audit), and without it they would have been filed as
bugs.

### Worked example: the iOS repos on the VM, 2026-11-24

These were dry runs on the VM's Linux rung only. Every XCTest class or `just` recipe that needs the Mac was written
`skip`, "needs Mac". A named class, test or recipe that is nowhere in the tree was written `missing`. Each runner
writes an explicit row for every class ref: the worst of that class's Linux cases. Without that row, judge's
prefix match would fold in a stale `Class.testGone` that another story cites.

| Repo (stories walked)             | Green (partial) | Red | Needs Mac | Untestable | Rungs on the VM                                                              |
| --------------------------------- | --------------- | --- | --------- | ---------- | ---------------------------------------------------------------------------- |
| context-grabber, board (663 impl) | 486 (346)       | 27  | 75        | 75         | Linux `swift test` (2634 cases), BoardWasm + node replay, board-ops, scripts |
| exercise-analyzer (77 impl)       | 39 (4)          | 0   | 4         | 34         | Linux `swift test` of ExerciseCore (415 cases)                               |
| audio-debugger (7 impl)           | 0               | 0   | 2         | 5          | none: AudioCore imports Accelerate unfenced, so it does not build on Linux   |

- **All 27 context-grabber reds are stale stories, not failing code.** Every Linux case passed or was flaky, and so
  did the wasm replay (319 checks). One timing test, `InboxOutboxTests`, failed under load and passed its serial
  retry. The 27 stories cite 14 checks that are no longer on main: 11 UI test classes or methods and 3 host ones.
  `git log -S` on upstream/main finds the commit that last touched each name, and none of them is in the tree now.
- **Most greens are partial.** The Linux rung passes, and the same story's simulator or `native-build-mac` proof
  waits for a Mac walk.
- **A test target cited by name** (`BoardWasmTests`) is the classes under `Tests/<target>/`.
- **A Swift Testing file** (`MicControllerTests.swift` with free `@Test` functions) counts as its file name.
