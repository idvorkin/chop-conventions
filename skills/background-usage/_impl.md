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

Read `/tmp/cc-usage-output.txt`. The dialog renders up to three blocks:
`Current session`, `Current week (all models)`, and a model-specific
`Current week (<Model>)` — which has also rendered as
`Current week (<Model> only)`, so match on the parenthetical not being
`all models` rather than on the word "only". Ignore `Current session` —
**both** weekly blocks matter.

The model-specific block may be one line below the fold. The pane capture ends
with a `↓` scroll marker when there is more; send `Down` a few times and
re-capture, or its `Resets` line goes missing.

From `Current week (all models)` extract:

- **`usage_pct`**: the number before "% used"
- **Reset date/time**: from the "Resets" line, e.g. `Resets Aug 31, 10pm (America/Los_Angeles)`

From the model-specific block, when the dialog renders it, extract:

- **`model_name`**: the model named inside the parentheses, copied
  **exactly as the dialog labels it**. Which model gets metered separately
  varies — it has rendered as `Sonnet`, `Opus`, and `Fable` at different times —
  and it is NOT necessarily the model the calling session is running. If the
  dialog says `Sonnet`, report `Sonnet`, even from a Fable session. Guessing
  here is how a Sonnet number ends up mislabelled as Fable on a dashboard.
- **`model_pct`**: the number before "% used" inside that same block.

The model-specific block is optional; some accounts render only the all-models
line. When it is absent, report `model_name` / `model_pct` as unknown — never
reuse the all-models number for it.

Grepping both blocks in one pass keeps the two percentages from being transposed:

```bash
grep -nE 'Current (session|week)|% used|Resets' /tmp/cc-usage-output.txt
```

If the file is empty, or the all-models percentage and reset time are both
absent, report the error template in step 3.

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
- `usage_pct` = the **all-models** percentage from step 1 (pacing is judged on
  the number that actually governs the plan, never on `model_pct`)

Then:

- `usage_pct <= time_elapsed_pct` → "On track"
- `usage_pct > time_elapsed_pct` but `< 2 × time_elapsed_pct` → "Burning fast"
- `usage_pct >= 2 × time_elapsed_pct` → "Slow down"

## Step 3 — Report

Return a single-line summary. The all-models percentage, reset countdown and
pacing keep their existing positions and wording; the model-specific reading is
**appended** to the percentage as a parenthetical `<Model> M%`:

> **Usage: N% used (<Model> M%) | X days until reset | On track**

When the dialog carried no model-specific block, drop the parenthetical
entirely rather than printing a placeholder or a guessed model name:

> **Usage: N% used | X days until reset | On track**

Examples:

> **Usage: 9% used (Sonnet 4%) | 4 days until reset | On track**
> **Usage: 46% used (Fable 24%) | 4 days until reset | Burning fast**
> **Usage: 65% used | 18 hours until reset | Burning fast**
> **Usage: 40% used (Opus 31%) | 6 days until reset | Slow down**

Callers that persist the reading — e.g. the Cockpit usage tile at
`~/tmp/agent/skill/usage/last.json`, served by `decision_queue/serve.py` as
`GET /usage` — want the two numbers kept apart: the all-models percentage in
`weekly_pct`, and `model_pct` + `model_name` as their own fields. Write the
model-specific percentage only under the name the dialog actually printed.

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
- Never rename the model-specific line. The label comes from the dialog, not
  from the model this session is running — a `Sonnet` block reported as "Fable"
  is a wrong number wearing the right name, which is worse than no number at
  all.
- Assumes `claude` is on PATH and the user is already authenticated.
