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

1. Resolves the target (may ask for disambiguation, **always confirms
   before dispatch** when target was inferred rather than explicit)
2. Runs `git -C <target> fetch origin` (no `cd` — the parent stays in its
   own working directory so its shell state is not polluted)
3. Creates a worktree at `<target>/.worktrees/delegated-<slug>` based on
   `origin/<default-branch>` (see Worktree Creation for the exact commands —
   this deviates from `using-git-worktrees`' default of branching off HEAD)
4. Dispatches the subagent (foreground by default). The subagent is the
   only actor that `cd`s — into the worktree, as its first action.
5. Reports back: **PR URL**, branch name, 3–5-bullet summary of changes,
   worktree path (for post-hoc inspection)

## Architecture

### Parent / subagent split

```
┌─────────────────────────────────────────────────────────────┐
│ Parent Claude (current session)                             │
│                                                             │
│  1. Resolve target repo          [skill markdown prompt]   │
│  2. git -C <target> fetch origin (parent does NOT cd)      │
│  3. Verify .worktrees/ ignored; create worktree off        │
│     origin/<default> (explicit commands —                  │
│     do NOT delegate to using-git-worktrees; see below)     │
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
- **Worktree creation** — direct `git worktree add` commands (see Worktree
  Creation). We do **not** call `using-git-worktrees` here because that
  skill branches off current HEAD, auto-runs `npm install` / `cargo build`,
  and runs a baseline test pass — none of which we want for a delegated
  change off `origin/main` that may be a pure doc edit.
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
- Own the git-worktree infrastructure setup

The subagent gets a clean context — no distracting history, just the brief
and the target repo's files. That's the entire win of this skill; if the
subagent inherited the parent's context we could've just done the work
in-session.

**Security note:** The escape-hatch session-log pointer (see Brief Format)
hands the subagent read access to the parent's entire conversation jsonl,
which may contain secrets, tokens, or private file contents pasted in by
the user. The subagent inherits full tool access in its fresh context and
could read that file. v1 accepts this risk because (a) the subagent is
still the user's own Claude session, and (b) the instruction is "only if
genuinely stuck; prefer a clarifying question." If a repo is sensitive
enough that this matters, the user should not delegate to it via this
skill.

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

- CLAUDE.md (root, then any nested — use `find . -name CLAUDE.md -not -path './.worktrees/*'`)
- AGENTS.md
- justfile, Makefile, package.json (scripts section only — `jq .scripts package.json`)
- .github/workflows/\*.yml (names only — so you know what CI will run)
- .pre-commit-config.yaml if present (hooks may reformat your staged files;
  if a commit fails with "files were modified by this hook", re-stage and
  re-commit — do not fight the formatter)

Then enumerate (list contents only, don't read every SKILL.md) by running
`ls -1 skills/` and `ls -1 .claude/skills/` (either may not exist; that's
fine). Only read a specific `SKILL.md` if the task directly matches its
name.

# Git workflow

Detect fork vs direct-push workflow. **Shortcut: if
`~/.claude/skills/up-to-date/diagnose.py` exists, run it first** — it
handles `parse_remotes` / `is_fork_url` / `classify_remotes` and prints
JSON. Invoke as `~/.claude/skills/up-to-date/diagnose.py --pretty` from
inside the worktree (no `--path` arg; the script reads cwd's git context),
then `jq .remotes` to extract the relevant block.

Interpretation of the JSON:

- `remotes.is_fork_workflow == true` AND a remote named `upstream` exists
  → Two-remote fork workflow (case A below).
- `remotes.is_fork_workflow == false` with a single `origin` → you still
  must run the single-remote check below. `diagnose.py` classifies purely
  by URL owner against a hardcoded `FORK_ORGS` list and has no concept of
  `gh auth status` or `gh repo view --json parent`, so it cannot tell the
  difference between Case B (canonical-only origin matching auth) and
  Case C (origin is itself a fork that needs PRs to its parent — the
  chop-conventions pattern).

Then run these checks manually regardless of script output:

1. `gh auth status` — note the active account (e.g. `idvorkin-ai-tools`)
2. `git remote -v` — inspect `origin` and any `upstream`
3. For the single-remote case, check whether the repo `origin` points to is
   itself a fork: `gh repo view <owner/repo from origin URL> --json isFork,parent -q '{isFork, parent: (.parent.owner.login + "/" + .parent.name)}'`.
   If `isFork` is true, the canonical repo is `parent`. If false, there is
   no upstream and the canonical IS `origin`.
4. Classify using the decision tree below:
   - **Branch on remote count.** If two remotes (`origin` + `upstream`):
     check whether `upstream` URL is canonical and `origin` URL is the
     fork. If yes → **Case A: two-remote fork workflow** (push origin,
     PR --repo canonical). If swapped (origin canonical, upstream fork)
     → STOP and report; do not guess.
   - If one remote (`origin` only): use the `gh repo view` result from
     step 3 above plus the auth-account check.
     - If `origin` is NOT a fork AND the org segment of `origin`'s URL
       matches the auth account → **Case B: direct-push workflow**
       (push origin, PR with no `--repo` flag).
     - If `origin` IS a fork AND its owner matches the auth account →
       **Case C (chop-conventions pattern): single fork-origin, push
       direct, PR to parent.** Push to `origin`, then
       `gh pr create --repo <parent-owner>/<parent-repo>`.
     - If `origin` is NOT a fork AND its owner does NOT match the auth
       account → **Case D: canonical-only origin, no fork wired up.**
       Look for any other remote whose URL owner segment matches the
       auth account; if found, push to it and `gh pr create --repo <canonical>`.
       If none found → STOP and fail: "cannot push to a repo this auth
       account does not own; set up the fork first with
       `gh repo fork --remote --remote-name=fork`".
   - **Case A details:** push branch to the fork remote, open PR with
     `gh pr create --repo <canonical-owner>/<repo>`.
   - **Case B details:** active account owns `origin` and there is no
     `upstream`. Push to `origin`, PR with `gh pr create` (no `--repo`).
   - **Case C details (the chop-conventions pattern):** only `origin`
     exists, it is `idvorkin-ai-tools/chop-conventions` (a fork), auth
     account is also `idvorkin-ai-tools`, and PRs go to the parent
     `idvorkin/chop-conventions`. There is NO separate `upstream` remote.
     The `gh repo view --json parent` lookup is what tells you the
     canonical slug to pass to `--repo`.

5. Commit messages must end with the standard trailer:
   `Co-Authored-By: Claude <noreply@anthropic.com>` (or whatever trailer
   the target repo's CLAUDE.md specifies — repo convention wins).

# Lessons reflection (run before writing your final message)

After the PR is open, before you write your final message, reflect on
your own work against these prompts (these are the `learn-from-session`
skill's reflection prompts — if
`~/.claude/skills/learn-from-session/SKILL.md` is symlinked on the
machine, read it for the full filter rules and voice guidance):

1. What environmental constraint in this target repo surprised you?
   (path quirk, tool alias, hook reformat, missing dep, protected branch)
2. What safety gotcha almost shipped? (wrong remote, missing `.gitignore`
   entry, commit to `main`, destructive default)
3. What was the _right_ place for content you initially put somewhere
   wrong?
4. What pattern worked well enough to codify?
5. What tool invocation ate time before you landed on the right one?

Apply the durability filter: keep only lessons that are **durable**
(true in future sessions, not specific to this task), **non-obvious**
(not already in the target's `CLAUDE.md` or the default Claude Code
system prompt), and **actionable** (tells a future Claude what to do —
not a retrospective story). Discard narrative ("we discovered..."),
vague generalities, and one-off fix postmortems.

If any lessons survive the filter, draft them as a `Lessons:` block in
your final message (see Final output contract for the format). If
nothing survives, omit the block entirely. **When in doubt, omit** —
narrative noise in `CLAUDE.md` is worse than a lost lesson.

Do NOT commit any `CLAUDE.md` edits derived from this reflection to
the work PR. The drafted lesson is _material for the user to approve_,
not a committed change.

# Final output contract

Your final message MUST contain, in this order:

1. **PR URL** on its own line, prefixed with `PR: `
2. **3–5 bullet summary** of what changed and why, prefixed with
   `Summary:`
3. **(Optional) Lessons block** — include only if your reflection
   surfaced durable insights. Omit if nothing surfaced.
4. Nothing else. No preamble, no "I'll now...", no sign-off.

**Lessons block format (when present):** start with the literal line
`Lessons:` on its own. For each lesson, write — as plain text, not a
fenced code block, because nested fences would break this brief when
it is embedded in `SKILL.md` — three fields on their own lines:

- `file:` absolute path to the target repo's `CLAUDE.md` that should
  receive the addition
- `why:` one-line justification citing the cost or risk this work hit
- `diff:` the lines to insert, each prefixed with `+ `, in
  durable-rule voice (no "we discovered", no narrative, ≤5 lines per
  lesson, bullets preferred)

Multiple lessons are written as multiple `file:`/`why:`/`diff:` groups
separated by a blank line. The parent relays this block verbatim to
the user — don't try to pre-apply it.

# Hard prohibitions

- No `git push --force` on any branch
- No `--no-verify` on commits (hooks exist for a reason)
- No commits directly to `main`
- No `rm -rf` or destructive ops without explicit confirmation
- No `gh pr merge` — opening the PR is the terminal action
- No committing `CLAUDE.md` edits derived from Lessons reflection to
  the work PR. Lessons are draft material in your final message only;
  the user owns the approval gate.

# Historical context (escape hatch)

If — and only if — you get genuinely stuck and need to understand _why_
this task was requested, the originating conversation is at:

<path to parent's session jsonl>

It's a large JSONL file. Use `grep` / `jq` to find specific turns. Do not
read the whole file. Prefer ending with a clarifying question in your
final message over spelunking the log.
```

### Fork detection decision tree (reference)

This is the same algorithm the brief's prose describes — kept here as a
diagram for the spec reader. The brief itself can't embed this fenced
block because a nested fence would terminate the outer markdown fence.

```
                       +------------------+
                       | How many remotes |
                       |  (origin + other)|
                       +--------+---------+
                                |
              +-----------------+----------------+
              |                                  |
          2 remotes                          1 remote
              |                                  |
   +----------+----------+            +----------+----------+
   | upstream = canonical|            | gh repo view origin |
   | origin   = fork?    |            |   --json isFork,    |
   +----------+----------+            |       parent        |
              |                       +----------+----------+
      +-------+--------+                         |
     yes              no             +-----------+-----------+
      |                |             |                       |
      v                v         isFork=true            isFork=false
  CASE A:         Remotes are        |                       |
  TWO-REMOTE      swapped; STOP      v                       v
  FORK WORKFLOW   and report     +-------+              +---------+
  push origin     (don't guess)  | owner | == auth?     | owner   | == auth?
  PR --repo                      +---+---+              +----+----+
  canonical                          |                       |
                              +------+-----+          +------+------+
                              |            |          |             |
                             yes           no        yes            no
                              |            |          |             |
                              v            v          v             v
                          CASE C:      Look for   CASE B:       CASE D:
                          FORK-ORIGIN  any other  DIRECT-PUSH   CANONICAL-
                          push origin  remote     WORKFLOW      ORIGIN, NO
                          PR --repo    matching   push origin   FORK WIRED
                          parent       auth org;  PR (no        Look for any
                          (slug from   if found,  --repo)       remote matching
                          .parent in   push there,               auth org. If
                          gh JSON)     PR --repo                 found, push
                                       <canonical>               there, PR
                                       Else STOP:                --repo origin.
                                       "set up                   Else STOP:
                                       fork first"               same fail msg.
```

`diagnose.py`'s `classify_remotes` covers Case A and the simple Case B
(single canonical origin matching auth) but **misses Cases C and D
entirely** — it has no concept of `gh auth status`, no `gh repo view
--json parent` lookup, and only does URL-vs-FORK_ORGS string matching.
The subagent must therefore not trust `is_fork_workflow: false` blindly;
for the single-remote case, it must run both the auth-account comparison
and the `gh repo view --json isFork,parent` lookup itself.

### Session log resolution

Parent resolves the session jsonl path with:

```bash
# Claude Code hashes the session's *cwd* at launch, not the repo root.
# If the parent is running inside a worktree (e.g. .worktrees/foo),
# the hash encodes that worktree path, not the main checkout.
cwd_hash=$(pwd | sed 's|/|-|g')
newest=$(/bin/ls -t "$HOME/.claude/projects/$cwd_hash"/*.jsonl 2>/dev/null | head -1)

# Fallback: if nothing found under cwd hash, try the repo toplevel hash.
if [ -z "$newest" ]; then
  toplevel=$(git rev-parse --show-toplevel)
  toplevel_hash=$(echo "$toplevel" | sed 's|/|-|g')
  newest=$(/bin/ls -t "$HOME/.claude/projects/$toplevel_hash"/*.jsonl 2>/dev/null | head -1)
fi
```

Caveats, all acceptable for v1 since the log is an escape hatch, not a
required input:

- **Parallel sessions in the same cwd** resolve to "whichever jsonl was
  most recently written to," which may be a sibling session.
- **Hash scheme may drift.** Claude Code's project-hash format is not a
  stable public API. If `$HOME/.claude/projects/$hash` doesn't exist on
  the running machine, the parent warns and omits the escape hatch
  entirely rather than pointing at a wrong file.
- **Worktree vs main checkout** is handled by trying cwd first, toplevel
  second.

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
   - If exactly one high-confidence match → propose it to the user and
     **wait for confirmation** before dispatching. Inference is never
     final — cross-repo work is too easy to get wrong silently.
   - If multiple or zero matches → fall through to step 3
3. **Ask.**
   - `/bin/ls ~/gits/` and present candidates; user picks

**Validation after resolution** (all use `git -C <path>` — parent never `cd`s):

- Path exists
- Is a git repo (`git -C <path> rev-parse --is-inside-work-tree`)
- Has a remote named `origin` that resolves (`git -C <path> remote get-url origin`)
- Default branch resolves via the helper below (Worktree Creation defines
  it once; reuse it here). The chain is: after `git fetch origin`, run
  `git -C <path> remote set-head origin --auto` to refresh the cached
  `refs/remotes/origin/HEAD` (plain fetch does not do this), then read
  `git -C <path> symbolic-ref --short refs/remotes/origin/HEAD` (strip
  the `origin/` prefix). If that fails, fall back to
  `gh repo view <owner/repo> --json defaultBranchRef -q .defaultBranchRef.name`
  where `<owner/repo>` is parsed from `git -C <path> remote get-url origin`.
  Final fallback: literal `main`. Note: there is no top-level `gh -R` flag,
  and `gh repo view` does not accept a filesystem path — it only accepts
  an `OWNER/REPO` slug positional or auto-detects from cwd.
- After `git -C <path> fetch origin`, `origin/<default-branch>` is
  reachable (`git -C <path> rev-parse --verify origin/<default>`)
- NOT required to be clean — worktrees off `origin/<default>` are safe
  even when the parent working tree is dirty. (The target's HEAD branch
  matters only for the gitignore-commit safety check in Worktree Creation.)

## Worktree Creation

Done directly by the parent, not via `using-git-worktrees`. The parent
runs these commands against the target via `git -C <target>` — it does
**not** `cd` into the target repo.

```bash
T=<absolute path to target repo>

git -C "$T" fetch origin

# Refresh the cached `refs/remotes/origin/HEAD`. Plain `git fetch origin`
# does NOT update this ref — it's set at clone time and only refreshed
# by an explicit `set-head --auto`. Without this call, a target whose
# default branch was renamed (e.g. master → main) since it was cloned
# would yield a stale value below. `--auto` is a no-op if origin/HEAD
# already matches the remote's HEAD.
git -C "$T" remote set-head origin --auto >/dev/null 2>&1 || true

# Determine default branch (may not be "main"). Step-by-step rather than
# a single `||`-chain because piping through `sed` swallows the upstream
# exit code, so a chained `|| echo main` never fires when symbolic-ref
# fails — the empty pipe output wins instead.
default_branch=""
ref=$(git -C "$T" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null) && \
  default_branch="${ref#origin/}"
if [ -z "$default_branch" ]; then
  # `gh repo view` takes an OWNER/REPO slug positional, NOT a path, and
  # there is no top-level `gh -R` flag. Parse the slug from origin URL.
  origin_url=$(git -C "$T" remote get-url origin 2>/dev/null)
  slug_repo=$(printf '%s\n' "$origin_url" | sed -E 's#(\.git)?$##; s#^.*[/:]([^/:]+/[^/:]+)$#\1#')
  default_branch=$(gh repo view "$slug_repo" --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null)
fi
default_branch="${default_branch:-main}"

# Derive slug from the task description. Reproducible rule:
#   1. Lowercase
#   2. Replace every char outside [a-z0-9] with `-`
#   3. Collapse repeated `-`, strip leading/trailing `-`
#   4. Truncate to 40 chars; re-strip trailing `-`
#   5. If the result is empty (task was non-ASCII or pure punctuation),
#      fall back to "task-$(date +%Y%m%d-%H%M%S)"
#   6. If a branch named `delegated/<slug>` already exists in $T, append
#      `-2`, `-3`, ... until unique (cap at -9; beyond that, fall through
#      to the timestamp form from step 5).
slug=$(printf '%s' "$task_description" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
  | cut -c1-40 \
  | sed -E 's/-+$//')
[ -z "$slug" ] && slug="task-$(date +%Y%m%d-%H%M%S)"
i=1
candidate="$slug"
while git -C "$T" show-ref --verify --quiet "refs/heads/delegated/$candidate"; do
  i=$((i + 1))
  if [ "$i" -gt 9 ]; then
    candidate="task-$(date +%Y%m%d-%H%M%S)"
    break
  fi
  candidate="$slug-$i"
done
slug="$candidate"
branch="delegated/$slug"
path="$T/.worktrees/delegated-$slug"

# Safety: ensure .worktrees/ is gitignored before we touch it.
# git check-ignore needs to run with the target as cwd because it resolves
# against the index of the repo containing cwd; `git -C` handles this.
# Note: check-ignore returns 0 if the path WOULD be ignored, 1 if not.
if ! git -C "$T" check-ignore -q .worktrees; then
  # .worktrees/ must be ignored by a commit on the default branch,
  # otherwise the ignore entry lives on a feature branch and disappears
  # the moment the target checks out anything else. Refuse to land the
  # gitignore edit on the wrong branch (this also catches detached HEAD,
  # where `git branch --show-current` prints empty).
  current=$(git -C "$T" branch --show-current)
  if [ "$current" != "$default_branch" ]; then
    echo "STOP: target HEAD is '${current:-<detached>}', not '$default_branch'."
    echo "The .worktrees/ gitignore entry must land on the default branch."
    echo "Ask the user to either (a) check out $default_branch in $T and"
    echo "retry, or (b) land the ignore via their normal PR flow first."
    exit 1
  fi

  # On the default branch. If the repo is PR-only for the default branch,
  # we still cannot commit-and-push directly. Run the protection probe
  # BEFORE mutating the working tree. Two complementary checks; either
  # tripping STOPs us:
  #   (a) gh api branch-protection — works if the auth account has admin
  #       read on the repo; on 404 (no protection or no permission) it
  #       still exits 0 with `{"message":"Not Found",...}`, so check the
  #       JSON shape, not the exit code.
  #   (b) git push --dry-run — server-side advisory; protection rules
  #       that gate the actual push (not dry-run) won't show here, but
  #       a wrong-credential 403 will. Use it only as a sanity check.
  origin_url=$(git -C "$T" remote get-url origin)
  slug_repo=$(printf '%s\n' "$origin_url" | sed -E 's#(\.git)?$##; s#^.*[/:]([^/:]+/[^/:]+)$#\1#')
  protection_json=$(gh api "repos/$slug_repo/branches/$default_branch/protection" 2>/dev/null || true)
  if printf '%s' "$protection_json" | grep -q '"required_pull_request_reviews"\|"required_status_checks"'; then
    echo "STOP: $slug_repo's $default_branch is branch-protected. The"
    echo ".worktrees/ gitignore entry needs to land via the target repo's"
    echo "normal PR flow first; this skill will not bypass branch protection."
    exit 1
  fi

  echo ".worktrees/" >> "$T/.gitignore"
  git -C "$T" add .gitignore
  git -C "$T" commit -m "chore: gitignore .worktrees/"
  # Note: this commit is local-only. Whether to push it is the target
  # repo's workflow concern, NOT this skill's. The subagent's PR branch
  # will contain this commit as part of its base.
fi

# Create the worktree with a new branch rooted at origin/<default>.
# `worktree add` is the only step that mutates the working set; if it
# fails (path collision, ref missing), parent stops and reports — no
# cleanup of the gitignore commit, since that commit is independently
# valuable.
git -C "$T" worktree add "$path" -b "$branch" "origin/$default_branch"
```

Why not call `using-git-worktrees`:

- That skill branches off current HEAD (no way to pass a base ref)
- It auto-runs `npm install` / `cargo build` / `pip install` — noisy and
  wrong for doc-only or tiny changes
- It runs the target repo's test suite as a baseline check — slow, and
  unnecessary before the subagent has done anything

Naming:

- Directory: `.worktrees/delegated-<slug>`
- Branch: `delegated/<slug>` where `<slug>` follows the precise derivation
  rule in the recipe above (lowercase, non-alnum → `-`, collapse, trim,
  ≤40 chars; empty/non-ASCII falls back to `task-<timestamp>`; collisions
  with existing branches get `-2`...`-9` then a timestamp).
- Base: `origin/<default-branch>` — always fresh, never current HEAD

**v1 limitation — worktree location preference:** `using-git-worktrees`
honors a target repo's CLAUDE.md `worktree.*director` preference (and an
existing `worktrees/` dir as a fallback). This skill **does not** — it
hardcodes `.worktrees/delegated-<slug>`. If a target repo specifies a
different worktree location in its CLAUDE.md (e.g.
`~/.config/superpowers/worktrees/`), this skill silently ignores it. If
this becomes a real problem in practice, lift the directory-selection
logic out of `using-git-worktrees` into a shared snippet or accept the
worktree location as a parent argument. v1 punts.

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
- **`git fetch origin` fails** (network, auth, missing remote) → stop,
  surface the error, don't dispatch
- **`.worktrees/` not gitignored and the target is checked out on a
  non-default branch (or detached HEAD)** → stop, explain: the gitignore
  entry must land on the default branch; ask the user to check out the
  default branch in the target repo (or land the ignore via their normal
  PR flow) and retry. The recipe's `current=$(git branch --show-current)`
  returns empty string on detached HEAD, which compares unequal to the
  default branch and triggers this same path with a `<detached>`
  placeholder in the message.
- **`.worktrees/` not gitignored, target is on the default branch, and
  `gh api repos/.../branches/<default>/protection` reports
  `required_pull_request_reviews` or `required_status_checks`** → stop,
  explain: user needs to land the gitignore change via their normal PR
  flow first; this skill will not bypass branch protection. (If the auth
  account lacks admin read on the repo, the api returns Not Found and
  the probe is silently a no-op — false negatives are accepted because
  the worst case is the subsequent direct commit succeeds locally and
  the eventual `git push` of the worktree branch is unaffected.)
- **`git worktree add` fails** (path already exists, branch already
  exists, base ref missing) → stop, surface the error, don't dispatch
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

## Integration with learn-from-session

`learn-from-session`-style reflection happens **inside the subagent**,
not the parent. This is a deliberate split driven by visibility: the
parent's view of the delegated run is limited to "brief in, structured
final message out." It cannot see which hooks bit the subagent, which
docs were missing, which commands were ambiguous, or which patterns
the subagent had to invent. Parent has no substrate to reflect on. The
subagent, by contrast, has lived the work, and is the only actor that
can honestly answer `learn-from-session`'s reflection prompts for this
target repo.

### Responsibility split

1. **Subagent drafts.** Reflection → durability filter → drafted diff.
   Never commits the `CLAUDE.md` edit to the work PR; keeps it as
   draft material in the final message only.
2. **Parent relays verbatim.** The `Lessons:` block from the subagent's
   final message passes through unchanged into the parent's final
   report to the user.
3. **Parent offers two follow-up paths.**
   - **Quick path:** "Open a second PR in the same worktree with just
     this `CLAUDE.md` addition?" Parent runs the commit and
     `gh pr create` in the existing `.worktrees/delegated-<slug>`
     worktree (which still exists because cleanup is manual). Fast
     follow, same branch name suffixed with `-lessons`.
   - **Full path:** "Run `/learn-from-session` on the target repo for
     multi-file routing?" For lessons that might belong in multiple
     `CLAUDE.md` files or need deeper routing than a single
     mechanical insertion.
4. **User decides.** Approval gate stays with the user, not the
   subagent and not the parent. Rejected lessons ("skip it") are not a
   failure state — parent simply omits the follow-up offer and the
   delegated run is considered complete.

### Why not auto-commit lessons to the work PR

Because lesson drafting is high-noise by default. `learn-from-session`'s
own rules say "when in doubt, omit" and require explicit user approval
before `CLAUDE.md` edits land. Bypassing that gate to save a round-trip
inverts the cost/risk ratio — a wrongly-committed lesson is harder to
remove than a lost lesson is to regenerate on a re-run.

### Why not have the parent reflect independently

The parent's context after dispatch contains exactly the PR URL and
summary — there is no substrate for parent-side reflection. Attempting
it would either hallucinate observations or re-derive what the
subagent already knew.

## Files

- `skills/delegate-to-other-repo/SKILL.md` — the skill (pure markdown)

No Python, no tests. Target resolution and brief construction are prompt
work, not code work. This mirrors how `learn-from-session` is structured
(pure markdown, no helpers) rather than `up-to-date` (which has a helper
because it parallelizes subprocess calls and needs unit-testable
classification logic).

### Frontmatter

```yaml
---
name: delegate-to-other-repo
description: Delegate a task in a different git repo to a subagent in an isolated context. Use when the user asks to make a change in another repo without polluting the current session's context. Parent sets up a worktree off the target's default branch and dispatches a subagent that opens a PR back to the canonical remote.
allowed-tools: Bash, Read, Grep, Glob, Agent
---
```

Note `Agent` in `allowed-tools` — the skill dispatches a subagent via the
Agent tool, which must be explicitly allowed.

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
- **Worktree location:** hardcoded `.worktrees/delegated-<slug>`; target
  repo's CLAUDE.md `worktree.*director` preference is intentionally
  ignored in v1 (see Worktree Creation note)
- **No-CLAUDE.md target:** subagent proceeds, flags the absence in final summary
- **Background dispatch:** only if user explicitly asks
- **URL cloning:** not supported in v1; errors with `gh repo clone` hint
- **Parallel session log ambiguity:** accepted v1 limitation, documented
- **Fork detection:** subagent uses `up-to-date/diagnose.py` as a
  shortcut for the URL-based classification (Case A and the simple
  Case B), then runs `gh auth status` plus
  `gh repo view --json isFork,parent` itself for the single-remote
  cases (C and D), which the script does not cover
- **Slug derivation:** precise rule pinned in Worktree Creation —
  lowercase, non-alnum collapsed to `-`, trimmed to 40 chars; empty
  input falls back to `task-<timestamp>`; branch collisions get a
  `-2`...`-9` suffix then a timestamp
- **Branch-protection detection:** `gh api .../branches/<default>/protection`
  with a JSON-shape check (looking for `required_pull_request_reviews`
  or `required_status_checks`). Accepts false negatives when the auth
  account lacks admin read

## Success Criteria

The skill is working when:

1. A user can say `/delegate-to-other-repo fix the typo on the homepage of
the blog` and end up with a clickable PR URL without touching another
   terminal
2. The parent session's context after delegation contains only the PR URL
   and summary — not the target repo's `CLAUDE.md`, file reads, or diff
3. If the subagent fails, the parent surfaces actionable error info and
   preserves the worktree for takeover
4. Parent-side state pollution is minimal: no changes to the parent's
   working repo, and the target repo's only modification (if any) is a
   one-line `.gitignore` entry for `.worktrees/` — committed in the
   target, never in the parent's checkout
5. If the subagent's work surfaced durable lessons about the target
   repo, they are returned in a structured `Lessons:` block that the
   parent relays verbatim to the user. Lessons are never
   auto-committed by either parent or subagent; the user is offered a
   fast-follow path (same-worktree second PR) or the full
   `/learn-from-session` flow. Rejection is a normal non-failure
   terminal state.
