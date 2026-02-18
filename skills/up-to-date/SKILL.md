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

Report remote setup, then summarize findings:

| Check                | Status           | Action            |
| -------------------- | ---------------- | ----------------- |
| Branch               | `feature-xyz`    | -                 |
| PR                   | #123 MERGED      | Switching to main |
| Uncommitted          | 2 files modified | Listed below      |
| Behind upstream/main | 5 commits        | Will pull         |
| Fork main stale      | 3 commits behind | Will sync         |

Then take actions and report results.

## Post-Sync

After sync completes, ask: "Want to `/clear` context for a fresh start?" — users often run this at session start and stale context causes confusion.

## Safety Rules

- NEVER force push
- NEVER delete unmerged branches without asking
- NEVER commit or discard uncommitted changes without user approval
- Always preserve user's work
