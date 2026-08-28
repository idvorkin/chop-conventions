---
name: background-usage
description: Check Claude Code plan usage in a throwaway background session — a herdr workspace under herdr, otherwise a hidden detached tmux session. Reports the all-models weekly usage percentage, the separately-metered per-model percentage when the dialog shows one, time until reset, and pacing status.
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

> **Usage: 46% used (Fable 24%) | 4 days until reset | Burning fast**

The first percentage is `Current week (all models)` — the number that governs
the plan, and the one pacing is judged against. The parenthetical is the
`Current week (<Model> only)` line the dialog renders for whichever model is
metered separately on this account; it is **labelled exactly as the dialog
labels it** (`Sonnet`, `Opus`, `Fable`, …), which is not necessarily the model
the calling session runs. Some accounts render no such line — then the
parenthetical is simply absent, never a placeholder or a guess.
