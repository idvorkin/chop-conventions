---
name: background-usage
description: Check Claude Code plan usage via a hidden tmux session. Reports weekly usage percentage, time until reset, and pacing status.
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
  prompt: "Read and execute ~/gits/chop-conventions/skills/background-usage/_impl.md — follow all steps and return the one-line summary.",
  run_in_background: true
)
```

When the agent completes, relay its one-line summary to the user.
