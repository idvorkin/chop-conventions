# `/delegate-to-other-repo` Skill — Design

**Status:** Draft · **Date:** 2026-04-12 · **Beads:** chop-conventions-c6a

## Purpose

Let the user ask Claude to make a change in a _different_ repository than the
current session's working repo, with the work happening in an **isolated
subagent context** so the parent session stays focused on its own work. The
subagent operates in a git worktree of the target repo, reads that repo's
`CLAUDE.md` / `AGENTS.md` / skill inventory, does the work, and ends by
opening a pull request back to the canonical remote.

## Motivation

Today, when a user says "hey, also fix a typo in the blog repo while you're
at it," Claude's options are:

1. **Abandon the current repo context**, `cd` away, do the work in-session,
   polluting the conversation with another project's `CLAUDE.md` and
   conventions. Context gets muddled; long tasks eat tokens.
2. **Manually ask the user to switch sessions**, which is jarring and loses
   the conversational thread that motivated the request.

A delegation skill gives a third option: parent Claude stays where it is,
fires off a subagent with a clean, self-contained brief, and surfaces the
result (PR URL + summary) when the subagent returns.

## Non-Goals

- **Not a generic "clone any repo" tool.** Targets must already exist under
  `~/gits/`. If not, we error with a pointer to `gh repo clone`.
- **Not a multi-repo batch runner.** One target per invocation.
- **Not an in-session worker.** If you want same-session cross-repo work,
  use a different (future) skill. This one always dispatches a subagent.
- **Not a finish-phase skill.** There's no two-phase setup/finish split;
  the subagent owns the whole lifecycle through PR creation.

## User Experience

### Invocation (flexible)

```
/delegate-to-other-repo <target> <task description>
/delegate-to-other-repo <task description>
/delegate-to-other-repo
```

- **Form 1:** explicit target and task. Target is a local path or a
  `~/gits/<repo>` shortname.
- **Form 2:** task only; parent infers target from the current conversation
  ("fix that typo in the blog post we just talked about" →
  `~/gits/idvorkin.github.io`).
- **Form 3:** bare invocation; parent asks the user what to delegate, to
  which repo.

### Parent's visible work

1. Resolves the target (may ask for disambiguation)
2. Creates a worktree at `<target>/.worktrees/delegated-<slug>` off
   `origin/main`
3. Dispatches the subagent (foreground by default)
4. Reports back: **PR URL**, branch name, 3–5-bullet summary of changes,
   worktree path (for post-hoc inspection)

## Architecture

### Parent / subagent split

```
┌─────────────────────────────────────────────────────────────┐
│ Parent Claude (current session)                             │
│                                                             │
│  1. Resolve target repo          [skill markdown prompt]   │
│  2. Fetch origin/main in target  [git fetch]               │
│  3. Invoke using-git-worktrees   [skill call, cd'd in]     │
│  4. Construct self-contained brief                         │
│  5. Dispatch Agent tool          ──────────┐               │
│  6. Wait for result                        │               │
│  7. Relay PR URL + summary to user         │               │
└────────────────────────────────────────────┼───────────────┘
                                             │
                      ┌──────────────────────▼───────────────┐
                      │ Subagent (general-purpose, fresh ctx)│
                      │                                      │
                      │  1. cd <worktree>                    │
                      │  2. Read CLAUDE.md, AGENTS.md        │
                      │  3. Enumerate skills/, .claude/skills│
                      │  4. Note test/lint commands          │
                      │  5. Do the task                      │
                      │  6. Commit (hooks, no --no-verify)   │
                      │  7. Detect fork vs direct workflow   │
                      │  8. Push to correct remote           │
                      │  9. gh pr create --repo <canonical>  │
                      │ 10. Return PR URL + summary          │
                      └──────────────────────────────────────┘
```

### Parent responsibilities — infrastructure

- **Target resolution** (prompt-driven; no Python needed)
- **Baseline worktree creation** — delegated to `superpowers:using-git-worktrees`
- **Brief construction** — self-contained, see below
- **Dispatch + result relay** — via the `Agent` tool

### Subagent responsibilities — content

- Read target repo conventions
- Execute the task
- Handle git hygiene (commit, push, PR)
- Return a structured final message (PR URL + summary)

### What lives where — why this split

The parent is the only thing with access to the current conversation. It's
the right place to:

- Extract the user's original task wording
- Resolve "the blog" → `~/gits/idvorkin.github.io` from conversational
  context
- Own the git-worktree infrastructure setup (which `using-git-worktrees`
  already knows how to do)

The subagent gets a clean context — no distracting history, just the brief
and the target repo's files. That's the entire win of this skill; if the
subagent inherited the parent's context we could've just done the work
in-session.

## Brief Format

The brief is the single most important artifact this skill produces.
It must be fully self-contained — the subagent sees none of this
conversation.

### Required sections

```markdown
# Task

<user's words, lightly edited for clarity — do not paraphrase
destructively>

# Working directory

cd <absolute path to worktree> # FIRST action you take

# Target repo conventions

Read, in order (skip any that don't exist):

- CLAUDE.md (root, then any nested)
- AGENTS.md
- justfile, Makefile, package.json (scripts section)
- .github/workflows/\*.yml (names only — so you know what CI will run)

Then enumerate (list contents only, don't read every SKILL.md):

- skills/
- .claude/skills/

# Git workflow

Detect fork vs direct-push workflow:

1. Run `gh auth status` — note the active account
2. Run `git remote -v` — check for both `origin` and `upstream`
3. If `upstream` points to a canonical repo and `origin` points to a fork,
   this is **fork workflow**: push to `origin`, open PR with
   `gh pr create --repo <canonical>`
4. Otherwise, this is **direct-push workflow**: push to `origin`, open PR
   with `gh pr create`

# Final output contract

Your final message MUST contain exactly:

1. **PR URL** on its own line, prefixed with `PR: `
2. **3–5 bullet summary** of what changed and why, prefixed with `Summary:`
3. Nothing else. No preamble, no "I'll now...", no sign-off.

# Hard prohibitions

- No `git push --force` on any branch
- No `--no-verify` on commits (hooks exist for a reason)
- No commits directly to `main`
- No `rm -rf` or destructive ops without explicit confirmation
- No `gh pr merge` — opening the PR is the terminal action

# Historical context (escape hatch)

If — and only if — you get genuinely stuck and need to understand _why_
this task was requested, the originating conversation is at:

<path to parent's session jsonl>

It's a large JSONL file. Use `grep` / `jq` to find specific turns. Do not
read the whole file. Prefer ending with a clarifying question in your
final message over spelunking the log.
```

### Session log resolution

Parent resolves the session jsonl path with:

```bash
toplevel=$(git rev-parse --show-toplevel)
hash=$(echo "$toplevel" | sed 's|/|-|g')
newest=$(/bin/ls -t "$HOME/.claude/projects/$hash"/*.jsonl 2>/dev/null | head -1)
```

Caveat: "newest jsonl" is only reliably the current session when there's
one active Claude session per repo. If the user runs parallel sessions in
the same repo, this may resolve to the wrong log. Acceptable for v1 — the
log is an escape hatch, not a required input.

## Target Resolution Algorithm

Parent follows this order (prompt-driven — it's a checklist in `SKILL.md`,
not code):

1. **Explicit arg.**
   - Absolute path → use it
   - Relative path → resolve against `pwd`
   - Bare name (e.g. `blog`) → resolve to `~/gits/blog`
   - `owner/repo` → error: "clone first with `gh repo clone owner/repo ~/gits/repo`"
2. **Inferred from conversation.**
   - Scan recent turns for phrases like "the blog", "chop-conventions",
     "that other repo" and match against `~/gits/` entries
   - If exactly one match → use it, tell the user
   - If multiple or zero matches → fall through to step 3
3. **Ask.**
   - `/bin/ls ~/gits/` and present candidates; user picks

**Validation after resolution:**

- Path exists
- Is a git repo (`git -C <path> rev-parse --is-inside-work-tree`)
- NOT required to be clean — worktrees off `origin/main` are safe even
  when the parent working tree is dirty

## Worktree Creation

Delegated to `superpowers:using-git-worktrees`:

- Directory: `.worktrees/delegated-<slug>` (the skill's default)
- Branch: `delegated/<slug>` where `<slug>` is derived from the task
  description (kebab-case, truncated to 40 chars)
- Base: `origin/main` (explicit, not current branch) — always fresh
- Pre-check: `using-git-worktrees` verifies `.worktrees/` is gitignored;
  if not, it fixes and commits per its own rules

## Dispatch

```
Agent tool:
  subagent_type: "general-purpose"
  description: "Delegated work in <target-repo>"
  prompt: <the brief constructed above>
  run_in_background: false  (default; true only if user asked)
```

Parent waits for the subagent's result message. On foreground dispatch,
the parent can't do parallel work while waiting — that's fine for v1.

## Failure Handling

### Parent-side failures

- **Target not found / not a git repo** → stop, report, ask user
- **Worktree creation fails** → stop, report the `using-git-worktrees`
  error, don't dispatch
- **Session log unresolvable** → warn, proceed without the escape-hatch
  reference; don't block dispatch

### Subagent-side failures

If the subagent's final message doesn't match the output contract (no
`PR:` line), parent treats it as a failure. Parent surfaces the
subagent's last message and asks the user:

- **Retry** with same brief?
- **Abandon** (delete the worktree)?
- **Take over in-session** (parent `cd`s into the worktree and continues
  the work manually)?

No automatic retry loop. If the subagent failed, retrying the same brief
probably just fails again — user input is the right escalation.

## Cleanup

Subagent does **not** delete its worktree on success. Reasons:

- User may want to inspect the changes before the PR merges
- User may want to iterate (amend, add commits) without re-running the skill
- Worktree deletion is trivially `git worktree remove <path>` — not worth
  automating

Parent's final report includes the worktree path and this command for
when the user wants to clean up.

## Files

- `skills/delegate-to-other-repo/SKILL.md` — the skill (pure markdown)

No Python, no tests. Target resolution and brief construction are prompt
work, not code work. This mirrors how `learn-from-session` is structured
(pure markdown, no helpers) rather than `up-to-date` (which has a helper
because it parallelizes subprocess calls and needs unit-testable
classification logic).

## Installation

After the skill file lands:

```bash
ln -s /home/developer/gits/chop-conventions/skills/delegate-to-other-repo \
      ~/.claude/skills/delegate-to-other-repo
```

Documented in `README.md` skills table.

## Open Questions

None load-bearing. Defaults chosen:

- **Branch naming:** `delegated/<slug>` — traceable back to the skill
- **Worktree cleanup:** manual, via reported command
- **No-CLAUDE.md target:** subagent proceeds, flags the absence in final summary
- **Background dispatch:** only if user explicitly asks
- **URL cloning:** not supported in v1; errors with `gh repo clone` hint
- **Parallel session log ambiguity:** accepted v1 limitation, documented

## Success Criteria

The skill is working when:

1. A user can say `/delegate-to-other-repo fix the typo on the homepage of
the blog` and end up with a clickable PR URL without touching another
   terminal
2. The parent session's context after delegation contains only the PR URL
   and summary — not the target repo's `CLAUDE.md`, file reads, or diff
3. If the subagent fails, the parent surfaces actionable error info and
   preserves the worktree for takeover
4. The skill composes cleanly with `superpowers:using-git-worktrees`
   rather than reimplementing worktree logic
