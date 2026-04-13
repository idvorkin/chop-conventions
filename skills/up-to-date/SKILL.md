---
name: up-to-date
description: Sync git repository with upstream. Use at the start of a session, when asked to sync, get up to date, check git status, or when working on a stale branch.
allowed-tools: Bash, Read
---

# Up To Date

Diagnose and sync the current git repo with upstream.

## Step 1: Diagnose

Run the helper — it fetches, queries `gh pr view`, and checks remote hygiene in parallel, then prints JSON:

```bash
~/.claude/skills/up-to-date/diagnose.py
```

(Project-level installs: the script lives alongside this SKILL.md at `<project>/.claude/skills/up-to-date/diagnose.py`. If `~/.claude/skills/up-to-date/diagnose.py` is missing, use the project-local path instead.)

The JSON output has this shape:

```json
{
  "remotes": {
    "entries": [{"name": "origin", "url": "..."}, ...],
    "source": "upstream",
    "is_fork_workflow": true,
    "issues": [{"kind": "...", "detail": "...", "fix": "..."}]
  },
  "branch": {
    "name": "main",
    "is_main": true,
    "behind": 0,
    "ahead": 0,
    "behind_commits": ["abc123 subject", ...],
    "leftover_commits": [...]
  },
  "worktree": {
    "uncommitted": ["M  foo.py", ...],
    "stashes": ["stash@{0}: ...", ...]
  },
  "pr": {
    "state": "MERGED",
    "number": 42,
    "title": "...",
    "mergeable": "MERGEABLE",
    "review_decision": "APPROVED",
    "recent_reviews": [...],
    "recent_comments": [...]
  },
  "errors": []
}
```

Conventions:

- `remotes.source` is either `"upstream"` (fork workflow) or `"origin"` (single-remote). Use this as `SRC` for all subsequent git commands.
- `pr` is `null` on main or when no PR exists for the current branch.
- `branch.leftover_commits` lists patch-unique commits on a feature branch that are still missing from `source/main`. Commits already applied upstream under a different SHA are filtered out.
- `errors` contains any subprocess failures the script wants surfaced (empty on the happy path).

## Step 2: Report Hygiene

If `remotes.issues` is non-empty, show them in the output table and offer the `fix` commands — **do not execute automatically**. Known issue kinds:

- `non_standard_name` — remote named something other than `origin`/`upstream`
- `swapped_remotes` — `origin` points at canonical while a fork remote exists
- `fork_without_canonical` — fork remote exists but no canonical upstream

## Step 3: Act

Use `SRC = remotes.source`. After any action on main, clean up merged branches:

```bash
git branch --merged main | grep -v '^\*\|main' | xargs -r git branch -d
```

### On main (`branch.is_main` true)

```bash
git pull $SRC main
# Fork workflow: keep fork in sync
[ "$SRC" = "upstream" ] && git push origin main
```

If a PR is needed after syncing `main`, derive it from the remotes:

- Fork workflow (`origin` = fork, `upstream` = canonical): if commits are already on fork `main` but not canonical `main`, open a recovery PR from `origin:main` to `upstream:main` with `gh pr create --repo <upstream-owner>/<repo> --head <origin-owner>:main --base main`.
- No fork (`origin` is canonical): do **not** use the recovery flow. Create a feature branch from the current commit and open a branch PR instead.
- Remote hygiene issues present: fix remotes first; do not guess the PR command from a miswired setup.

### Feature branch + PR merged (`pr.state == "MERGED"`)

Check `branch.leftover_commits` first:

- Non-empty → **ASK USER**: new PR for leftovers, or discard?
- Empty → safe to switch to main and delete branch

```bash
BRANCH=$(git branch --show-current)
git checkout main && git pull $SRC main
[ "$SRC" = "upstream" ] && git push origin main
git branch -d "$BRANCH"  # use -D only if user confirmed discarding leftovers
```

### Feature branch + PR open (`pr.state == "OPEN"`)

Report status from the JSON. `pr.recent_reviews` and `pr.recent_comments` already hold the last 3 of each — surface those to the user.

### Feature branch + PR closed, not merged (`pr.state == "CLOSED"`)

**Ask user**: delete branch or keep working?

### Feature branch + no PR (`pr` is null)

- `branch.ahead > 0` → ask if the user wants to create a PR
- `branch.ahead == 0` → ask if the user wants to delete the branch

### Uncommitted changes

If `worktree.uncommitted` is non-empty, list the files and **ask user**: commit, stash, or discard. Never act automatically.

### Stashed changes

If `worktree.stashes` is non-empty, list them and inform the user.

## Output Format

| Check              | Status           | Action                                       |
| ------------------ | ---------------- | -------------------------------------------- |
| Remote naming      | pass/fail        | Offer rename commands from issue `fix` field |
| Workflow           | PR / direct push | Warn if fork org pushing direct              |
| Branch             | `branch.name`    | —                                            |
| PR                 | `#N STATE`       | Context-dependent                            |
| Uncommitted        | N files          | Listed below                                 |
| Behind source/main | N commits        | Will pull                                    |
| Stashes            | N stashes        | Listed below                                 |

## Post-Sync

Ask: "Want to `/clear` context for a fresh start?"

## Safety

- NEVER force push — **except** when syncing a fork's main and either (a) the only divergence is automated backlink commits (`chore: update backlinks [skip ci]`) or (b) the fork-only commits are already present upstream by patch equivalence (`git cherry -v upstream/main main` shows only `-` entries), so resetting local `main` to `upstream/main` and force-pushing `origin/main` loses no unique work. In those cases, force push to the fork is safe and expected.
- NEVER delete unmerged branches without asking
- NEVER commit/discard uncommitted changes without user approval

## Manual fallback

If `diagnose.py` is missing or errors, fall back to running commands directly:

```bash
SRC=$(git remote | grep -q '^upstream$' && echo upstream || echo origin)
git remote -v
git fetch --all --prune
git branch --show-current
git status --porcelain
git stash list
git rev-list --count HEAD..$SRC/main   # behind
git rev-list --count $SRC/main..HEAD   # ahead
gh pr view --json state,number,title,mergeable,reviewDecision 2>/dev/null
```

## Implementation

The `diagnose.py` script is ~150 lines of stdlib Python with a `#!/usr/bin/env -S uv run --script` shebang, so it runs without manual env setup wherever `uv` is installed. Pure classification logic (`parse_remotes`, `is_fork_url`, `classify_remotes`) is unit-tested in `test_diagnose.py` — run `python3 -m unittest test_diagnose.py` from this directory.
