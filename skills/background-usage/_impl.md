# Background Usage — Implementation

This file is executed by a subagent. Do NOT read this in the main context.

`/usage` exists only inside the Claude Code TUI — there is no `claude usage`
subcommand — so the only way to read it is to drive a throwaway Claude session
in a second terminal and scrape the rendered dialog. Which multiplexer does the
driving depends on where this session is running.

**This skill parses nothing.** Igor on PR #212: "No — drop the python
processing and just return raw and then the calling agent can process the
raw." Its whole job is to drive `/usage` in a throwaway session, capture the
pane verbatim, and hand the text back. No percentages are extracted, no pacing
is judged, no `last.json` is written. The caller does all of that.

That division is the point. Reading a dialog and interpreting a dialog are
different jobs with different failure modes: `50% used` and `50% left` are the
same digits and opposite facts, and when the interpretation lives in here, an
inverted reading arrives downstream as a bare number with nothing to check it
against. Returning the screen instead means whoever needs an answer can always
see what the answer was derived from — and re-derive it when the screen changes
shape.

## Files this run writes

Both live in `~/tmp/agent/skill/usage/`:

| File           | What it is                                                    |
| -------------- | ------------------------------------------------------------- |
| `raw.txt`      | the pane, verbatim — the capture, and what the report returns |
| `raw.ansi.txt` | the same pane with colour codes; tmux only, and only because  |
|                | it is one extra command on a pane already open                |

```bash
mkdir -p ~/tmp/agent/skill/usage
```

Both are rewritten on every run. Leaving a stale `raw.txt` behind is worse than
failing loudly: a caller that reads the file rather than the returned text
would interpret last run's screen and stamp it with this run's clock.

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
- `none` → skip to step 1 and use the failure template.

Both paths end with the rendered dialog in `~/tmp/agent/skill/usage/raw.txt`.

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
herdr pane read <pane_id> --source visible --lines 60 > ~/tmp/agent/skill/usage/raw.txt
```

Match on `Current week`, not `% used` — the `Current session` line also ends in
`% used` and can render first.

**Scroll until both weekly blocks are on screen.** The dialog is taller than
the pane, and the model-specific block — with its own `Resets` line — is the
part that falls below the fold. Send `down` until the SECOND `Current week`
block and the `Resets` line under it are both visible:

```bash
herdr pane read <pane_id> --source visible | grep -c 'Resets'   # want >= 3
herdr pane send-keys <pane_id> down
```

**The stop condition is those lines, not the `↓` marker.** Measured on a real
run 2026-08-28: the marker is still there once both weekly blocks are up,
because a "What's contributing to your limits usage?" section sits below them
and never has to be captured. Looping until the arrow disappears scrolls
straight past the numbers. Cap it at ~6 downs and capture what you have.

This is a completeness problem, not a parsing one: whatever is below the fold
never reaches the caller at all, and nothing downstream can tell the difference
between a block that was absent and a block that was merely unscrolled.

`herdr pane read` returns plain text, so there is no `raw.ansi.txt` on this
path. Delete any stale one rather than leaving last run's colours next to this
run's capture:

```bash
rm -f ~/tmp/agent/skill/usage/raw.ansi.txt
```

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

**Scroll until both weekly blocks are on screen**, for the reason given in A4 —
and with the same stop condition, which is the `Resets` lines and not the `↓`:

```bash
for i in $(seq 1 6); do
  [ "$(tmux capture-pane -t cc-usage-check -p | grep -c 'Resets')" -ge 3 ] && break
  tmux send-keys -t cc-usage-check Down
  sleep 1
done
```

Three `Resets` lines means session + both weekly blocks are up. Two means the
model-specific block is still below the fold; on an account that renders no
such block it will stay at two, which is why this is capped rather than a
`while`.

### B5. Capture, twice, then always clean up

Both captures come off the SAME pane before it is killed — capture first, kill
after, or the second one reads a session that no longer exists:

```bash
tmux capture-pane -t cc-usage-check -p  > ~/tmp/agent/skill/usage/raw.txt
tmux capture-pane -t cc-usage-check -e -p > ~/tmp/agent/skill/usage/raw.ansi.txt
tmux kill-session -t cc-usage-check 2>/dev/null || true
```

`-p` is the plain text everything downstream reads; `-e` keeps the colour
codes, which is the only copy that still shows which bar was drawn in which
colour if that ever turns out to matter.

**Always kill the session**, even if earlier steps failed.

---

## Step 1 — Return the capture

**The capture IS the result.** Return the contents of `raw.txt` verbatim, in a
fenced block, under one line naming where it came from and when:

    /usage capture — ~/tmp/agent/skill/usage/raw.txt — 2026-08-28 07:44:02 -0700

    ```
       Current session
       ██████████████▍                                    29% used
       Resets 3:20pm (UTC)

       Current week (all models)
       ████████████████████████████                       56% used
       Resets Sep 1, 5am (UTC)

       Current week (Fable)
       █████████████████████                              42% used
    ```

The timestamp comes from the file, not from a guess:

```bash
stat -c '%y' ~/tmp/agent/skill/usage/raw.txt
```

Three rules about that block:

- **Verbatim means verbatim.** Do not tidy the bars, re-align the columns, drop
  the blank rows, or trim the `Current session` block because it looks
  irrelevant. Whichever line the caller cares about is a line you cannot know
  in advance, and a "cleaned" capture is no longer evidence of anything.
- **Do not summarise it, and do not lead with a number.** No "Usage: 56% used",
  no pacing verdict, no "you are ahead of schedule". Extracting one figure and
  putting it at the top is exactly the interpretation this skill no longer does
  — and the figure you chose would quietly become the answer.
- **Say nothing the screen did not.** If the dialog rendered no model-specific
  block, that absence is part of the capture; do not remark on it, explain it,
  or fill it in.

The pane is short — around twenty-five lines — so returning all of it costs
almost nothing and leaves the caller with everything it needs.

### If it did not work

The failures are the one place this skill still speaks in its own voice,
because a caller cannot parse a capture that does not exist:

> **Usage capture FAILED — neither herdr nor tmux is available, so /usage could not be driven.**

> **Usage capture FAILED — the dialog never rendered (no "Current week" in the pane after Ns). The throwaway session has been cleaned up.**

> **Usage capture FAILED — the capture came back empty. The throwaway session has been cleaned up.**

Never substitute a remembered number, and never fall back to reading an older
`raw.txt`: a stale capture returned as a fresh one is the failure mode the
whole design exists to make impossible.

## Safety

- Always tear down the throwaway session — `herdr workspace close` or
  `tmux kill-session` — even on failure.
- Under herdr, only ever close the workspace this run created. Read its id from
  the `workspace create` response; never close one you did not create.
- Under tmux, kill a pre-existing `cc-usage-check` session before creating a new one.
- If the capture fails or comes back empty, return the failure template above.
  Never a remembered number, and never an older `raw.txt`.
- Never leave last run's `raw.txt` in place after a failed capture. The screen
  it shows is not the screen this run saw.
- Never interpret. No percentages, no pacing, no `last.json` — that is the
  caller's job, and doing it here is how a wrong reading loses its receipt.
- Assumes `claude` is on PATH and the user is already authenticated.
