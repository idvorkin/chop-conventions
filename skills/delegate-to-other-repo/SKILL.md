---
name: delegate-to-other-repo
description: Use when the user asks to make a change in a different git repo than the current session's cwd, and wants it done without polluting the current conversation with that repo's context. Typical triggers — "also fix X in the blog", "delegate this to the dotfiles repo", cross-repo tasks mentioned alongside current work.
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# Delegate To Other Repo

Parent Claude (you, in the current session) sets up an isolated worktree in
a different target repo, constructs a self-contained brief, and dispatches a
subagent with a fresh context to do the actual work. The subagent reads the
target repo's conventions, opens a PR, and returns a structured final
message. You relay the PR URL and a short summary.

**Core principle:** parent does infrastructure, subagent does content.
Parent never `cd`s — everything uses `git -C <target>`.

**Announce at start:** "I'm using the delegate-to-other-repo skill to set up
a subagent for cross-repo work in `<target>`."

## When to use

- User asks for a change in another repo mid-conversation
  ("also fix the typo in the blog", "while you're at it, bump
  the version in the other service")
- Task is self-contained enough for a subagent to handle end-to-end
- You want to preserve the current session's context — cross-repo
  work would otherwise load another repo's `CLAUDE.md`, file reads,
  and diff into your working memory
- Target repo already exists locally (this skill does NOT clone)

**Do NOT use** when:

- The task needs back-and-forth with the user during the work
- The current repo and the "other" repo are the same
- The user explicitly said "just do it in this session"
- The target isn't under `~/gits/` yet — tell the user to run
  `gh repo clone owner/repo ~/gits/repo` first

## Flow at a glance

```
Parent (you)                             Subagent (fresh context)
──────────────────                       ─────────────────────────
1. Resolve target repo
2. git -C <T> fetch origin
3. Create worktree off
   origin/<default-branch>
4. Build brief (template +
   substitutions)
5. Agent tool dispatch  ─────────────►   1. cd <worktree>
                                         2. Read CLAUDE.md / AGENTS.md
                                         3. Enumerate skills/
                                         4. Do the task
                                         5. Detect fork vs direct
                                         6. Commit, push, PR
                                         7. Reflect on lessons
6. Receive final message ◄───────────    8. Return PR: / Summary: /
7. Relay to user                            (optional) Lessons:
8. Offer lesson follow-up
   (if present)
```

## Phase 1: Resolve the target repo

Follow this checklist in order:

### 1a. Explicit argument

If the user passed a target:

- **Absolute path** (`/home/user/gits/blog`) → use it
- **Relative path** (`../blog`) → resolve against current `pwd`
- **Bare name** (`blog`) → resolve to `~/gits/blog`
- **`owner/repo` slug** → STOP and tell the user:
  > "I don't clone repos. Run `gh repo clone <owner>/<repo> ~/gits/<repo>` first, then retry."

### 1b. Inferred from conversation

If no argument, scan the recent conversation for repo references
("the blog", "chop-conventions", "that other repo") and match them
against `~/gits/` entries.

**Inference is never final.** Always propose the match to the user and
wait for confirmation before dispatching:

> "You mentioned 'the blog' — I'm reading that as `~/gits/idvorkin.github.io`. Proceed?"

If multiple candidates or no match, fall through to 1c.

### 1c. Ask

Run `/bin/ls ~/gits/` and present the list. Let the user pick.

### 1d. Validate the resolved target

All validation runs via `git -C <path>` — never `cd` into the target:

1. Path exists
2. Is a git repo: `git -C "$T" rev-parse --is-inside-work-tree`
3. Has an `origin` remote that resolves:
   `git -C "$T" remote get-url origin`
4. Default branch is resolvable (see Phase 2 recipe)
5. `origin/<default>` is reachable after `git fetch`

**Not required to be clean.** Worktrees off `origin/<default>` are safe
even when the target's working tree is dirty.

## Phase 2: Create the worktree

**DO NOT delegate to `superpowers:using-git-worktrees`.** That skill
branches off current HEAD, auto-runs `npm install` / `cargo build`, and
runs baseline tests — none of which are correct for delegating a change
off a fresh `origin/<default>` that may be a doc-only edit.

Full shell recipe lives at [`worktree-recipe.md`](worktree-recipe.md).
Read that file and follow it verbatim. Key points:

- Uses `git -C "$T"` throughout
- Runs `git remote set-head origin --auto` after fetch to refresh
  stale `refs/remotes/origin/HEAD` (plain `git fetch` does not)
- Resolves default branch via `symbolic-ref` → `gh repo view` fallback
  → literal `main`, with explicit `[ -z "$default_branch" ]` guards
  to avoid a pipe-precedence bug
- Derives a slug from the task description with a reproducible rule
  (lowercase → non-alnum collapsed to `-` → ≤40 chars; empty/non-ASCII
  falls back to `task-<timestamp>`; collisions get `-2`..`-9` suffixes
  then a timestamp). Collision check covers BOTH `refs/heads/` and
  `refs/remotes/origin/` to avoid non-fast-forward push rejection
- Writes `.worktrees/` to `.git/info/exclude` (local-only, untracked,
  branch-independent) — NOT a `.gitignore` commit. This avoids
  mutating any branch's history, works regardless of target's
  current branch, and survives branch-protected defaults
- Creates the worktree at `.worktrees/delegated-<slug>` on branch
  `delegated/<slug>` rooted at `origin/<default>`

### V1 limitation

This skill hardcodes `.worktrees/delegated-<slug>` and does NOT honor a
target repo's CLAUDE.md `worktree.*director` preference.
`using-git-worktrees` does honor that — revisit if it becomes a
problem.

## Phase 3: Construct the brief

The brief is the single most important artifact this skill produces.
It must be fully self-contained — the subagent sees none of the
current conversation.

Template lives at [`brief-template.md`](brief-template.md). Read it,
substitute the slot placeholders, and pass the result as the `prompt`
parameter to the Agent tool.

### Slots to substitute

| Slot                 | Source                                                                                                                                    |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `<TASK>`             | User's task description, lightly edited for clarity. **Never paraphrase destructively** — preserve their wording.                         |
| `<WORKTREE_PATH>`    | Absolute path to the worktree you created in Phase 2                                                                                      |
| `<SESSION_LOG_PATH>` | Current session's jsonl (see Session log resolution below). If unresolvable, omit the entire "Historical context" section from the brief. |
| `<TARGET_REPO_SLUG>` | Parsed from `git -C "$T" remote get-url origin` (e.g. `idvorkin/chop-conventions`) — used by the subagent for fork detection              |

### Session log resolution

```bash
# Claude Code hashes the session's *cwd* at launch, not the repo root.
# If you're running inside a worktree, the hash encodes the worktree
# path, not the main checkout.
cwd_hash=$(pwd | sed 's|/|-|g')
newest=$(/bin/ls -t "$HOME/.claude/projects/$cwd_hash"/*.jsonl 2>/dev/null | head -1)

# Fallback: try the repo toplevel hash.
if [ -z "$newest" ]; then
  toplevel=$(git rev-parse --show-toplevel)
  toplevel_hash=$(echo "$toplevel" | sed 's|/|-|g')
  newest=$(/bin/ls -t "$HOME/.claude/projects/$toplevel_hash"/*.jsonl 2>/dev/null | head -1)
fi
```

If neither path yields a jsonl, warn and omit the historical-context
section. Parallel sessions in the same cwd resolve to "whichever jsonl
was most recently written" — this is an accepted v1 ambiguity since
the log is an escape hatch, not a required input.

## Phase 4: Dispatch the subagent

```
Agent tool:
  description:        "Delegated work in <target-repo>"
  subagent_type:      "general-purpose"
  prompt:             <the substituted brief from Phase 3>
  run_in_background:  false
```

**Foreground by default.** Only pass `run_in_background: true` if the
user explicitly asked ("dispatch in background", "I want to keep
working while this runs"). Foreground means you wait for the result
message before continuing — that's fine.

**Never retry automatically.** If the subagent fails, escalate to the
user (see Phase 5).

## Phase 5: Relay the result and offer follow-ups

The subagent returns a final message with:

1. **`PR:` URL** — on its own line
2. **`Summary:` 3–5 bullets** — what changed and why
3. **`Lessons:` block** (optional) — draft CLAUDE.md insertions the
   subagent thinks are worth capturing

### Happy path

Relay all three sections verbatim to the user. Add a note with the
worktree path and the cleanup command:

> "Worktree preserved at `<path>`. Delete with `git worktree remove <path>` when you're done iterating on it."

### If `Lessons:` is present

Show the block verbatim, then offer two follow-up paths:

1. **Quick path** — "Open a second PR in the same worktree with just
   this CLAUDE.md addition?" If accepted, run the commit and
   `gh pr create` in the existing `.worktrees/delegated-<slug>`
   worktree (still exists, since cleanup is manual). Branch name:
   `delegated/<slug>-lessons`.
2. **Full path** — "Run `/learn-from-session` on the target repo for
   multi-file routing?" For lessons that span multiple CLAUDE.md
   files or need deeper routing.

If the user declines both ("skip it"), that's a **normal terminal
state** — omit the follow-up and consider the delegated run complete.
Rejected lessons are NOT a failure.

### If the subagent's final message doesn't match the contract

Missing `PR:` line → treat as failure. Show the user the subagent's
last message and ask:

- **Retry** with the same brief?
- **Abandon** — delete the worktree and stop?
- **Take over in-session** — you (parent) `cd` into the worktree and
  continue the work manually?

No automatic retry loop.

## Fork workflow detection (reference)

The subagent handles this itself — the brief walks it through the
decision tree. For the full 4-case tree with ASCII diagram and the
`diagnose.py` reuse strategy, see [`fork-detection.md`](fork-detection.md).

TL;DR: the subagent runs `gh auth status`, `git remote -v`, and
(for single-remote cases) `gh repo view <slug> --json isFork,parent`,
then classifies into:

- **Case A** — two remotes (`origin` + `upstream`): fork workflow,
  push to `origin`, PR `--repo <canonical>`
- **Case B** — one remote, canonical origin matching auth:
  direct push, PR with no `--repo`
- **Case C** — one remote, fork-as-origin matching auth
  (**chop-conventions' real pattern**): push to `origin`, PR
  `--repo <parent-from-gh-json>`
- **Case D** — one remote, canonical origin NOT matching auth:
  look for sibling fork remote; if none, STOP

## Integration with learn-from-session

Reflection happens **in the subagent**, not the parent. The parent
has no visibility into what tripped up the subagent during the work
(missing docs, hook reformats, unclear conventions) — only the
subagent lived it. So the subagent runs through `learn-from-session`'s
reflection prompts on its own work, applies the durability filter,
and drafts any surviving lessons inline in its final message.

The parent's job is to relay, not to reflect:

1. Subagent drafts — never commits CLAUDE.md edits to the work PR
2. Parent relays the `Lessons:` block verbatim
3. Parent offers quick-path or full-path follow-ups (see Phase 5)
4. User owns the approval gate

## Hard prohibitions

These apply to both parent and subagent:

- **No `git push --force`** on any branch
- **No `--no-verify`** on commits
- **No commits on any branch of the target's primary checkout** —
  the parent never mutates the target's branches. The only thing
  the parent writes is `.git/info/exclude` (local-only, untracked).
  All subagent work happens on the delegated branch inside the
  worktree and is pushed via normal PR flow.
- **No `rm -rf`** or destructive ops without explicit user confirmation
- **No `gh pr merge`** — opening the PR is the terminal action
- **No committing CLAUDE.md edits derived from lessons reflection**
  to the work PR. Lessons are draft material in the subagent's final
  message only; the user owns the approval gate.
- **No `cd` in the parent.** Parent always uses `git -C <target>`.
  Only the subagent `cd`s, into the worktree, as its first action.

## Failure handling

### Parent-side

| Failure                                                            | Response                                                   |
| ------------------------------------------------------------------ | ---------------------------------------------------------- |
| Target not found / not a git repo                                  | Stop, report, ask user                                     |
| `git fetch origin` fails                                           | Stop, surface error, don't dispatch                        |
| `.git/info/exclude` write fails (permission denied)                | Stop, surface error — should not happen on user-owned repo |
| `git worktree add` fails (path/branch collision, base ref missing) | Stop, surface error                                        |
| Session log unresolvable                                           | Warn, omit historical-context section, continue            |

### Subagent-side

| Failure                                | Response                                       |
| -------------------------------------- | ---------------------------------------------- |
| Final message has no `PR:` line        | Escalate to user (retry / abandon / take over) |
| Subagent exits without a final message | Same — treat as failure                        |
| Rejected lessons                       | NOT a failure — normal terminal state          |

## Common mistakes

### Using `cd <target>` instead of `git -C <target>`

Parent ends up stranded in the target's cwd, polluting shell state for
any post-dispatch commands. Fix: always `git -C "$T"` in the parent.
Only the subagent `cd`s.

### Hardcoding `origin/main`

Breaks on repos with `master` or `trunk` as the default branch. Fix:
resolve the default branch with the fallback chain in `worktree-recipe.md`.

### Skipping `remote set-head --auto` after fetch

Plain `git fetch origin` does NOT refresh `refs/remotes/origin/HEAD`. A
target whose default branch was renamed (e.g. master → main) since
clone yields a stale value from `symbolic-ref`. Fix: always run
`git -C "$T" remote set-head origin --auto` between fetch and the
symbolic-ref lookup.

### Committing `.worktrees/` to a branch's `.gitignore`

Committing on the target's current branch pollutes arbitrary branches
and vanishes on next checkout. Committing on the default branch
requires branch-switching (destructive) and breaks on protected
defaults. Fix: write to `.git/info/exclude` instead — local-only,
untracked, branch-independent, per-repo (shared across worktrees).

### Trusting `diagnose.py`'s `is_fork_workflow: false`

It misclassifies chop-conventions' real pattern (single fork-as-origin
with no upstream). Fix: for the single-remote case, always run
`gh repo view <slug> --json isFork,parent` manually — see
`fork-detection.md`.

### Nested fences in the brief

Embedding ` ``` ` inside the brief's outer ` ```markdown ` fence
terminates the fence early when the brief is stored in SKILL.md. Fix:
the brief uses plain-text formatting for all embedded structure
(decision trees, output contract, `Lessons:` block). See
`brief-template.md` — it's pre-formatted to avoid this.

## Red flags — STOP and reconsider

- You're about to `cd` into the target repo in the parent
- You're about to call `using-git-worktrees` to create the worktree
- You're about to retry a failed subagent dispatch automatically
- You're about to commit a drafted lesson to the work PR
- You're about to dispatch without confirming an inferred target
- The target slug is non-ASCII and you're about to use it unfiltered

## Related

- **REQUIRED SUB-SKILL:** `superpowers:using-git-worktrees` — read it so
  you understand _why_ this skill deliberately does NOT call it
- `learn-from-session` — the reflection flow the subagent runs inside
  itself before returning
- `up-to-date` — its `diagnose.py` helper is what the subagent uses
  as a fork-detection shortcut (Cases A and simple B only)

## Supplementary files

- [`worktree-recipe.md`](worktree-recipe.md) — full shell recipe for
  Phase 2, with safety checks and fallback chains
- [`brief-template.md`](brief-template.md) — the full self-contained
  brief the parent substitutes slots into and passes to Agent
- [`fork-detection.md`](fork-detection.md) — the 4-case decision tree
  with ASCII diagram and `diagnose.py` interaction rules
