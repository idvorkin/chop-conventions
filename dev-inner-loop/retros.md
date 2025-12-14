# Retrospectives

Run retrospectives periodically (weekly recommended) to review human-AI collaboration patterns and improve workflows.

## When to Run

- Weekly on a set day
- When user says "retro" or "workflow review"
- At end of significant project milestones

## Storage

```
retros/
  _retro_state.json    # Tracks last run date, covers all project instances
  YYYY-MM-DD.md        # Individual retro reports
```

Store at project root, not under `.claude/` - retros are project artifacts worth version controlling.

## Data Sources

| Source                                      | What to Extract                                   |
| ------------------------------------------- | ------------------------------------------------- |
| Claude logs (`~/.claude/history.jsonl`)     | User messages, corrections, friction patterns     |
| Claude stats (`~/.claude/stats-cache.json`) | Daily message/session/tool counts                 |
| Git history                                 | Commits, branches, PRs merged                     |
| Beads (if used)                             | Issues closed, cycle time, discovered-from chains |

## Gathering Data

**IMPORTANT: Use subagents to analyze Claude log files.** The log files (`~/.claude/history.jsonl`) can be very large and will blow your context if you read them directly. Spawn a subagent to analyze the logs and return a summary.

```bash
# Usage stats for last week (small file, safe to read directly)
cat ~/.claude/stats-cache.json | jq '.dailyActivity[] | select(.date >= "YYYY-MM-DD")'

# Git activity (safe to run directly)
git log --since="1 week ago" --oneline | wc -l
```

**For log analysis, use a subagent:**

```
Use the Task tool with subagent_type="general-purpose" to analyze ~/.claude/history.jsonl:
- Filter for messages since [date]
- Extract friction patterns (user corrections starting with "no", "wrong", etc.)
- Count messages by project
- Return a summary, not the raw data
```

Example subagent prompt:

> "Analyze ~/.claude/history.jsonl for the past week. Find friction patterns where user corrected the agent (messages starting with 'no', 'wrong', 'try again', 'not what'). Count messages per project. Return a summary table of findings, not raw data."

## Retro Template

```markdown
# Retro: YYYY-MM-DD

## Summary Metrics

- Messages: X
- Sessions: X
- Commits: X

## What Went Well

- [Features delivered, PRs merged, process wins]

## What Didn't Go Well

- [Friction patterns, rework, confusion]

## Friction Analysis

| Pattern   | Count | Example          | Root Cause        |
| --------- | ----- | ---------------- | ----------------- |
| [Pattern] | X     | "No, I meant..." | [Why it happened] |

## Workflow Recommendations

| Pattern               | Recommendation | Status          |
| --------------------- | -------------- | --------------- |
| [What kept happening] | [Fix to apply] | pending/applied |

## Action Items

- [ ] [Process improvement]
- [ ] [Docs update]
- [ ] [Tech debt to address]

## Required Doc Reviews

- [ ] **ARCHITECTURE.md** - Does it reflect current codebase? Any new patterns to document?
- [ ] **TEST_STRATEGY.md** - Any gaps exposed this week? New test patterns needed?
- [ ] **CLAUDE.md** - Any new conventions to add from friction patterns above?
```

## Friction Pattern Categories

Look for these signals in user messages:

| Signal                             | Meaning                     |
| ---------------------------------- | --------------------------- |
| "No", "Wrong", "Not what I meant"  | Misunderstanding of request |
| "Try again", "Redo"                | Output quality issue        |
| "Broken", "Doesn't work"           | Bug introduced              |
| "Stuck", "Can't figure out"        | Agent capability gap        |
| "I already said", "As I mentioned" | Context not retained        |
| "Too much", "Simpler"              | Over-engineering            |

## Multi-Instance Projects

If multiple agent instances work on the same codebase (e.g., swing-1 through swing-6):

- Retros cover ALL instances since they share the codebase
- Store retro in the primary working clone
- Include per-instance metrics where relevant

## Privacy Check

Before committing retros, scan for sensitive data:

```bash
grep -iE "token|tskey|secret|password|api.key|credential|auth-key" .claude/retros/*.md
```

Common false positives: "keypoint", "key files", "key insight"

## Applying Learnings

1. **Immediate fixes**: Update CLAUDE.md with new conventions
2. **General patterns**: PR to chop-conventions for reuse
3. **Tooling gaps**: Create beads issues for improvements
4. **Process changes**: Update team documentation

## Reference in CLAUDE.md

Add to your project's CLAUDE.md:

```markdown
## Retros

Run weekly (or when user says "retro"). See [chop-conventions/dev-inner-loop/retros.md](https://github.com/idvorkin/chop-conventions/blob/main/dev-inner-loop/retros.md) for process.

Storage: `retros/`
```
