---
name: up-to-date
description: Sync git repository with upstream. Use at the start of a session, when asked to sync, get up to date, check git status, or when working on a stale branch.
allowed-tools: Bash, Read
---

# Up To Date

Diagnose and sync the current git repo with upstream.

## Step 1: Diagnose

Run the versioned helper that lives with this skill — it fetches, queries
`gh pr view`, and checks remote hygiene in parallel, then prints JSON. In this
source repo that path is:

```bash
skills/up-to-date/diagnose.py
```

When the skill is installed into `~/.claude/skills/` or `<project>/.claude/skills/`,
that installed path is typically a symlink to this same file. Prefer the
versioned repo copy when you are editing this repository.

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
    "ahead_patch_unique_commits": ["def456 subject", ...],
    "ahead_patch_equivalent_commits": ["ghi789 subject", ...],
    "can_force_align": false,
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
  "shared_claude_md": {
    "machine_info": {"machine": "orbstack-dev", "dev_machine": true, "reasons": [...]},
    "chop_root": "/home/user/gits/chop-conventions",
    "enabled": true,
    "expected_symlinks": {"global": {...}, "machine": {...}, "dev_machine": {...}},
    "actual":            {"global": {...}, "machine": {...}, "dev_machine": {...}},
    "actions":           [{"kind": "create_symlink", "slot": "global", ...}]
  },
  "post_up_to_date_path": "/home/user/gits/foo/.claude/post-up-to-date.md",
  "errors": []
}
```

`shared_claude_md` is **omitted entirely** when `diagnose.py` cannot
resolve a chop-conventions checkout. In that case `errors[]` carries a
`{subsystem: "shared_claude_md", code: "chop_root_unresolved"}` entry
and Step 3.5 is skipped without blocking the rest of `/up-to-date`.

`post_up_to_date_path` is `null` when no `.claude/post-up-to-date.md`
exists at the repo toplevel; symlinked hooks are refused and surface as
`{subsystem: "post_up_to_date", code: "hook_is_symlink"}` in
`errors[]`.

Conventions:

- `remotes.source` is either `"upstream"` (fork workflow) or `"origin"` (single-remote). Use this as `SRC` for all subsequent git commands.
- `pr` is `null` on main or when no PR exists for the current branch.
- `branch.ahead_patch_unique_commits` and `branch.ahead_patch_equivalent_commits` come from `git cherry -v source/main HEAD`, so the script tells you whether ahead commits are unique work or already present upstream under different SHAs.
- `branch.can_force_align` is `true` only on `main` when every ahead commit is patch-equivalent to `source/main`; in that case, re-aligning the fork's `main` loses no unique work.
- `branch.leftover_commits` lists patch-unique commits on a feature branch that are still missing from `source/main`. Commits already applied upstream under a different SHA are filtered out.
- `errors` contains subprocess failures from fetch and the post-fetch git diagnostics (`rev-list`, `log`, `status`, `stash`, `cherry`) so callers can tell the difference between "no divergence" and "diagnostic failed".

## Step 2: Report Hygiene

If `remotes.issues` is non-empty, show them in the output table and offer the `fix` commands — **do not execute automatically**. Known issue kinds:

- `non_standard_name` — remote named something other than `origin`/`upstream`
- `swapped_remotes` — `origin` points at canonical while a fork remote exists
- `fork_without_canonical` — fork remote exists but no canonical upstream

## Step 3: Act

Use `SRC = remotes.source`. After any action on main, surface absorbed branches and prunable worktrees from the diagnose JSON — both are pre-computed via `git cherry` (patch-id), so squash/rebase/cherry-pick merges are all detected uniformly.

**Do not use `git branch --merged` for cleanup.** It only catches branches whose tip is an ancestor of main, missing squash and rebase merges — the most common paths for PRs to land. Use `diagnose.py`'s `absorbable_branches` field instead.

### Absorbable branches (`absorbable_branches` in the JSON)

Local branches (excluding `main`, `master`, and the currently checked-out branch) whose every commit is patch-id-equivalent to something already in `$SRC/main`. Safe to delete.

Surface the list to the user and offer deletion. Use `-d` first (safe), fall back to `-D` only after the patch-id check already verified:

```bash
git branch -d <branch> 2>/dev/null || git branch -D <branch>
```

### Prunable worktrees (`worktrees` in the JSON)

Each entry has `{path, branch, is_primary, absorbed, unmerged_count}`. **A worktree is prunable iff `is_primary == false AND absorbed == true`.** The primary checkout is flagged separately and is never a deletion candidate, regardless of its branch's absorption state — removing the primary is destructive.

Surface prunable worktrees to the user and offer removal. **Do not auto-remove** — a stale worktree on disk is cheap, a lost in-progress change is expensive:

```bash
git worktree remove <path>
git branch -D <branch>   # branch left behind by worktree remove; safe after patch-id check
```

Non-primary worktrees with `absorbed == false` (`unmerged_count > 0`) should be **kept** — their branch still has work not yet in `$SRC/main`.

### On main (`branch.is_main` true)

If `branch.can_force_align` is true, prefer:

```bash
git pull --rebase $SRC main
[ "$SRC" = "upstream" ] && git push --force-with-lease origin main
```

Otherwise:

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

## Step 3.5 — Shared CLAUDE.md setup / resync

Consult `shared_claude_md` in the diagnose JSON. Skip this step entirely
when any `errors[]` entry has `subsystem == "shared_claude_md"` — that
includes `chop_root_unresolved` (no chop-conventions checkout found),
which the skill surfaces once at end-of-run with the remediation
hint. The other shared-CLAUDE.md subsystems (a symlinked
`~/.claude/claude-md`, etc.) are also reported but not auto-fixed.

If the `shared_claude_md` key is absent, the subsystem could not run —
treat exactly the same as "skip this step". The skill never invents an
empty block.

### First-run opt-in

If `shared_claude_md.enabled == false` AND `shared_claude_md.actions` is
empty, the user has never opted in on this machine. Offer opt-in:

1. Print `shared_claude_md.machine_info` so the user sees which files
   would be linked: `machine` (and, if `dev_machine==true`, the
   `dev-machine.md` slot).
2. Ask: "Enable shared CLAUDE.md on this machine?"
3. On approval, verify `~/.claude/claude-md` is **not a symlink**
   (`Path.is_symlink()` — refuse with a clear error if it is), then
   `mkdir -p ~/.claude/claude-md` and `touch ~/.claude/claude-md/.enabled`.
4. Re-run `diagnose.py` and act on the now-non-empty `actions` list.
5. Print the `@`-import lines the user must add to `~/.claude/CLAUDE.md`
   by hand — the skill never edits that file automatically because it
   may contain machine-local overrides. Template:

   ```markdown
   @~/.claude/claude-md/global.md
   @~/.claude/claude-md/machine.md
   @~/.claude/claude-md/dev-machine.md
   ```

   On non-dev machines the third line resolves to a missing symlink and
   is silently ignored — Claude Code's `@`-imports no-op on absent
   targets — so the template is byte-identical across all machines.

### Subsequent runs — action loop

For each action in `shared_claude_md.actions`:

| Kind                      | Command                 | Auto?        |
| ------------------------- | ----------------------- | ------------ |
| `create_symlink`          | `ln -s <target> <path>` | Ask user     |
| `replace_stale_symlink`   | `ln -sfn <target> <path>` | Ask user   |
| `remove_obsolete_symlink` | report only             | Never auto   |
| `report_user_file`        | report only             | Never auto   |

`create_symlink` uses plain `-s` (NOT `-sfn`) so a race-condition real
file at `<path>` fails loudly rather than being clobbered. Stay silent
when `actions` is empty.

## Step 5 — Post-hook

If `post_up_to_date_path` is non-null, run the hook-trust helper:

```bash
skills/up-to-date/hook_trust.py --repo-toplevel $(git rev-parse --show-toplevel) --pretty
```

The helper returns a JSON object with `status`:

- `trusted` — hash matches a recorded approval. Execute the hook by
  reading the content from the helper's `content_b64` field (base64-
  decoded), treating the markdown as instructions to follow. **Do not
  re-read the file from disk** — the helper already performed the
  single authoritative read; re-reading opens a TOCTOU window between
  the hash check and execution.
- `first_sight` — no prior approval. Display the decoded content to
  the user, ask "trust this hook?", and on approval run
  `hook_trust.py --approve --repo-toplevel <toplevel>` to record the
  hash. Then execute the content from memory.
- `changed` — the hook content changed since the prior approval.
  Same flow as `first_sight` — show the new content, ask, approve,
  execute.
- `corrupt` — the trust store at `~/.claude/claude-md/hooks-trusted.json`
  could not be parsed. Surface the error to the user and SKIP
  execution. Do NOT overwrite the corrupt file — the user must
  inspect and repair (or `rm` to reset all trust) manually.
- `rejected` — the hook is a symlink or unreadable. Surface the error
  and skip. Symlinked hooks are refused outright because their targets
  can drift outside the repo's commit history.
- `absent` — no hook file present. No-op.

Hooks fire on every `/up-to-date` run regardless of whether commits
were pulled; the markdown is responsible for its own idempotency.

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
git rev-list --left-right --count $SRC/main...HEAD
git cherry -v $SRC/main HEAD
gh pr view --json state,number,title,mergeable,reviewDecision 2>/dev/null
```

## Implementation

The `diagnose.py` script is stdlib-only Python with a `#!/usr/bin/env -S uv run --script` shebang, so it runs without manual env setup wherever `uv` is installed. Pure classification logic (`parse_remotes`, `is_fork_url`, `classify_remotes`, cherry parsing) is unit-tested in `test_diagnose.py` — run `python3 -m unittest test_diagnose.py` from this directory.
