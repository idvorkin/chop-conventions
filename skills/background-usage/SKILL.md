---
name: background-usage
description: Capture the Claude Code /usage dialog in a throwaway background session — a herdr workspace under herdr, otherwise a hidden detached tmux session — and return the pane verbatim. It parses nothing: the caller reads the capture and decides what the numbers mean.
allowed-tools: Agent
---

# Background Usage Check

Check your Claude Code plan usage without blocking your current session.

**Always dispatch this to a background Agent subagent.** Do NOT run the steps inline.

## How to run

Spawn a background Agent that reads and executes `_impl.md` in this skill's directory:

```
Agent(
  description: "Check usage",
  prompt: "Read and execute ~/.claude/skills/background-usage/_impl.md — follow all steps and return the one-line summary.",
  run_in_background: true
)
```

When the agent completes, relay its one-line summary to the user.

## What comes back

The `/usage` pane, verbatim, in a fenced block under a line naming the file and
its timestamp:

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

**No percentage is extracted, no pacing is judged, and no `last.json` is
written.** Igor on PR #212: "No — drop the python processing and just return
raw and then the calling agent can process the raw."

So the caller owns the reading. Whatever it needs — which block governs the
plan, whether the second one is Sonnet or Fable this week, whether the screen
said "used" or "left", how much of the window has elapsed — it decides from the
text, with the text still in hand. A skill that answered those questions itself
would hand back a bare number with nothing to check it against, which is how a
`Sonnet` line reached the Cockpit masthead labelled "Fable".

## What it leaves behind

Two files in `~/tmp/agent/skill/usage/`, both rewritten every run:

| File           | What it is                                                   |
| -------------- | ------------------------------------------------------------ |
| `raw.txt`      | the `/usage` pane, verbatim — the same text that is returned |
| `raw.ansi.txt` | the same pane with colour codes (tmux path only)             |

igor2's Cockpit reads `raw.txt` directly, serving it as `GET /usage/raw` behind
the usage tile's `raw` control — so the screen behind the tile's numbers is one
tap away. The tile's numbers themselves come from `last.json`, which the
**caller** writes; see the usage cron in that repo's
`.claude/commands/startup-larry.md` for the contract.
