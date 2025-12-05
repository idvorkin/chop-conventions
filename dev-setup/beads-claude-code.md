# Beads + Claude Code Integration

Best practices for using [beads](https://github.com/steveyegge/beads) issue tracking with Claude Code.

## Status Line Configuration

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

### What it shows

```
developer@host:~/gits/myproject (feature-branch) [proj-123: Fix login bug]
```

- **Green:** `user@host`
- **Blue:** Working directory (with `~` for home)
- **Yellow:** Current git branch
- **White:** `[issue-id: truncated-title]` (if beads issue is in_progress)

### Requirements

- `jq` installed (for parsing JSON input)
- `bd` (beads CLI) in PATH
- Project has `.beads/` directory initialized

## Session Hooks

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

## Beads Workflow in Claude Code

### Starting work

```bash
bd ready              # Find available work (no blockers)
bd show <id>          # Review issue details
bd update <id> --status=in_progress  # Claim it
```

### During work

- Status line shows current issue automatically
- Use `bd comment <id> "progress notes"` to track progress
- Create dependent issues with `bd create` + `bd dep add`

### Completing work

```bash
bd close <id> --reason="description of fix"
bd sync               # Push to remote
```

### Session close checklist

Before ending a session, always run:

```bash
git status            # Check what changed
git add <files>       # Stage code changes
bd sync               # Commit beads changes
git commit -m "..."   # Commit code
git push              # Push to remote
```

## Enabling the Beads Plugin

Add to `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "beads@beads-marketplace": true
  }
}
```

This enables `/beads:*` slash commands in Claude Code.
