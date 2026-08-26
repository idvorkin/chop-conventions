---
name: bg-agent-brief
description: Use when dispatching a substantive background agent (Agent tool) and you're about to hand-write the dispatch prompt — the role/READ-FIRST/constraints/work/VERIFY/push-rules/report skeleton. Fires for any non-trivial delegation, same-repo or cross-repo. Also use for the SendMessage form when course-correcting a running agent.
allowed-tools: Bash, Read
---

# bg-agent-brief

The dispatch prompt a main session hand-writes for every substantive
background agent is the single highest-volume reinvention measured across
real sessions: **~178 KB of near-identical prompt prose in one 27-hour
session across 49 dispatches, 43 sharing the same skeleton.** This skill
captures that skeleton and ships a generator so a dispatching Claude
**fills a form instead of retyping ~3.6 KB of boilerplate.**

N=2 is far exceeded — 49 instances in a single session. The abstraction
is earned. (Per chop-conventions "Abstractions: Wait for N=2", one
instance would be copy-paste bait; this is not that case.)

## Why a new skill, not an extension of `delegate-to-other-repo`

`delegate-to-other-repo` owns the **cross-repo infrastructure**: resolving
a target repo, fetching, creating a worktree in *another* repo, fork-vs-direct
detection, the parent/subagent handoff. Its `brief-template.md` is **one
fixed instance** of a brief, hardcoded for the cross-repo-PR case.

`bg-agent-brief` owns the **prompt skeleton itself**, which applies to
**any** substantive dispatch — including same-repo dispatches (run the
tests, produce a screenshot, do isolated work in the current repo) that
never touch the cross-repo worktree machinery. Many of the 49 measured
dispatches were same-repo. Folding this generator into
`delegate-to-other-repo` would force same-repo callers to pull in worktree
setup and fork detection they don't need.

So they compose rather than merge: `delegate-to-other-repo` builds the
infrastructure, then **can** call this generator to assemble the brief
body. This one is the sibling that any dispatch reaches for.

## When to use

- You're about to dispatch a background agent (Agent tool, usually
  `run_in_background: true`) for anything beyond a one-line lookup.
- You catch yourself typing "You are a subagent… Read CLAUDE.md first…
  VERIFY… open a PR…" — stop and fill the form.
- You need to course-correct a **running** agent — use the `followup`
  subcommand for the SendMessage form.

**Don't** bother for a trivial fan-out (a bare Explore search, a
single-fact lookup). The skeleton earns its keep only for substantive,
lifecycle-owning dispatches.

## The skeleton (7 sections, in order)

Measured element frequencies out of 49 dispatches in parentheses.

1. **One-sentence role** — what this agent is and its single deliverable.
2. **READ FIRST, in order** (26/49) — an *ordered* list: target repo's
   `CLAUDE.md` → relevant README → the design bead/issue/spec. Ordered
   because later files assume earlier context.
3. **Constraints / workspace rules** (26/49) — "do NOT create a worktree,
   the live server serves this path"; "do NOT edit `<file>`, another agent
   changed it"; "cwd does not persist between Bash calls, pass `-C` or
   re-`cd`"; a worktree's `.git/` is shared with concurrent agents.
4. **The work** — numbered, each item carrying the user's **verbatim**
   words where they exist, plus known gotchas injected inline.
5. **VERIFY BEFORE YOU REPORT DONE** (28/49) — concrete checks: `curl` for
   HTTP 200, exercise the endpoint once, screenshot at a real viewport,
   run the tests. And critically: **clean up test artifacts.** Only 5/49
   dispatches said this and its absence bit twice — a scratch bead,
   marker row, or seeded log line left in real data is the igor2-88g.114
   defect class. The generator makes this line **default-ON**.
6. **Repo push rules** — Bitbucket vs GitHub (igor2 is Bitbucket, `gh pr
   create` does NOT work there — open the PR via browser URL), never push
   main/master, fork-vs-origin.
7. **Report contract** (43/49) — exactly what to return: the PR URL, the
   screenshot path, the specific confirmations.

### SendMessage follow-up form (course-correcting a running agent)

Used 9 times in one session. To add a correction to an **in-flight** agent
without restating the whole brief, send (via SendMessage with the agent's
task id):

    <LABEL> from Igor, verbatim: '<his words>'

where `<LABEL>` is one of **ADDITIONAL REQUIREMENT**, **SCOPE REDUCTION**,
or **GOVERNING DESIGN PRINCIPLE**. The `followup` subcommand emits this.

## The generator

Fill the form; get the assembled brief on stdout to paste into the Agent
tool's `prompt` parameter.

```bash
skills/bg-agent-brief/assemble_brief.py brief \
  --role "Rebuild the Cockpit favicon and redeploy." \
  --worktree /abs/path/to/worktree \
  --read-first "igor2/CLAUDE.md" \
  --read-first "decision_queue/README.md" \
  --constraint "do NOT create a worktree; the live server serves this path" \
  --work "swap the emoji favicon for an instrument SVG" \
  --verify "curl -sf localhost:8778 -> HTTP 200" \
  --push bitbucket --repo-slug idvorkin/igor2 \
  --default-branch master --branch cockpit-favicon \
  --report "Bitbucket PR URL" --report "screenshot at 390px"
```

`--read-first`, `--constraint`, `--work`, `--verify`, and `--report` are
repeatable and preserve order. Only `--role` is required.

**Push targets** (`--push`):

| value           | behavior                                                              |
| --------------- | -------------------------------------------------------------------- |
| `github-fork`   | (default) push to `origin` fork, `gh pr create --repo <slug>`        |
| `github-direct` | push to `origin`, plain `gh pr create` (origin is canonical)         |
| `bitbucket`     | `gh` does NOT work — emits the browser PR-create URL to print back   |
| `none`          | no push/PR; results in the final report only                         |

**Cleanup line** is ON by default (`--no-cleanup` to drop it). Keep it on
unless the agent genuinely creates no data — it's the igor2-88g.114
guard.

**SendMessage follow-up:**

```bash
skills/bg-agent-brief/assemble_brief.py followup \
  --kind scope-reduction \
  --verbatim "just the header, skip the sub-pages"
```

`--kind` is one of `additional-requirement`, `scope-reduction`,
`governing-principle`.

## Checklist (for callers writing the brief by hand)

Even without the generator, a good brief has, in order:

- [ ] **Role** — one sentence, one deliverable.
- [ ] **READ FIRST** — ordered: CLAUDE.md → README → design bead/spec.
- [ ] **Constraints** — worktree/cwd/edit fences; shared `.git/` warning.
- [ ] **The work** — numbered, user's verbatim words, gotchas inline.
- [ ] **VERIFY** — concrete checks (curl/tests/screenshot) **and clean up
      every test artifact** (igor2-88g.114 guard).
- [ ] **Push rules** — Bitbucket vs GitHub, never main/master, fork-vs-origin.
- [ ] **Report contract** — exactly what to return.

## Testing

```bash
cd skills/bg-agent-brief && python3 -m unittest test_assemble_brief
```

Pure assembly (`assemble_brief`, `assemble_followup`, `push_rules_block`)
is unit-tested in isolation — no subprocess mocking. `typer` is
lazy-imported in `_build_app()` behind the `__main__` guard, so the tests
run in system Python.

## Supplementary files

- [`assemble_brief.py`](assemble_brief.py) — the generator (Typer CLI,
  `brief` + `followup` subcommands, `uv run --script` self-bootstrap)
- [`test_assemble_brief.py`](test_assemble_brief.py) — unit tests for the
  pure assembly functions

## Related

- `delegate-to-other-repo` — the cross-repo infrastructure skill this one
  generalizes. It sets up the worktree + fork detection; this generator
  assembles the brief body that any dispatch (same-repo included) needs.
- `learn-from-session` — the reflection flow a delegated subagent runs on
  its own work before returning.
