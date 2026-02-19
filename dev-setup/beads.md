# Beads: Issue Tracking for AI Agents

> A lightweight, git-backed issue tracker designed for AI-assisted development workflows

## Why Beads?

Traditional issue trackers (GitHub Issues, Jira) weren't designed for AI agents. Beads provides:

- **Dependency tracking** with 4 types: `blocks`, `related`, `parent-child`, `discovered-from`
- **Ready work detection** - instantly find unblocked tasks with `bd ready`
- **Git-native sync** - issues stored in JSONL, versioned with your code
- **Agent-friendly** - `--json` output for programmatic access
- **Offline-first** - all queries run locally, sync when online

## Installation

```bash
# Install beads
brew install steveyegge/tap/beads
# or
go install github.com/steveyegge/beads@latest
```

## Quick Start

```bash
# Initialize in your project
bd init --prefix myproject

# Create issues
bd create --title="Implement feature X" --type=feature
bd create --title="Fix bug Y" --type=bug

# Add dependencies
bd dep add myproject-2 myproject-1  # myproject-2 blocked by myproject-1

# Find ready work
bd ready

# Claim and work
bd update myproject-1 --status in_progress --assignee claude
# ... do the work ...
bd close myproject-1 --reason "Implemented in commit abc123"

# Sync with git
bd sync
```

## Best Practices

### 1. Use All Four Dependency Types

| Type              | Use When                                | Example                                |
| ----------------- | --------------------------------------- | -------------------------------------- |
| `blocks`          | Work cannot start until blocker is done | "Tests blocked by feature impl"        |
| `related`         | Issues share context but don't block    | "Similar bug in another module"        |
| `parent-child`    | Epic/subtask hierarchy                  | "Auth epic → login task"               |
| `discovered-from` | Found during other work                 | "Bug found while implementing feature" |

**The `discovered-from` type is critical for agent workflows** - it maintains audit trails when agents discover new work during implementation:

```bash
# Working on swing-48b, found a bug
bd create --title="Found memory leak in cache"
bd dep add swing-new swing-48b --type discovered-from
```

### 2. Claim Work with Assignees

Prevent duplicate work in multi-agent setups:

```bash
# Claim work
bd update myproject-1 --status in_progress --assignee agent-name

# Find only your work
bd ready --assignee agent-name
bd list --assignee agent-name --status in_progress
```

### 3. Session Start Protocol

```bash
# 1. Check for work
bd ready

# 2. Review specific issue
bd show myproject-1

# 3. Claim it
bd update myproject-1 --status in_progress

# 4. Check blocked issues (might be unblocked now)
bd blocked
```

### 4. Session End Protocol

**Never skip this - work isn't done until pushed:**

```bash
# 1. Check what changed
git status

# 2. Stage code changes
git add <files>

# 3. Sync beads changes
bd sync

# 4. Commit code
git commit -m "feat: implement feature X"

# 5. Sync any new beads changes
bd sync

# 6. Push to remote
git push
```

### 5. Install Git Hooks for Zero-Lag Sync

Create `.githooks/pre-commit`:

```bash
#!/bin/bash
# Flush pending beads changes before commit
if command -v bd &> /dev/null && [ -d ".beads" ]; then
    bd sync --quiet 2>/dev/null || true
fi
```

Create `.githooks/post-merge`:

```bash
#!/bin/bash
# Import beads changes after merge/pull
if command -v bd &> /dev/null && [ -d ".beads" ]; then
    bd sync --quiet 2>/dev/null || true
fi
```

Enable hooks:

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/post-merge
```

### 6. Use a Dedicated Sync Branch

For team projects, configure a sync branch so beads changes don't pollute main:

```yaml
# .beads/config.yaml
sync-branch: "beads-metadata"
```

This keeps beads commits separate from code commits.

### 7. Periodic Compaction

Clean old closed issues to keep the database lean:

```bash
# Preview what would be removed
bd compact --days 30 --dry-run

# Actually compact
bd compact --days 30
```

### 8. Hierarchical IDs for Epics

Use dot notation for epic subtasks:

```bash
# Create epic
bd create --title="Auth System" --type=epic
# Creates: myproject-a3f

# Create subtasks
bd create --title="Design login UI" --id=myproject-a3f.1
bd create --title="Implement OAuth" --id=myproject-a3f.2
bd create --title="Add tests" --id=myproject-a3f.3
```

## CLAUDE.md Integration

Add this to your project's CLAUDE.md:

```markdown
## Task Tracking (Beads)

This project uses [beads](https://github.com/steveyegge/beads) for issue tracking.

**Core workflow:**

1. `bd ready` - Find unblocked work
2. `bd update ID --status in_progress` - Claim task
3. Do the work
4. `bd close ID --reason "why"` - Complete task
5. `bd sync` - Sync with git

**When discovering new work:**
\`\`\`bash
bd create --title="Found issue X"
bd dep add NEW-ID PARENT-ID --type discovered-from
\`\`\`

**Session end checklist:**

- [ ] `git status` - check changes
- [ ] `git add <files>` - stage code
- [ ] `bd sync` - sync beads
- [ ] `git commit` - commit code
- [ ] `git push` - push to remote
```

## Common Commands Reference

```bash
# Finding work
bd ready                    # Unblocked tasks
bd list --status=open       # All open issues
bd list --status=in_progress # Active work
bd blocked                  # See what's stuck

# Creating & updating
bd create --title="..." --type=task|bug|feature|epic|chore
bd update ID --status=in_progress
bd update ID --assignee=name
bd close ID --reason="explanation"
bd close ID1 ID2 ID3       # Close multiple at once

# Dependencies
bd dep add ISSUE DEPENDS_ON                    # Default: blocks
bd dep add ISSUE DEPENDS_ON --type=related
bd dep add ISSUE DEPENDS_ON --type=parent-child
bd dep add ISSUE DEPENDS_ON --type=discovered-from

# Inspection
bd show ID                  # Full issue details
bd stats                    # Project health metrics

# Sync
bd sync                     # Bidirectional git sync
bd sync --status            # Check sync status
```

## Troubleshooting

**Worktree error: "branch is already checked out"**

```
Error pulling from sync branch: failed to create worktree: exit status 128
fatal: 'main' is already checked out at '/path/to/repo'
```

Beads uses git worktrees for sync. If `sync.branch` equals your current branch, this fails. Fix:

```bash
git branch beads-sync main
git push -u origin beads-sync
bd config set sync.branch beads-sync
```

**"Blocked operation not yet supported via daemon"**

- Some operations require direct database access
- Use `bd --no-daemon <command>` as workaround

**Issues not syncing**

- Run `bd sync` manually
- Check `.beads/config.yaml` for sync-branch setting
- Ensure git hooks are installed

**Stale `in_progress` issues**

- Review with `bd list --status=in_progress`
- Close completed ones: `bd close ID --reason="done"`

## Claude Code Integration

### Status Line Configuration

Show current branch, in-progress issue ID, and truncated title in Claude Code's status line.

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "input=$(cat); cwd=$(echo \"$input\" | jq -r '.workspace.current_dir'); cwd_short=$(echo \"$cwd\" | sed \"s|^$HOME|~|\"); branch=\"\"; bd_issue=\"\"; if [ -d \"$cwd/.git\" ]; then branch=$(cd \"$cwd\" && git branch --show-current 2>/dev/null); fi; if command -v bd >/dev/null 2>&1 && [ -d \"$cwd/.beads\" ]; then bd_line=$(cd \"$cwd\" && bd list --status=in_progress 2>/dev/null | head -1); if [ -n \"$bd_line\" ]; then bd_id=$(echo \"$bd_line\" | awk '{print $1}'); bd_title=$(echo \"$bd_line\" | sed 's/.*- //' | cut -c1-30); bd_issue=\" [${bd_id}: ${bd_title}]\"; fi; fi; printf '\\033[01;32m%s@%s\\033[00m:\\033[01;34m%s\\033[00m \\033[33m(%s)\\033[00m%s' \"$(whoami)\" \"$(hostname -s)\" \"$cwd_short\" \"$branch\" \"$bd_issue\""
  }
}
```

**Requires:** `jq` installed, `bd` in PATH, project has `.beads/` directory.

### Session Hooks

Auto-prime beads context on session start and before compaction:

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [{ "type": "command", "command": "bd prime" }]
      }
    ],
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [{ "type": "command", "command": "bd prime" }]
      }
    ]
  }
}
```

## Resources

- [GitHub: steveyegge/beads](https://github.com/steveyegge/beads)
- [Beads FAQ](https://github.com/steveyegge/beads/blob/main/docs/FAQ.md)
- [Steve Yegge's Beads for Blobfish](https://steve-yegge.medium.com/beads-for-blobfish-80c7a2977ffa)
