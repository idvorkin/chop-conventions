---
name: up-to-date
description: Sync git repository with upstream. Use at the start of a session, when asked to sync, get up to date, check git status, or when working on a stale branch. Checks branch status, uncommitted changes, PR state, and upstream drift, then takes appropriate actions.
allowed-tools: Bash, Read
---

# Up To Date

Diagnose and sync the current git repository state with upstream.

## When To Use

- At the start of a new session (proactively)
- When the user says "up to date", "sync", "git status", or similar
- When you suspect you're on a stale or merged branch
- Before starting new work

## Diagnostic Steps

Run these checks in parallel:

```bash
# Current state
git branch --show-current
git status --porcelain
git stash list

# Upstream comparison
git fetch --all --prune 2>&1
git rev-list --count HEAD..origin/main 2>/dev/null || echo "0"
git rev-list --count origin/main..HEAD 2>/dev/null || echo "0"

# Recent upstream commits (if behind)
git log --oneline HEAD..origin/main 2>/dev/null | head -10
```

If on a feature branch (not main), also check PR status:

```bash
gh pr view --json state,number,title,mergeable,reviewDecision,statusCheckRollup 2>/dev/null || echo "NO_PR"
```

## Decision Tree and Actions

### On main branch

- **Behind origin/main** → `git pull`
- **Up to date** → Report ready to work

### On feature branch with PR

- **PR merged** → Switch to main, delete branch, pull:
  ```bash
  BRANCH=$(git branch --show-current)
  git checkout main
  git pull
  git branch -d "$BRANCH"
  ```
- **PR closed (not merged)** → Ask user: delete branch or keep working?
- **PR open** → Report status, show any review comments:
  ```bash
  gh pr view --json reviews,comments --jq '.reviews[-3:], .comments[-3:]'
  ```

### On feature branch without PR

- **Has commits ahead of main** → Ask if user wants to create PR
- **No commits ahead** → Ask if user wants to delete branch

### Uncommitted changes present

- **List them clearly** with `git status`
- **Ask user** what to do: commit, stash, or discard
- Do NOT automatically commit or discard

### Stashed changes present

- **List stashes** with `git stash list`
- **Inform user** they have stashed work

## Cleanup: Delete Merged Branches

After switching to main, clean up merged branches:

```bash
git branch --merged main | grep -v '^\*' | grep -v 'main' | while read branch; do
  echo "Deleting merged branch: $branch"
  git branch -d "$branch"
done
```

## Output Format

Summarize findings in a table:

| Check       | Status           | Action            |
| ----------- | ---------------- | ----------------- |
| Branch      | `feature-xyz`    | -                 |
| PR          | #123 MERGED      | Switching to main |
| Uncommitted | 2 files modified | Listed below      |
| Behind main | 5 commits        | Will pull         |

Then take the actions and report results.

## Safety Rules

- NEVER force push
- NEVER delete unmerged branches without asking
- NEVER commit uncommitted changes without user approval
- NEVER discard changes without explicit user confirmation
- Always preserve user's work
