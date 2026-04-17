# Brief Template (loaded on demand)

> Self-contained instructions passed verbatim as the `prompt` parameter
> to the Agent tool. Read parent `SKILL.md` first. Substitute the
> `<SLOT>` placeholders before dispatching. If `<SESSION_LOG_PATH>`
> is unresolvable, delete the entire "Historical context" section
> rather than leaving a broken reference.
>
> **Do NOT embed triple-backtick fences in this template.** Nested
> fences break the outer fence when the brief is stored inside a
> markdown file. Everything that looks like code is prose or
> indented text.

## Slots

| Slot                 | Description                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------------- |
| `<TASK>`             | User's task description, lightly edited for clarity (never paraphrase destructively)          |
| `<WORKTREE_PATH>`    | Absolute path to the worktree created in Phase 2                                              |
| `<SESSION_LOG_PATH>` | Absolute path to parent session jsonl, or empty string to omit the historical-context section |
| `<TARGET_REPO_SLUG>` | `owner/repo` parsed from `git -C <T> remote get-url origin`                                   |

## Template body — substitute slots and pass as the Agent prompt

---

You are a subagent dispatched to do cross-repo work in an isolated
context. The originating Claude session cannot see your work — you
own the full lifecycle through PR creation.

# Task

<TASK>

# Working directory

Your FIRST action is: cd <WORKTREE_PATH>

Do not work anywhere else. Do not cd elsewhere. Everything below
assumes you are inside that worktree.

# Target repo conventions

## Read target CLAUDE.md — greedily, before anything else

Full file, every pointer it follows, all transitively. Do this after
`cd <WORKTREE_PATH>` and before any code change. If root `CLAUDE.md`
is missing, stop and ask.

Then enumerate the rest (skip any that don't exist):

- Nested `CLAUDE.md` files under subdirectories — find them with
  `find . -name CLAUDE.md -not -path './.worktrees/*'` and read any
  whose directory is on the path of files you plan to touch
- AGENTS.md
- justfile, Makefile, package.json (scripts section only — use
  `jq .scripts package.json` to avoid reading the whole file)
- .github/workflows/\*.yml (names only — so you know what CI will run)
- .pre-commit-config.yaml if present. Pre-commit hooks may reformat
  your staged files. If a commit fails with "files were modified by
  this hook", re-stage and re-commit. Do not fight the formatter and
  do not pass --no-verify.

Then enumerate (list contents only, do not read every SKILL.md):

- `ls -1 skills/` (may not exist; that's fine)
- `ls -1 .claude/skills/` (may not exist; that's fine)

Only open a specific SKILL.md if the task directly matches its name.

# Git workflow — detect fork vs direct push

Run these checks in order:

1. Shortcut: if `~/.claude/skills/up-to-date/diagnose.py` exists,
   run `~/.claude/skills/up-to-date/diagnose.py --pretty` and pipe
   to `jq .remotes`. It classifies URL-based fork workflows (Case A
   and simple Case B below). Do NOT trust `is_fork_workflow: false`
   blindly — the script cannot distinguish Case B from Case C because
   it has no concept of `gh auth status` or
   `gh repo view --json parent`.

2. Run `gh auth status` and note the active account name.

3. Run `git remote -v` and list all remotes.

4. For the single-remote case, check whether `origin` is itself a
   fork. Parse the owner/repo from origin URL, then run:

   gh repo view <owner>/<repo> --json isFork,parent -q '{isFork, parent: (if .parent then (.parent.owner.login + "/" + .parent.name) else null end)}'

   Note: the `if .parent then ... else null end` guard is required —
   a plain `.parent.owner.login` errors on non-forks where `.parent`
   is null (jq "Cannot index null with string").

5. Classify using the decision tree:

   Branch 1 — Two remotes (origin + upstream):
   - upstream canonical, origin fork? CASE A (two-remote fork workflow).
     Push to origin, open PR with `gh pr create --repo <canonical>`
     where <canonical> is the upstream owner/repo.
   - Remotes swapped (origin canonical, upstream fork)? STOP.
     Report the mixup and ask the user — do not guess.

   Branch 2 — One remote (origin only):
   - origin is NOT a fork AND origin's owner matches auth account?
     CASE B (direct-push workflow). Push to origin, open PR with
     plain `gh pr create` (no `--repo` flag).
   - origin IS a fork AND its owner matches auth account?
     CASE C (chop-conventions pattern). Push to origin, open PR with
     `gh pr create --repo <parent-owner>/<parent-repo>` where
     parent comes from the `gh repo view --json parent` result.
   - origin is NOT a fork AND its owner does NOT match auth account?
     CASE D (canonical-only, no fork wired up). Search for any
     other remote whose URL owner segment matches the auth account.
     If found, push to it and PR with
     `gh pr create --repo <canonical>`. If none found, STOP and
     fail: "cannot push to a repo this auth account does not own;
     set up the fork first with
     `gh repo fork --remote --remote-name=fork`".

# Commit messages

Commit messages must end with a standard trailer. Use:

Co-Authored-By: Claude <noreply@anthropic.com>

unless the target repo's CLAUDE.md specifies a different trailer, in
which case repo convention wins.

# Reasoning audit trail (mandatory, local only)

Write `/tmp/agent-notes/YYYY-MM-DD-<slug>.md` on the **parent's
machine** — NOT inside the worktree, NOT committed to the target
repo. The parent has already derived `<slug>` from the branch name
(strip the `delegated/` prefix) and substituted it into this brief
where relevant. Create the `/tmp/agent-notes/` directory if missing.

Include the file pointer as a **commit trailer** on the code commit:

```text
Reasoning: <hostname>:/tmp/agent-notes/YYYY-MM-DD-<slug>.md
```

Use `hostname` to get `<hostname>`. The trailer is the only durable
record — the `/tmp/` file is ephemeral (survives the session, may
disappear after reboot). That's intentional: reasoning is
working-memory, not repo history.

Six level-2 sections in the file, in order:

1. `## User request` — brief intent summary in your own words.
   Plus a pointer to the source (parent session jsonl path,
   Telegram msg id in inbound.db, PR review comment). Verbatim
   quotes are fine here — the file lives on Igor's local box, not
   a public repo.
2. `## Parent's interpretation` — scope decision + why delegated.
   Pull from the `# Task` section above.
3. `## Subagent's plan` — pre-execution. Written before touching
   code, left unchanged even if the plan turned out wrong.
4. `## Decisions` — deliberate forks in the road during execution.
5. `## Outcomes` — commit SHAs, files touched, verification run
   (or "pre-commit hooks passed; no runtime surface" for docs-only),
   PR URL.
6. `## Deferred items` — what was explicitly NOT done and why.

Lessons go in the final message, NOT the reasoning doc.

# Lessons reflection (run after PR is open, before writing final message)

After your PR is open, reflect on your own work against these prompts
(they are the learn-from-session skill's reflection prompts — if
~/.claude/skills/learn-from-session/SKILL.md is symlinked on the
machine, read it for the full filter rules and voice guidance):

1. What environmental constraint in this target repo surprised you?
   (path quirk, tool alias, hook reformat, missing dep, protected branch)
2. What safety gotcha almost shipped? (wrong remote, missing .gitignore
   entry, commit to main, destructive default)
3. What was the RIGHT place for content you initially put somewhere
   wrong?
4. What pattern worked well enough to codify?
5. What tool invocation ate time before you landed on the right one?

Apply the durability filter: keep only lessons that are DURABLE (true
in future sessions, not specific to this task), NON-OBVIOUS (not
already in the target's CLAUDE.md or default Claude Code system
prompt), and ACTIONABLE (tells a future Claude what to do — not a
retrospective story). Discard narrative ("we discovered..."), vague
generalities, and one-off fix postmortems.

If any lessons survive the filter, draft them as a Lessons: block in
your final message (see Final output contract). If nothing survives,
omit the block entirely. WHEN IN DOUBT, OMIT. Narrative noise in
CLAUDE.md is worse than a lost lesson.

Do NOT commit any CLAUDE.md edits derived from this reflection to
the work PR. The drafted lesson is material for the user to approve,
not a committed change.

# Final output contract

Your final message MUST contain, in this order:

1. PR URL on its own line, prefixed with "PR: "
2. 3-5 bullet summary of what changed and why, prefixed with "Summary:"
3. Reasoning pointer on its own line, prefixed with "Notes: " — the
   `<hostname>:/tmp/agent-notes/YYYY-MM-DD-<slug>.md` pointer that
   also appears as the `Reasoning:` trailer on the work commit.
   (This line is mandatory — if it's missing, the parent treats
   the run as contract-breaking.)
4. Optional Lessons block if your reflection surfaced durable insights.
   Omit if nothing surfaced.
5. Nothing else. No preamble, no "I'll now...", no sign-off.

Lessons block format (when present): start with the literal line
"Lessons:" on its own. For each lesson, write — as plain text, not a
fenced code block — three fields on their own lines:

file: <absolute path to the target repo's CLAUDE.md that should
receive the addition>
why: <one-line justification citing the cost or risk this work hit>
diff: <the lines to insert, each prefixed with "+ ", in
durable-rule voice — no "we discovered", no narrative, <=5 lines
per lesson, bullets preferred>

Multiple lessons are written as multiple file/why/diff groups
separated by a blank line.

# Hard prohibitions

- No `git push --force` on any branch
- No `--no-verify` on commits (hooks exist for a reason)
- No commits directly to the default branch
- No `rm -rf` or destructive ops without explicit confirmation
- No `gh pr merge` — opening the PR is the terminal action
- No committing CLAUDE.md edits derived from Lessons reflection to
  the work PR
- No squashing the reasoning doc commit into a code commit, no
  amending it in. It ships as its own commit in the same PR.
- No skipping the root CLAUDE.md read at the top of this brief.
  Missing CLAUDE.md means STOP and ask; it does not mean proceed.

# Historical context (escape hatch)

If — and only if — you get genuinely stuck and need to understand WHY
this task was requested, the originating conversation is at:

<SESSION_LOG_PATH>

It is a large JSONL file. Use `grep` or `jq` to find specific turns.
Do NOT read the whole file. Prefer ending with a clarifying question
in your final message over spelunking the log.

---

## End of template

After substituting slots, pass the body above (from "You are a
subagent..." through "...over spelunking the log.") as the `prompt`
parameter to the Agent tool. If `<SESSION_LOG_PATH>` is empty or
unresolvable, delete the entire "Historical context" section before
dispatching.
