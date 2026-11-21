---
name: repo-doctor
description: Audit a repository against the house layout for Igor's repos (idvorkin/* and idvorkin-ai-tools/*) — AGENTS.md as the anchor, beads installed without hooks, just as the build/test entry point, VISION.md, the no-mistakes gate, jobs-to-be-done + user stories, bug-kit on iOS — and convert what is missing one item at a time. Use on /repo-doctor, when starting work in a repo that does not look like the others, when Igor asks whether a repo is "set up", or to sweep every repo under ~/gits.
allowed-tools: Bash, Read, Glob, Grep
---

# Repo Doctor

One layout for every repo Igor builds, so an agent that has worked in one can work in all of them. The
doctor is code (`tools/repo_doctor.py`); this file says when to run it, how to read it, and how to convert
a repo that fails. The conversion playbook is in [convert.md](convert.md) (loaded on demand).

| Invocation                            | What it does                                                              |
| ------------------------------------- | ------------------------------------------------------------------------- |
| `/repo-doctor`                        | Audit the current repo; print the table; exit 1 on any `FAIL`             |
| `/repo-doctor ~/gits/foo ~/gits/bar`  | Audit several repos                                                       |
| `/repo-doctor --scan ~/gits --matrix` | One row per house repo under `~/gits` (origin or upstream in a house org) |
| `/repo-doctor --json`                 | Machine-readable, for a conversion agent or a sweep                       |
| `/repo-doctor convert <repo> <check>` | Fix ONE finding on a branch + PR in that repo, per `convert.md`           |

```bash
~/.claude/skills/repo-doctor/tools/repo_doctor.py [--json] [--matrix] [--scan DIR] [REPO ...]
```

The script is read-only. It reads `.git/config`, `.git/info/exclude` and the index directly (no git
subprocess), so it is cheap enough to run over fifty repos.

## The checks

Each check reports `ok`, `WARN`, `FAIL`, `n/a` or `GAP`, with a one-line fix hint. Only `FAIL` sets the
exit code. `GAP` means the repo cannot meet the convention because the house tool does not exist yet.

| Check              | Healthy                                                                                                                 | Calibrated against                                                                               |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `agents_md`        | `AGENTS.md` is the one instruction file; `CLAUDE.md` is a symlink to it or a 2-line `@AGENTS.md` import                 | cockpit (symlink), exercise-analyzer and lcd-timer (import); context-grabber drifts              |
| `agents_pointers`  | `AGENTS.md` mentions every convention the repo has: `just`, `bd`/beads, `docs/stories`, `VISION.md`, no-mistakes        | exercise-analyzer                                                                                |
| `beads`            | `.beads/` exists (`bd init -p <prefix> --skip-hooks --skip-agents --non-interactive`)                                   | cockpit, bug-kit, context-grabber                                                                |
| `beads_local`      | `.beads/` in `.git/info/exclude`; no `.beads/` files tracked (beads stay on the box; igor2 AGENTS.md "Cross-Repo Work") | cockpit                                                                                          |
| `beads_hooks`      | `core.hooksPath` does not point into `.beads/hooks`; no bd scripts in `.git/hooks` (bd hooks swallowed prek failures)   | context-grabber is the counter-example                                                           |
| `justfile`         | a justfile with a `test` recipe and a build-ish one (`build*`, `run*`, `install*`, `dev*`, `app`, `serve*`, `start*`)   | exercise-analyzer, lcd-timer, context-grabber                                                    |
| `scripts_via_just` | every runnable file in `scripts/` is reachable from a just recipe                                                       | exercise-analyzer (`just pull-logs`, `just file-bugs`)                                           |
| `vision`           | `VISION.md` at the root, made with the vision skill (kunchenguid/vision, `~/gits/vision`)                               | none of the five had one on 2026-10-11; lavish-axi and no-mistakes do                            |
| `no_mistakes`      | `.no-mistakes.yaml` with `commands.test` (ideally `just test`) or `test.instructions`                                   | exercise-analyzer, cockpit, lcd-timer, bug-kit                                                   |
| `stories`          | `docs/stories/README.md` (persona, format, Status vocabulary) + `NN-<journey>.md`; Mike Cohn use case + Gherkin         | exercise-analyzer, cockpit, lcd-timer, bug-kit; context-grabber's `docs/user-needs.md` is `WARN` |
| `jtbd`             | `docs/jtbd.md`, or a `## Jobs to be done` heading in `VISION.md` / `docs/stories/README.md` / `README.md`               | context-grabber `docs/jtbd.md`                                                                   |
| `bug_reporting`    | iOS (`SDKROOT = iphoneos` in a pbxproj, or `.iOS(` in `Package.swift`): depends on `idvorkin/bug-kit`. Non-iOS: `GAP`   | exercise-analyzer (`XCRemoteSwiftPackageReference "bug-kit"`)                                    |

`bug_reporting` on a non-iOS repo is a **known gap** (Igor, 2026-10-11): there is no bug-kit-like library
for Python, web or macOS repos yet. Report it; do not build one as part of a conversion.

## Reading the output

- A repo is **healthy** when nothing is `FAIL`. `WARN` is a judgment call to raise with Igor, not a
  blocker; `GAP` is nobody's fault.
- The fix hint names the mechanism, not a script: VISION.md, stories and JTBD cannot be generated blind.
  Those conversions draft for Igor's review (see `convert.md`), they never commit a vision he did not
  approve.
- `--scan` treats any direct child of the directory with an `idvorkin` or `idvorkin-ai-tools` remote as a
  house repo. Forks of other people's projects (lavish-axi, no-mistakes, beads) show up too; their
  findings are informational, not work.
- The script never writes to the target repo. Converting is a separate, deliberate step.

## Converting

Read [convert.md](convert.md). The rules in one breath: one finding per branch, PR in the target repo, never
push its default branch, read the target's `AGENTS.md` first, and the three judgment items (vision,
stories, JTBD) end at a draft for Igor, not a merge.

## Keeping the doctor honest

- A new convention is a new `check_*` function in `tools/repo_doctor.py` plus a row above and a test in
  `tools/test_repo_doctor.py`. Prose here never carries a path the script does not check.
- Calibrate against real repos before changing a pattern: the five used on 2026-10-11 were
  exercise-analyzer, cockpit, bug-kit, context-grabber and lcd-timer.
- `just test` in chop-conventions runs the tests (uv, pytest, typer).
