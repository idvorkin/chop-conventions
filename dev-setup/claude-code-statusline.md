# Claude Code Status Line

A standalone POSIX `sh` script that renders a colored, information-dense status line for Claude Code.

## Why This Exists

Claude Code's `statusLine` setting accepts an inline command, but inline JSON-quoted shell becomes unreadable fast (see `beads.md` § Status Line Configuration for an example). A standalone script is easier to maintain, easier to test (`echo '{...}' | sh statusline-command.sh`), and trivially diff-able when iterating on format.

## What It Shows

```text
user@host ~/path/to/repo [branch] | Opus 4.6 ctx:12% 125k/1000k $0.42
```

| Segment         | Source                                                  | Color                                            |
| --------------- | ------------------------------------------------------- | ------------------------------------------------ |
| `user@host`     | `whoami` / `hostname -s`                                | default                                          |
| `~/path`        | `cwd` (or `workspace.current_dir`), home shortened      | yellow                                           |
| `[branch]`      | `git symbolic-ref --short HEAD` (skipped if not a repo) | yellow                                           |
| `Opus 4.6`      | `model.display_name`                                    | default                                          |
| `ctx:NN% Xk/Yk` | `context_window.{used_percentage, context_window_size}` | green <200k, yellow <400k, pink <600k, red ≥600k |
| `$X.XX`         | `cost.total_cost_usd`                                   | default                                          |

The token bucket (`Xk`) snaps to the nearest 10k so the number doesn't jitter on every tick. The total (`Yk`) comes from `context_window.context_window_size` directly — no model-string heuristics, so Sonnet (200k) and Opus `[1m]` render correctly without code changes.

## Install

```bash
cp dev-setup/statusline-command.sh ~/.claude/statusline-command.sh
chmod +x ~/.claude/statusline-command.sh
```

Then add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "sh $HOME/.claude/statusline-command.sh"
  }
}
```

## Requirements

- `jq` on PATH (every field is read via `jq -r`)
- POSIX `sh` (no bashisms)
- Terminal that renders ANSI color escapes (every modern terminal)

## Testing

The script reads JSON from stdin, so it's trivial to exercise without launching Claude Code:

```bash
echo '{
  "cwd": "/home/you/project",
  "model": {"display_name": "Opus 4.6", "id": "claude-opus-4-6[1m]"},
  "context_window": {"used_percentage": 12.5, "context_window_size": 1000000},
  "cost": {"total_cost_usd": 0.42}
}' | sh dev-setup/statusline-command.sh
```

Vary `used_percentage` across `15`, `25`, `45`, `65` (on a 1M context window) to see all four color tiers.

## Customizing the Color Tiers

The thresholds live near the bottom of the script and are expressed in **absolute used tokens**, not percentages — so a 400k cutoff means "400k tokens" regardless of whether the model has a 200k or 1M window:

```sh
used_tokens=$(awk "BEGIN {printf \"%d\", $used * $ctx_size / 100}")
if [ "$used_tokens" -lt 200000 ]; then
  ctx_color=$GREEN
elif [ "$used_tokens" -lt 400000 ]; then
  ctx_color=$YELLOW
elif [ "$used_tokens" -lt 600000 ]; then
  ctx_color=$PINK
else
  ctx_color=$RED
fi
```

Adjust the `200000` / `400000` / `600000` boundaries to taste. Absolute thresholds are tuned for the 1M Opus window — on a 200k-context model (Sonnet/Haiku) you'll sit at yellow almost immediately, which is intentional: 200k is actually tight. Switch to percentage-based tiers if you spend most of your time on narrower-context models.

## Available Fields (Reference)

The full JSON schema piped in by Claude Code includes a lot more than this script uses — `session_id`, `transcript_path`, `cost.total_lines_added`, `rate_limits.five_hour.used_percentage`, `agent.name`, `vim.mode`, and more. See https://code.claude.com/docs/en/statusline.md for the complete reference. Drop additional segments into this script as needed.

## Related

- `beads.md` § Status Line Configuration — an inline alternative that surfaces the in-progress beads issue. Prefer this script if you don't need the bd integration; combine the two if you do.
