---
name: architect-review
description: Run iterative architect review passes on a design spec, tracking convergence. Use when a spec is written and needs hardening before implementation — each pass finds fewer issues until the design stabilizes.
allowed-tools: Agent, Read, Edit, Bash, Glob, Grep
---

# Architect Review

Run multiple architect review passes on a design spec, with each pass reading the spec as modified by the previous. Track changes in a changelog file to measure convergence.

## When to use

- After writing or updating a design spec, before moving to implementation planning
- When you want independent review of architectural decisions
- When the user says "review the plan", "harden the spec", "architect review"

**Do NOT use when:**

- The spec is still a rough draft the user is actively editing -- wait until they say it's ready for review
- The document is implementation code, not a design spec -- use code review instead
- The spec is under 1 page -- a single careful read is enough, no iterative passes needed

## How it works

Each pass is a background Opus agent that reads the current spec, makes concrete edits, and reports what it changed. Passes run sequentially — each reads the output of the previous. A changelog file tracks every change across passes — see step 1 for where it lives and how it's named.

The pattern converges: pass 1 finds many issues, pass 2 finds fewer, pass 3 finds edge cases, pass 4 typically finds nothing. Stop when a pass makes 0-2 changes.

## Process

### 1. Pick the changelog location and create it

Two locations, picked by one question: **is the architect review being run on a single existing plan/spec file in a repo?**

- **Yes → beside the plan, as a plain rolling changelog.** Path: `<spec-dir>/<spec-name>-changelog.md`. **No datestamp in the filename, no `architect-review` in the filename** — it's _the_ changelog for this plan. If the user re-runs an architect review later, append a new dated section to the same file (don't create a second file). The changelog travels with the plan in git, gets committed alongside spec edits, and forms the canonical record of how the spec evolved before any PR exists.

- **No → `~/tmp/architect-review/<slug>-<YYYY-MM-DD-HHMM>.md`.** This covers multi-file reviews, scratch input, external repos you don't want to pollute, and anywhere there isn't one obvious "the spec file" to sit next to. The datestamp lives in the _filename_ here because there's no rolling file to append to — multiple reviews need to coexist as separate files. Run `mkdir -p ~/tmp/architect-review` first.

When in doubt, ask the user: "I'm putting the architect review changelog at `<path>` — sound right?" A wrong guess that drops a file into the reviewed repo is more annoying than a one-line check.

**Check whether the file already exists before seeding it.** This is the common case for the beside-spec mode on a re-run — the prior review's changelog is still there.

- **File does not exist** → seed it with this template:

  ```markdown
  # <Spec Title> — Changelog

  Spec: <absolute path to spec>

  ## Architect Review — <YYYY-MM-DD HH:MM>

  ### Convergence Tracking

  | Pass | Changes |
  | ---- | ------- |
  ```

- **File already exists** → do NOT overwrite. Append a new `## Architect Review — <YYYY-MM-DD HH:MM>` section (with its own `### Convergence Tracking` table) at the end of the file. Prior sections stay untouched. This is how multiple reviews accumulate on the same plan.

### 2. Run passes as background agents

Launch each pass using the **Agent tool** with `model: "opus"` and `run_in_background: true`. Wait for each pass to complete before launching the next. Each agent gets this prompt structure:

```
You are a senior software architect doing pass <N> on a design spec.
Previous passes made [X, Y, Z] changes respectively (see the changelog).

Read the changelog at <changelog-path> first to understand what previous passes changed.
Then read the spec at <spec-path> carefully.

Context: <brief description of the system>

Review for:
- [pass 1] Architectural soundness, interface design, missing concerns, unnecessary complexity, consistency
- [pass 2] Anything pass 1 missed or got wrong, contradictions from pass 1 edits, over-engineering
- [pass 3+] Subtle implementation bugs, remaining ambiguity, over-engineering from previous reviewers

Make specific, concrete edits using the Edit tool. Each edit is a "change" — cosmetic rewording does NOT count. If the spec is solid, make zero changes — don't change for the sake of changing.

Report:
1. Numbered list of changes made and why
2. Things reviewed but left unchanged
3. Assessment: is this ready for implementation?
```

### 3. After each pass completes

1. Read the agent's final report and extract the numbered list of changes.
2. **Print the delta summary to the main session before doing anything else.** Numbered list of the concrete changes the pass made, each with a one-line rationale. The user wants to see _what moved_ between passes — not just "pass N done." This is mandatory: if you skip the summary, the user can't tell whether the pass was useful without manually diffing the spec.
3. Append the same numbered changes + rationale to the changelog file (the durable record).
4. Update the convergence table row: `| Pass N | <count> |`.
5. If changes > 2: launch the next pass (background).
6. If changes <= 2: stop — the spec has converged.

### 4. Report convergence

Tell the user the final state and **always** point at the persisted changelog (whichever location you picked in step 1) so they can revisit pass-by-pass diffs later:

```
Architect review complete — 4 passes, converged.
Pass 1: 21 changes | Pass 2: 13 | Pass 3: 5 | Pass 4: 1
Full changelog: <absolute path to the changelog file>
```

If the changelog landed beside the spec (the in-repo iteration case), also remind the user to `git add` it — it's part of the iteration record, not transient noise.

### 5. (Optional) Post the convergence summary to the PR

If the spec lives in a git repo on a branch with an open pull request, post the convergence summary as a PR comment so reviewers see the architect's findings without digging into the orchestrating session. This is **best-effort** — every failure mode here is a silent skip, never an error.

Detect:

```bash
gh pr view --json url,number 2>/dev/null
```

If that returns nothing — no PR for the current branch, `gh` not installed, `gh` not authenticated, not in a git repo — skip this step entirely. The architect review is still successful; the PR comment is a bonus, not a requirement.

If a PR is found, build the comment body (a temp file is easiest):

```markdown
## Architect Review — <YYYY-MM-DD HH:MM>

**Result:** converged in 4 passes (or: stopped at 5 passes, still open: <list>)

| Pass | Changes |
| ---- | ------- |
| 1    | 21      |
| 2    | 13      |
| 3    | 5       |
| 4    | 1       |

**Final assessment:** <ready for implementation / needs more work because …>

Full changelog: `<absolute path>`
```

Then post:

```bash
gh pr comment <number> --body-file <body-path>
```

**Always include the run timestamp in the comment header.** Don't try to update an existing comment in place — surfacing the full review history (one comment per re-run) is more valuable than keeping the comment count low. The timestamp makes it obvious which review is which.

Requires the `gh` CLI installed and authenticated. If either is missing, this whole step is a silent no-op — do not nag the user, do not fail the run.

## Key rules

- **Always run agents in background** — don't block the conversation
- **Sequential, not parallel** — each pass must read the previous pass's edits
- **Opus model** for all passes — architecture review needs the strongest reasoning
- **Changelog is mandatory** — without it, you can't measure convergence
- **Beside the plan as `<spec>-changelog.md` when reviewing one in-repo plan file; `~/tmp/architect-review/<slug>-<datestamp>.md` otherwise** — see step 1
- **Multiple reviews on the same beside-the-spec changelog append as new dated sections — don't make a second file**
- **Check before seeding** — if the beside-spec changelog already exists, append a new dated section; never overwrite
- **PR comment is best-effort** — every failure mode in step 5 is a silent skip; the architect review never fails because `gh` isn't installed
- **Surface deltas after every pass** — the main session must print the numbered change list, not just announce that the pass finished
- **Don't over-run** — 4 passes is typical. Stop at 5 max even if not fully converged. Report what's still open.
- **Read the spec's repo CLAUDE.md first** — understand project conventions before reviewing
- **A "change" is a substantive architectural edit** — fixing a typo or rewording a sentence for style does not count toward the convergence threshold

## Anti-patterns

- Running passes in parallel (each would review stale state)
- Making cosmetic/wordsmith changes (wastes a pass)
- Continuing past convergence (diminishing returns)
- Not creating the changelog (can't track progress)
