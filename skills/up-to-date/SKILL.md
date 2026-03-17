---
name: up-to-date
description: Sync git repository with upstream. Use at the start of a session, when asked to sync, get up to date, check git status, or when working on a stale branch.
allowed-tools: Bash, Read
---

# Up To Date

Diagnose and sync the current git repo with upstream.

## Step 1: Remote Hygiene

Check remotes follow convention before doing anything else.

**Convention:** `origin` = your fork (push here), `upstream` = canonical repo (PRs target here). Single-remote repos use `origin` as source of truth.

**Fork orgs** (must use PRs, never direct push to canonical): `idvorkin-ai-tools`

```bash
git remote -v
```

Check for these problems and report in output table:
1. **Non-standard names** — remotes named anything other than `origin`/`upstream` (e.g., `fork`)
2. **Swapped remotes** — `origin` points to canonical repo when a fork remote exists
3. **Fork org without PR workflow** — a known fork org remote exists but isn't set up as `origin`

If issues found, **offer fix commands but don't execute automatically**:
```bash
git remote rename <canonical> upstream
git remote rename <fork> origin
git branch --set-upstream-to=upstream/main main
```

## Step 2: Diagnose

```bash
# Determine source of truth
SRC=$(git remote | grep -q '^upstream$' && echo upstream || echo origin)

git fetch --all --prune 2>&1
git branch --show-current
git status --porcelain
git stash list
echo "Behind $SRC/main:" && git rev-list --count HEAD..$SRC/main
echo "Ahead of $SRC/main:" && git rev-list --count $SRC/main..HEAD
git log --oneline HEAD..$SRC/main | head -10
```

On a feature branch, also check:
```bash
gh pr view --json state,number,title,mergeable,reviewDecision 2>/dev/null || echo "NO_PR"
```

## Step 3: Act

Use `SRC` from diagnosis. After any action on main, clean up merged branches:
```bash
git branch --merged main | grep -v '^\*\|main' | xargs -r git branch -d
```

### On main
```bash
git pull $SRC main
# Fork workflow: keep fork in sync
[ "$SRC" = "upstream" ] && git push origin main
```

### Feature branch + PR merged
Check for leftover commits (made after PR merged), then switch to main:
```bash
BRANCH=$(git branch --show-current)
LEFTOVER=$(git log --oneline $SRC/main..$BRANCH)
# If leftover commits exist → ASK USER: new PR or discard?
git checkout main && git pull $SRC main
[ "$SRC" = "upstream" ] && git push origin main
git branch -d "$BRANCH"  # use -D only if user confirmed discard of leftovers
```

### Feature branch + PR open
Report status and show recent feedback:
```bash
gh pr view --json reviews,comments --jq '.reviews[-3:], .comments[-3:]'
```

### Feature branch + PR closed (not merged)
Ask user: delete branch or keep working?

### Feature branch + no PR
- Has commits ahead → ask if user wants to create PR
- No commits ahead → ask if user wants to delete branch

### Uncommitted changes
List with `git status`. **Ask user**: commit, stash, or discard. Never act automatically.

### Stashed changes
List with `git stash list` and inform user.

## Output Format

| Check | Status | Action |
|---|---|---|
| Remote naming | pass/fail | Offer rename commands |
| Workflow | PR / direct push | Warn if fork org pushing direct |
| Branch | `branch-name` | — |
| PR | #N STATE | Context-dependent |
| Uncommitted | N files | Listed below |
| Behind source/main | N commits | Will pull |
| Stashes | N stashes | Listed below |

## Post-Sync

Ask: "Want to `/clear` context for a fresh start?"

## Safety

- NEVER force push
- NEVER delete unmerged branches without asking
- NEVER commit/discard uncommitted changes without user approval
