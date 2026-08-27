# Background Usage — Implementation

This file is executed by a subagent. Do NOT read this in the main context.

`/usage` exists only inside the Claude Code TUI — there is no `claude usage`
subcommand — so the only way to read it is to drive a throwaway Claude session
in a second terminal and scrape the rendered dialog. Which multiplexer does the
driving depends on where this session is running.

## Step 0 — Pick the multiplexer

```bash
if [ "${HERDR_ENV:-}" = 1 ]; then
  echo herdr
elif command -v tmux >/dev/null 2>&1; then
  echo tmux
else
  echo none
fi
```

- `herdr` → run **Path A**.
- `tmux` → run **Path B**. A detached tmux session works even when the caller is
  not inside tmux, so this is also the fallback for a plain terminal.
- `none` → skip to step 3 and report the error template.

Both paths end with the rendered dialog in `/tmp/cc-usage-output.txt`.

---

## Path A — herdr

### A1. Create an unfocused workspace

```bash
herdr workspace create --label cc-usage-check --cwd "$PWD" --no-focus
```

Read `.result.workspace.workspace_id` (e.g. `w5`) and `.result.root_pane.pane_id`
(e.g. `w5:p1`) out of the JSON response. Do not guess or derive them.

A separate workspace — rather than a split in the caller's tab — leaves the
user's layout untouched. Herdr has no detached/hidden session concept, so the
workspace is visible in the sidebar for the ~60–90s the check runs.

### A2. Start Claude in it

```bash
herdr agent start usage-check --kind claude --pane <pane_id> --timeout 90000
```

`agent start` returns only once Herdr sees the agent interactive-ready, so no
prompt polling is needed here.

### A3. Open the usage dialog

```bash
herdr pane run <pane_id> "/usage"
herdr pane send-keys <pane_id> enter
```

**Two Enters are required.** `pane run` sends the text plus one Enter
atomically, but that first Enter is swallowed by Claude's slash-command
autocomplete menu — it closes the menu without submitting. Without the second
`send-keys ... enter` the prompt sits at `❯ /usage` and the wait in A4 times
out.

### A4. Wait, then capture

```bash
herdr pane wait-output <pane_id> --match "Current week" --source visible --timeout 45000
herdr pane read <pane_id> --source visible --lines 60 > /tmp/cc-usage-output.txt
```

Match on `Current week`, not `% used` — the `Current session` line also ends in
`% used` and can render first.

### A5. Always clean up

```bash
herdr workspace close <workspace_id>
```

Run this even if A2–A4 failed, then confirm the id is gone from
`herdr workspace list`. Never close a workspace you did not create.

---

## Path B — tmux

### B1. Clean up any stale session

```bash
tmux kill-session -t cc-usage-check 2>/dev/null || true
```

### B2. Spawn a hidden detached session

```bash
tmux new-session -d -s cc-usage-check "claude"
```

### B3. Wait for Claude to start

Poll until the prompt appears (look for the `❯` character):

```bash
for i in $(seq 1 20); do
  if tmux capture-pane -t cc-usage-check -p 2>/dev/null | grep -q '❯'; then
    echo "Claude ready after ${i}s"
    break
  fi
  sleep 1
done
```

If Claude is not ready after 20 seconds, clean up (B5) and report an error.

### B4. Send /usage and wait

```bash
tmux send-keys -t cc-usage-check '/usage' Enter
tmux send-keys -t cc-usage-check Enter
```

The second Enter is for the same autocomplete-menu reason described in A3.

Poll until the dialog appears:

```bash
for i in $(seq 1 30); do
  if tmux capture-pane -t cc-usage-check -p 2>/dev/null | grep -q 'Current week'; then
    echo "Usage dialog ready after ${i}s"
    break
  fi
  sleep 1
done
```

### B5. Capture and always clean up

```bash
tmux capture-pane -t cc-usage-check -p > /tmp/cc-usage-output.txt
tmux kill-session -t cc-usage-check 2>/dev/null || true
```

**Always kill the session**, even if earlier steps failed.

---

## Step 1 — Parse the captured dialog

Read `/tmp/cc-usage-output.txt` and extract ONLY the "Current week (all models)"
section. Ignore "Current session" and "Current week (Sonnet only)" lines.

From that section, extract:

- **Usage percentage**: the number before "% used"
- **Reset date/time**: from the "Resets" line, e.g. `Resets Aug 31, 10pm (America/Los_Angeles)`

If the file is empty or neither field is present, report the error template in
step 3.

## Step 2 — Time remaining and pacing

The `Resets` line carries its own timezone in parentheses. **It is not UTC** —
it renders in the machine's local zone. Read the zone out of the line and do all
arithmetic in that zone:

```bash
TZ="<zone from the Resets line>" date '+%Y-%m-%d %H:%M'
```

The reset line has no year. Assume the current year in that zone; if that places
the reset in the past, roll forward one year.

Time remaining = reset − now:

- `>= 48` hours remaining → report as "N days"
- `< 48` hours remaining → report as "N hours"

Pacing — the weekly window is 7 days, so it began at `reset − 7 days`:

- `time_elapsed_pct` = `(now − (reset − 7d)) / 7d × 100`
- `usage_pct` = the percentage from step 1

Then:

- `usage_pct <= time_elapsed_pct` → "On track"
- `usage_pct > time_elapsed_pct` but `< 2 × time_elapsed_pct` → "Burning fast"
- `usage_pct >= 2 × time_elapsed_pct` → "Slow down"

## Step 3 — Report

Return a single-line summary:

> **Usage: N% used | X days until reset | On track**

Examples:

> **Usage: 9% used | 4 days until reset | On track**
> **Usage: 65% used | 18 hours until reset | Burning fast**
> **Usage: 40% used | 6 days until reset | Slow down**

If no multiplexer was available:

> **Usage: ERROR — neither herdr nor tmux is available, so /usage could not be driven.**

If capture failed or the output was empty:

> **Usage: ERROR — could not capture /usage output. The throwaway session has been cleaned up.**

## Safety

- Always tear down the throwaway session — `herdr workspace close` or
  `tmux kill-session` — even on failure.
- Under herdr, only ever close the workspace this run created. Read its id from
  the `workspace create` response; never close one you did not create.
- Under tmux, kill a pre-existing `cc-usage-check` session before creating a new one.
- If capture fails or output is empty, report the error template above instead
  of guessing a number.
- Assumes `claude` is on PATH and the user is already authenticated.
