---
name: learn-from-session
description: Extract durable lessons from a completed Claude session and codify them in the right CLAUDE.md files or skills. Use at the end of a long session, after a bug hunt that surfaced a non-obvious constraint, or when the user asks "what can we learn from this session". Discovers CLAUDE.md files dynamically, routes lessons by generic scope (project / shared conventions / environment / machine-local), enforces neutral voice, and asks for approval before editing.
allowed-tools: Bash, Read, Edit, Glob, Grep
---

# Learn From Session

Extract durable lessons from the current session and apply them to the right CLAUDE.md file(s) or skills. Works on any machine with any repo layout — discovers CLAUDE.md files dynamically, routes by scope category not by repo name.

## When to use

- User says "what can we learn from this session", "apply to CLAUDE.md", or similar
- End of a long session where multiple corrections piled up
- After a bug hunt that uncovered a non-obvious environmental constraint
- After shipping a new pattern worth standardizing (boot hook, exclude list, dep check recipe)

**Do NOT use** when the session was routine. Not every session produces CLAUDE.md-worthy material. If the reflection step yields nothing durable, say so and stop.

## The Iron Rule: Brevity

CLAUDE.md files are loaded into every prompt on every turn. Every line costs tokens on every future call. Additions must earn their cost.

Target: **≤5 lines per addition**, bullets preferred. One concept per line. Skills can be longer since they're loaded on demand, but CLAUDE.md is a hard cap.

## Step 0: Discover CLAUDE.md Files

Find every CLAUDE.md and `.claude.local.md` that could be a target. Start from repos actually touched in this session, then broaden.

```bash
# Active working dirs from this session (infer from git contexts you've used)
# Then find CLAUDE.md / .claude.local.md under each + under $HOME
find <repo-1> <repo-2> "$HOME" -maxdepth 4 \
    \( -name CLAUDE.md -o -name .claude.local.md \) 2>/dev/null
```

For each file found, **Read its opening section** to understand what scope it covers. Do not infer scope from the path — names and layouts differ across machines. A file named `settings/CLAUDE.md` might be dotfiles on one machine and a project's user-facing settings docs on another.

## Step 1: Reflect

Walk back through the session and answer these prompts explicitly. Each should have either a concrete answer or "none in this session":

1. **What environmental constraint surprised us?** (host OS quirk, container limitation, read-only filesystem, PID 1 weirdness, tool shadowed by an alias, binary at an unexpected path)
2. **What safety gotcha almost shipped?** (lifeline process missing from an exclude list, race condition, signal handler timing, destructive default, missing dep check)
3. **What was the _right_ place for content we initially put in the _wrong_ place?** (file choice, module boundary, doc location, skill boundary)
4. **What pattern worked well enough to codify?** (idempotent boot hook, dep check before main loop, smoke test with low thresholds, rebase-before-PR)
5. **What tool invocation ate time before landing on the right one?** (wrong flag, shadowed binary, pattern that matched unintended targets, subshell trap timing)
6. **Were ≥3 similar sequential tool calls fired in a row?** (e.g. repeated `gh pr view`, `bd show`, small-file `Read`s across a list, `up-to-date` diagnose across N repos) — if yes, propose the `bulk` skill's matching `bulk-*` CLI, or a new `bulk-*` entry if none fits. One tool call firing N parallel sub-calls beats N sequential calls on wall-clock almost every time, and it keeps main-thread context cleaner.

If fewer than two prompts have real answers, the session likely doesn't need CLAUDE.md updates. Tell the user and stop.

## Step 2: Filter by Durability

Keep only lessons that are:

- **Durable** — will be true in future sessions, not specific to this bug or commit
- **Non-obvious** — not already in the default Claude Code system prompt or trivially discoverable
- **Actionable** — tells a future Claude what to do or not do, not just a retrospective story

Discard:

- One-off fix narratives ("in this session we fixed X by doing Y")
- Anything already in the repo's CLAUDE.md, the default system prompt, or shared guardrails
- Warnings about behavior that a self-aware Claude would discover in one tool call
- Things that would be true of any Linux/Mac box — CLAUDE.md captures what's _special_ about this environment

## Step 3: Route Each Lesson to a Scope

Use **generic scope categories**, most-specific-wins. Do not route by repo name — route by what kind of rule the lesson is.

1. **Project-local** — specific to the repo you're actively working in (its layout, conventions, PR flow, domain rules). Goes in that repo's `CLAUDE.md`.
2. **Shared conventions** — general coding / skill / PR / safety rules that apply across multiple projects. Goes in whichever repo holds cross-project conventions (varies by environment — could be a `conventions/`, `shared/`, or similar repo).
3. **Environment / machine / shell** — OS quirks, tool aliases, boot mechanisms, path surprises, shell patterns. Goes in the repo that owns dotfiles / machine setup for this environment.
4. **Truly personal or machine-only** — applies to one machine only and should not be committed. Goes in `.claude.local.md` (must be gitignored).

If a lesson spans scopes, pick the most specific. If you're unsure which file owns a lesson, show the user both options and ask.

## Step 4: Draft Concise Additions

For each surviving lesson, write the addition in this shape:

````
### Update: <absolute path to CLAUDE.md>

**Why:** <one-line justification citing this session's cost or risk>

​```diff
+ <the addition — ≤5 lines, prefer bullets, no preamble>
​```
````

### Style rules

- **No narrative.** Never say "in this session..." — write as a durable rule, not as a report.
- **No preamble.** Jump straight to the fact or the instruction.
- **Use code fences** for commands and file paths so they're skimmable.
- **Bold only the one key term** a reader would grep for. Not the whole bullet.
- **Avoid duplication.** If a nearby section already touches the topic, extend it instead of adding a new section.

## Step 5: Consider Skill Changes

Not every lesson belongs in CLAUDE.md. Some lessons are better expressed as:

- **An update to an existing skill** — if the lesson is a new check, constraint, or step that a skill should enforce (e.g., "the doctor skill should verify X"), propose editing that skill's `SKILL.md` instead of CLAUDE.md.
- **A new skill** — if the lesson is a repeatable workflow or recipe that doesn't fit any existing skill, propose a new skill directory with its own `SKILL.md`. Example triggers: a pattern the session re-used three times, a bespoke multi-step recipe, a safety check that should become callable.

Surface these proposals in the Step 6 diff batch alongside CLAUDE.md changes so the user can approve everything together. Skills can be longer than CLAUDE.md entries since they're loaded on demand, but still follow the voice rules — skills are also directives to a future Claude.

## Step 6: Present and Approve

Show all proposed changes in one message, grouped by target file. Include CLAUDE.md diffs, skill updates, and new-skill proposals side by side. End with a single explicit ask: "Apply these?" Wait for explicit approval before editing any file. Do not partial-apply unless the user picks specific items.

## Step 6.5: Cross-repo routing check

**Before writing any edit**, check per target file: does it live in the current session's repo?

```bash
# Session repo toplevel
SESSION_REPO=$(git rev-parse --show-toplevel 2>/dev/null)

# Target repo toplevel (run from the directory containing the target CLAUDE.md)
TARGET_REPO=$(git -C "$(dirname <target-path>)" rev-parse --show-toplevel 2>/dev/null)
```

Route each target:

- **Same repo** (`SESSION_REPO == TARGET_REPO`) → apply in-session as Step 7 describes. No prompt needed.
- **No git context** (e.g. `~/.claude/CLAUDE.md` with `TARGET_REPO` empty) → machine-local, apply in-place. No prompt needed.
- **Different repo** (`SESSION_REPO != TARGET_REPO`, both non-empty) → **STOP and ask the user**. Do not edit. Do not auto-delegate.

The most common different-repo case is a lesson routing to `chop-conventions`' shared fragments (`claude-md/global.md`, `claude-md/machine.md`, `claude-md/dev-machine.md`, `machines/*.md`). Symlinks under `~/.claude/skills/` and `~/.claude/claude-md/` resolve back into `chop-conventions`, so a lesson about a skill's behavior lands here too — the toplevel check catches this correctly as long as you resolve symlinks first (`realpath <target>` before the `git -C` call).

### The cross-repo prompt

For each different-repo target, show this prompt verbatim and wait for the user's choice:

> This lesson would edit `<resolved-path>` in `<other-repo>` (outside the current session's repo). Options:
>
> 1. **Delegate** — hand this edit to the `delegate-to-other-repo` skill, which creates an isolated worktree in the target repo and dispatches a subagent to open a PR. The current session stays unpolluted.
> 2. **Abort** — skip this lesson. You can capture it manually later.
>
> Which?

On **delegate**: invoke the `delegate-to-other-repo` skill with a pre-built task description containing (a) the absolute target path, (b) the exact diff / insertion from Step 4, and (c) the one-line justification from Step 4. Do not attempt to edit the file from this session — the subagent owns it. Collect the returned PR URL for the final report.

On **abort**: drop that lesson from the batch. Record it in your final summary as "skipped — cross-repo, user aborted" so nothing silently disappears.

Never auto-delegate. The prompt is load-bearing — `delegate-to-other-repo` opens PRs, and a user who's only reviewing lessons should not have unexpected PRs appear.

## Step 7: Apply

On approval and after Step 6.5 has been resolved for every target:

0. Same-repo and machine-local targets proceed here. Cross-repo targets have already been handled by `delegate-to-other-repo` (or dropped), so skip them in this phase — do not try to edit them inline.
1. **Create a feature branch per repo** — naming: `claude-md-<short-topic>` or `session-learnings-<date>`
2. **Apply edits** using the Edit tool (not Write — these are targeted insertions)
3. **Commit per repo** with a descriptive message that cites the lesson itself, not "update CLAUDE.md". Include the standard `Co-Authored-By` trailer.
4. **Push** and **create a PR** if the repo uses a PR workflow — check `gh auth status` and `git remote -v` first to detect fork vs direct-push flow. Otherwise commit locally and inform the user.
5. **Report** PR URLs with `/files` appended so the user can skim diffs directly.

If the user says "just commit locally, no PR" or "just do it on main", follow that instead — but ask if unclear.

## Anti-patterns

Red flags that you're adding noise to CLAUDE.md:

- **Narrative phrasing** — "We learned that X...", "In this session we discovered...", "Remember that..."
- **Vague generalities** — "Always test before deploying", "Be careful with X"
- **Already-covered territory** — guardrail rules already in the shared conventions repo, or things already in the default Claude Code system prompt ("prefer editing existing files", "use feature branches")
- **One-off bug postmortems** — the bug is fixed; the narrative belongs in the commit message, not CLAUDE.md
- **Entries longer than 5 lines** without a strong reason
- **Verbatim code blocks longer than ~10 lines** — link to the real file instead
- **Trivial discoverables** — `ls`, `pwd`, `git status`, anything one tool call could surface
- **Machine-only paths or secrets** — these belong in `.claude.local.md`, not the shared `CLAUDE.md`

## Related

- `claude-md-management:revise-claude-md` — upstream generic version; this skill adds explicit scope routing, voice rules, and skill-update proposals on top
- `claude-md-management:claude-md-improver` — audits existing CLAUDE.md quality rather than adding new content
- `delegate-to-other-repo` — invoked from Step 6.5 when a lesson routes to a CLAUDE.md outside the current session's repo (most often `chop-conventions`' shared fragments)
