# Workflow Recommendations

Capture human-AI workflow improvements discovered during sessions for later review and codification.

## Why?

During AI-assisted coding sessions, patterns emerge:

- Repeated corrections the human makes
- Workflow friction points
- Missing context that should be documented
- New conventions worth standardizing

Capturing these in the moment prevents losing valuable insights.

## Setup

Create the directory:

```
.claude/
  workflow-recommendations/
    README.md             # Instructions
    YYYY-MM-DD-HHMMSS-XXXX.md  # Session files
```

## Add to CLAUDE.md

```markdown
## Workflow Review

At session end (when user signals done, or says "workflow review"):

1. Review session for patterns: repeated corrections, friction, missing context
2. Create `.claude/workflow-recommendations/YYYY-MM-DD-HHMMSS-XXXX.md` (XXXX = random 4 chars)
3. Ask user if they want to merge any immediately into CLAUDE.md
4. For generalizable patterns, offer to PR to chop-conventions
```

## File naming

Use `YYYY-MM-DD-HHMMSS-XXXX.md` where XXXX is random characters.

This prevents merge conflicts when multiple agents work in parallel.

## Recommendation file template

```markdown
# Session: YYYY-MM-DD (project-name)

## Recommendations

### [Short title]

- **Pattern**: [What kept happening]
- **Recommendation**: [Specific text to add]
- **Target**: CLAUDE.md | chop-conventions
- **Status**: pending | merged | rejected
```

## Workflow

1. **During session**: Agent notices patterns, user makes corrections
2. **End of session**: User signals done or says "workflow review"
3. **Agent creates**: Timestamped recommendations file
4. **Agent asks**: "Want to merge any of these now?"
5. **Periodically**: Human reviews, merges good ones
6. **Update status**: Mark recommendations as `merged` or `rejected`

## Rules

- **Never edit old files** - Only create new ones (avoids conflicts)
- **One file per session** - Keep recommendations grouped by context
- **Be specific** - Include exact text to add, not vague suggestions
- **Target appropriately** - Project-specific → CLAUDE.md, general → chop-conventions
