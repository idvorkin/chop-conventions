---
name: background-usage
description: Check Claude Code plan usage via a hidden tmux session. Reports weekly usage percentage, time until reset, and pacing status.
allowed-tools: Agent
---

# Background Usage Check

Check your Claude Code plan usage without blocking your current session.

**Always dispatch this to a background Agent subagent.** Do NOT run the steps inline.

## Cost & cadence

Each invocation spawns a hidden tmux Claude session and costs **~40k tokens**. Pick a cadence accordingly:

| Session type | Recommended cadence |
| --- | --- |
| Default / coaching | `/clock 12h /background-usage` |
| Leak-hunting an unexpected spike | `/clock 4h /background-usage` |
| Deep-work batch running overnight | Skip unless actively pacing |

At 4h cadence (six fires/day), monitoring alone is ~240k tokens/day — a meaningful slice of the weekly cap. Default to 12h; only drop to 4h when actively hunting a burn-rate anomaly.

## How to run

Spawn a background Agent that reads and executes `_impl.md` in this skill's directory:

```
Agent(
  description: "Check usage",
  prompt: "Read and execute ~/gits/chop-conventions/skills/background-usage/_impl.md — follow all steps and return the one-line summary.",
  run_in_background: true
)
```

When the agent completes, relay its one-line summary to the user.
