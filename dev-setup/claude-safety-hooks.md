# Claude Code Safety Hooks

Mechanical enforcement of [guardrails](../dev-inner-loop/guardrails.md). Instructions alone don't prevent accidents—hooks do.

## Why This Exists

AI agents can execute destructive commands without understanding consequences. Even with explicit instructions forbidding dangerous operations, agents make mistakes. This hook blocks destructive commands **before they run**.

## Quick Install

### Project-local (recommended for teams)

```bash
# Copy from chop-conventions (adjust path as needed)
mkdir -p .claude/hooks
cp /path/to/chop-conventions/dev-setup/hooks/git_safety_guard.py .claude/hooks/
chmod +x .claude/hooks/git_safety_guard.py
```

Then add to `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/git_safety_guard.py"
          }
        ]
      }
    ]
  }
}
```

### Global (protects all projects)

Same steps but use `~/.claude/` instead of `.claude/` and `$HOME/.claude/hooks/...` in settings.

## Commands Blocked

| Command                   | Why Dangerous                            |
| ------------------------- | ---------------------------------------- |
| `git checkout -- <files>` | Discards uncommitted changes permanently |
| `git restore <files>`     | Same as checkout (newer syntax)          |
| `git reset --hard`        | Destroys all uncommitted changes         |
| `git clean -f`            | Removes untracked files permanently      |
| `git push --force` / `-f` | Destroys remote history                  |
| `git branch -D`           | Force-deletes without merge check        |
| `rm -rf` (non-temp paths) | Recursive deletion                       |
| `git stash drop/clear`    | Permanently deletes stashed changes      |

## Commands Allowed

These look dangerous but are safe:

| Command                      | Why Safe                               |
| ---------------------------- | -------------------------------------- |
| `git checkout -b <branch>`   | Creates new branch, no file changes    |
| `git restore --staged`       | Only unstages, doesn't discard changes |
| `git clean -n` / `--dry-run` | Preview only                           |
| `rm -rf /tmp/...`            | Temp dirs are ephemeral by design      |

## What Happens When Blocked

```
BLOCKED by git_safety_guard.py

Reason: git checkout -- discards uncommitted changes permanently. Use 'git stash' first.

Command: git checkout -- file.txt

If this operation is truly needed, ask the user for explicit permission and have them run the command manually.
```

The command never executes. The agent sees this and should ask for help.

## Testing

```bash
# Should be blocked
echo '{"tool_name": "Bash", "tool_input": {"command": "git checkout -- file.txt"}}' | \
  python3 .claude/hooks/git_safety_guard.py

# Should pass (no output)
echo '{"tool_name": "Bash", "tool_input": {"command": "git status"}}' | \
  python3 .claude/hooks/git_safety_guard.py
```

## Important Notes

- **Restart required**: Claude Code snapshots hooks at startup
- **Not foolproof**: Regex matching can be bypassed with obfuscation—this catches honest mistakes, not malicious intent
- **Extend as needed**: Edit `DESTRUCTIVE_PATTERNS` in the script to add more blocked commands

## Attribution

Adapted from [misc_coding_agent_tips_and_scripts](https://github.com/Dicklesworthstone/misc_coding_agent_tips_and_scripts) by Jeffrey Emanuel, based on a real incident where an AI agent destroyed hours of uncommitted work.
