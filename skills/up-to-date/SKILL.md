---
name: up-to-date
description: Sync git repository with upstream. Use at the start of a session, when asked to sync, get up to date, check git status, or when working on a stale branch. Checks branch status, uncommitted changes, PR state, and upstream drift, then takes appropriate actions.
allowed-tools: Bash, Read
---

# Up To Date

Diagnose and sync the current git repository state with upstream.

## Remote Setup

Detect the remote configuration — this determines where to sync from:

| Setup         | Remotes           | Source of Truth | Push To |
| ------------- | ----------------- | --------------- | ------- |
| Fork workflow | origin + upstream | upstream/main   | origin  |
| Direct access | origin only       | origin/main     | origin  |

## Remote Hygiene Check

**Run this before any sync operations.** Validates that remotes follow conventions and the correct workflow is being used.

### Naming Convention

The standard convention is:
- `origin` → your fork (where you push feature branches)
- `upstream` → the canonical repo (where PRs target)

Detect misconfigurations by inspecting all remote URLs:

```bash
echo "=== Remote Hygiene Check ==="
REMOTES=$(git remote -v | grep '(fetch)')

# Collect all remote names and their GitHub orgs/users
while IFS= read -r line; do
    REMOTE_NAME=$(echo "$line" | awk '{print $1}')
    URL=$(echo "$line" | awk '{print $2}')
    # Extract org/user from GitHub URL (handles https and ssh)
    ORG=$(echo "$URL" | sed -E 's#.*(github\.com[:/])([^/]+)/.*#\2#')
    echo "  $REMOTE_NAME -> $ORG ($URL)"
done <<< "$REMOTES"
```

Flag these problems:

1. **Non-standard remote names**: If a remote exists that is neither `origin` nor `upstream` (e.g., `fork`), warn that it should be renamed:
   - The canonical repo should be `upstream`
   - The fork should be `origin`

2. **origin points to canonical repo in a fork setup**: If there are two remotes and `origin` points to the canonical (non-fork) repo, the remotes are swapped.

### Workflow Convention: Fork Orgs Must Use PRs

Some GitHub orgs are **fork orgs** — they exist to fork repos, not to own canonical code. Commits from these orgs should always reach the canonical repo via pull requests, never direct pushes.

Known fork orgs:
- `idvorkin-ai-tools`

```bash
# Check if any remote points to a known fork org
FORK_ORGS="idvorkin-ai-tools"
for org in $FORK_ORGS; do
    FORK_URL=$(git remote -v | grep "(fetch)" | grep "$org" | head -1)
    if [ -n "$FORK_URL" ]; then
        FORK_REMOTE=$(echo "$FORK_URL" | awk '{print $1}')
        echo "⚠️  Remote '$FORK_REMOTE' points to fork org '$org'"
        echo "   Workflow: push to '$FORK_REMOTE', then create PR to canonical repo"
        echo "   NEVER push directly to the canonical repo from a fork org"

        # Check if remote naming is correct
        if [ "$FORK_REMOTE" != "origin" ]; then
            echo "   ❌ NAMING: '$FORK_REMOTE' should be renamed to 'origin'"
            echo "   Fix: git remote rename $FORK_REMOTE origin"
        fi

        # The other remote should be 'upstream'
        CANONICAL_REMOTE=$(git remote -v | grep "(fetch)" | grep -v "$org" | head -1)
        if [ -n "$CANONICAL_REMOTE" ]; then
            CANONICAL_NAME=$(echo "$CANONICAL_REMOTE" | awk '{print $1}')
            if [ "$CANONICAL_NAME" != "upstream" ]; then
                echo "   ❌ NAMING: '$CANONICAL_NAME' should be renamed to 'upstream'"
                echo "   Fix: git remote rename $CANONICAL_NAME upstream"
            fi
        fi
    fi
done
```

### Offering to Fix

If remote naming issues are detected, offer the user the rename commands but **never execute them automatically**. Renaming remotes can break existing branch tracking and scripts.

Example fix for swapped remotes:
```bash
# Rename origin -> upstream (canonical repo)
git remote rename origin upstream
# Rename fork -> origin (your fork)
git remote rename fork origin
# Update tracking for main
git branch --set-upstream-to=upstream/main main
```

## Diagnostic Steps

Run all checks in one go:

```bash
SOURCE_REMOTE=$(git remote | grep -q '^upstream$' && echo "upstream" || echo "origin")
echo "Source of truth: $SOURCE_REMOTE/main"

git branch --show-current
git status --porcelain
git stash list
git fetch --all --prune 2>&1

echo "Behind $SOURCE_REMOTE/main:"
git rev-list --count HEAD..$SOURCE_REMOTE/main 2>/dev/null || echo "0"
echo "Ahead of $SOURCE_REMOTE/main:"
git rev-list --count $SOURCE_REMOTE/main..HEAD 2>/dev/null || echo "0"

git log --oneline HEAD..$SOURCE_REMOTE/main 2>/dev/null | head -10
```

If on a feature branch, also check PR status:

```bash
gh pr view --json state,number,title,mergeable,reviewDecision 2>/dev/null || echo "NO_PR"
```

## Decision Tree

### On main branch

```bash
SOURCE_REMOTE=$(git remote | grep -q '^upstream$' && echo "upstream" || echo "origin")
git pull $SOURCE_REMOTE main
# Fork workflow: also push to origin to keep fork in sync
if [ "$SOURCE_REMOTE" = "upstream" ]; then
    git push origin main
fi
```

### On feature branch with PR

- **PR merged** → Check for leftover commits, switch to main, sync:

  ```bash
  BRANCH=$(git branch --show-current)
  SOURCE_REMOTE=$(git remote | grep -q '^upstream$' && echo "upstream" || echo "origin")
  git fetch $SOURCE_REMOTE main
  LEFTOVER=$(git log --oneline $SOURCE_REMOTE/main..$BRANCH | wc -l)

  if [ "$LEFTOVER" -gt 0 ]; then
      echo "⚠️  $LEFTOVER commit(s) on $BRANCH not in $SOURCE_REMOTE/main:"
      git log --oneline $SOURCE_REMOTE/main..$BRANCH
      # ASK USER: create new PR for these, or cherry-pick to main?
  fi

  git checkout main
  git pull $SOURCE_REMOTE main
  [ "$SOURCE_REMOTE" = "upstream" ] && git push origin main
  git branch -d "$BRANCH"
  ```

- **PR closed (not merged)** → Ask user: delete branch or keep working?

- **PR open** → Report status, show recent review feedback:
  ```bash
  gh pr view --json reviews,comments --jq '.reviews[-3:], .comments[-3:]'
  ```

### On feature branch without PR

- **Has commits ahead of main** → Ask if user wants to create PR
- **No commits ahead** → Ask if user wants to delete branch

### Uncommitted changes

- List with `git status`
- **Ask user**: commit, stash, or discard
- Do NOT automatically commit or discard

### Stashed changes

- List with `git stash list` and inform user

## Cleanup: Delete Merged Branches

After switching to main:

```bash
git branch --merged main | grep -v '^\*' | grep -v 'main' | while read branch; do
    echo "Deleting merged branch: $branch"
    git branch -d "$branch"
done
```

## Output Format

Report remote hygiene first, then summarize sync findings:

| Check                | Status                          | Action                  |
| -------------------- | ------------------------------- | ----------------------- |
| Remote naming        | ✅ Correct / ❌ `fork`→`origin` | Offer rename commands   |
| Workflow             | ✅ PR workflow / ❌ Direct push  | Warn about convention   |
| Branch               | `feature-xyz`                   | -                       |
| PR                   | #123 MERGED                     | Switching to main       |
| Uncommitted          | 2 files modified                | Listed below            |
| Behind upstream/main | 5 commits                       | Will pull               |
| Fork main stale      | 3 commits behind                | Will sync               |

Then take actions and report results.

## Post-Sync

After sync completes, ask: "Want to `/clear` context for a fresh start?" — users often run this at session start and stale context causes confusion.

## Safety Rules

- NEVER force push
- NEVER delete unmerged branches without asking
- NEVER commit or discard uncommitted changes without user approval
- Always preserve user's work
