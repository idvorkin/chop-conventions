# Retrospectives

Run retrospectives periodically (weekly recommended) to review human-AI collaboration patterns and improve workflows.

## When to Run

- Weekly on a set day
- When user says "retro" or "workflow review"
- At end of significant project milestones

## Storage

```text
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

The goal is to understand what went wrong and find **improvements**, not just count issues. Use subagents to read the raw logs and examine actual user-agent interactions.

```text
Use the Task tool with subagent_type="general-purpose" to analyze ~/.claude/history.jsonl:
- Read raw conversation logs to understand what actually happened
- Find friction moments: where did the user correct the agent? What did Claude do wrong?
- Look at Claude's actual responses before corrections to understand root causes
- Identify patterns that suggest workflow or instruction improvements
- Return findings organized by improvement opportunity, not just counts
```

**Subagent output should include:**

- Start date and end date of analysis period
- Directories/projects analyzed
- Friction patterns found with specific examples
- What Claude did wrong in each case
- Recommended improvements to CLAUDE.md or workflows

Example subagent prompt:

> "Analyze ~/.claude/history.jsonl for the past week starting from [DATE]. For each project directory, find friction patterns where the user corrected the agent. Read the actual conversation: what did the user ask? What did Claude do? Why was it wrong? Group findings by improvement opportunity (e.g., 'Claude keeps doing X when user wants Y'). Include the start date, directories analyzed, and specific recommendations for CLAUDE.md updates."

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

| Pattern   | Example                    | What Claude Did Wrong | Improvement                 |
| --------- | -------------------------- | --------------------- | --------------------------- |
| [Pattern] | "No, I meant..." (context) | [Claude's mistake]    | [CLAUDE.md or workflow fix] |

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

Before committing retros, read the complete retro file and scan for sensitive data:

1. **Read the whole retro** - Don't just grep; read the file in Claude to catch context-dependent leaks
2. **Look for**: API keys, tokens, passwords, auth keys, Tailscale keys, credentials, internal URLs
3. **Common false positives**: "keypoint", "key files", "key insight"

Quick grep check:

```bash
grep -iE "token|tskey|secret|password|api.key|credential|auth-key" retros/*.md
```

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
