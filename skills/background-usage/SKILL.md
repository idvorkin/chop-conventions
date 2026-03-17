---
name: background-usage
description: Check Claude Code plan usage via a hidden tmux session. Reports weekly usage percentage, time until reset, and pacing status.
allowed-tools: Bash, Read
---

# Background Usage Check

Check your Claude Code plan usage without leaving your current session. Spawns a hidden tmux session, captures the `/usage` output, and reports a summary.

## Execution Mode

**Always run this skill as a background Agent subagent** (`run_in_background: true`). The tmux polling takes 10-30 seconds and should never block the main conversation. When triggered by a cron job or manually, dispatch via the Agent tool with `run_in_background: true` and report the one-line summary when the agent completes.

## Steps

### 1. Clean up any stale session

```bash
tmux kill-session -t cc-usage-check 2>/dev/null || true
```

### 2. Spawn hidden Claude session

```bash
tmux new-session -d -s cc-usage-check "claude"
```

### 3. Wait for Claude to start

Poll until the Claude prompt appears (look for the `❯` character):

```bash
for i in $(seq 1 20); do
  if tmux capture-pane -t cc-usage-check -p 2>/dev/null | grep -q '❯'; then
    echo "Claude ready after ${i}s"
    break
  fi
  sleep 1
done
```

If Claude is not ready after 20 seconds, clean up and report an error.

### 4. Send /usage and wait for output

```bash
tmux send-keys -t cc-usage-check '/usage' Enter
```

Poll until the usage dialog appears (look for "% used"):

```bash
for i in $(seq 1 30); do
  if tmux capture-pane -t cc-usage-check -p 2>/dev/null | grep -q '% used'; then
    echo "Usage dialog ready after ${i}s"
    break
  fi
  sleep 1
done
```

### 5. Capture and clean up

```bash
tmux capture-pane -t cc-usage-check -p > /tmp/cc-usage-output.txt
tmux kill-session -t cc-usage-check 2>/dev/null || true
```

**Always kill the session**, even if earlier steps failed. If any step above errored, run the kill command before reporting the error.

### 6. Read and parse the output

Read `/tmp/cc-usage-output.txt` and extract ONLY the "Current week (all models)" section. Ignore "Current session" and "Current week (Sonnet only)" lines.

From that section, extract:
- **Usage percentage**: the number before "% used"
- **Reset date/time**: from the "Resets" line (e.g., "Resets Mar 21, 3pm (UTC)")

### 7. Calculate time remaining

Get the current UTC time for comparison:

```bash
date -u '+%Y-%m-%d %H:%M UTC'
```

From the reset date/time (which is in UTC), calculate hours remaining from now.

- If >= 48 hours remaining: report as "N days"
- If < 48 hours remaining: report as "N hours"

### 8. Calculate pacing

Determine what percentage of the weekly period has elapsed (the period is 7 days, use the reset time to work backward to find the start).

- `time_elapsed_pct` = percentage of the 7-day period that has passed
- `usage_pct` = the usage percentage from step 6

Pacing:
- If `usage_pct <= time_elapsed_pct` → "On track"
- If `usage_pct > time_elapsed_pct` but `usage_pct < 2 * time_elapsed_pct` → "Burning fast"
- If `usage_pct >= 2 * time_elapsed_pct` → "Slow down"

### 9. Report

Output a single-line summary:

> **Usage: N% used | X days until reset | On track**

Examples:
> **Usage: 9% used | 4 days until reset | On track**
> **Usage: 65% used | 18 hours until reset | Burning fast**
> **Usage: 40% used | 6 days until reset | Slow down**

If capture failed or output was empty:
> **Usage: ERROR — could not capture /usage output. The cc-usage-check tmux session has been cleaned up.**

## Safety

- Always kill the `cc-usage-check` tmux session when done, even on failure
- If the tmux session already exists, kill it first before creating a new one
- If capture fails or output is empty, report the error template above instead of guessing
- This skill assumes `claude` is on PATH and the user is already authenticated
