---
name: ammon
description: What time is it for Ammon? Looks up the current time in Denmark (Europe/Copenhagen timezone) and reports it.
allowed-tools: Bash
---

# What Time Is It for Ammon?

Ammon is in Denmark. Look up the current time there and tell the user.

## Steps

Run both timezone lookups together:

```bash
echo "Denmark: $(TZ='Europe/Copenhagen' date '+%H:%M %Z (%A, %B %d, %Y)')"
echo "PST:     $(TZ='America/Los_Angeles' date '+%I:%M %p %Z (%A, %B %d, %Y)')"
```

**Important:** The machine may report in UTC. Always use the explicit `TZ=` commands above to get the correct local times — never trust the system clock's default timezone.

## Output Format

Report both times conversationally, e.g.:

> It's 14:30 CET for Ammon in Denmark (Wednesday, January 15, 2025).
> That's 5:30 AM PST for you.

If Ammon's time is outside normal waking hours (before 07:00 or after 23:00), mention that he's likely asleep.
