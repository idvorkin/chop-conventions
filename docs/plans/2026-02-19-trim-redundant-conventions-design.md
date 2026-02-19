# Trim Redundant Conventions

## Problem

Several convention files duplicate what Claude Code plugins now provide (superpowers, beads, pr-review-toolkit). This adds noise when copying conventions into projects and creates maintenance burden keeping two sources in sync.

## Approach: Aggressive Trim

Delete files fully superseded by plugins. Preserve unique nuggets by moving them to surviving files. Update the index to distinguish unique conventions from plugin-covered workflows.

## Files to Delete

| File                                         | Superseded By                            |
| -------------------------------------------- | ---------------------------------------- |
| `dev-inner-loop/bug-investigation.md`        | superpowers `systematic-debugging` skill |
| `dev-inner-loop/before-implementing.md`      | superpowers `brainstorming` skill        |
| `dev-inner-loop/workflow-recommendations.md` | Claude Code auto-memory                  |
| `dev-setup/beads-claude-code.md`             | beads plugin + `beads.md`                |
| `dev-setup/chop-logs.md`                     | Cursor-specific, obsolete                |
| `marketplace.md`                             | Incomplete stub, one-time setup          |

## Content to Preserve Before Deleting

1. **From `bug-investigation.md`**: "Ask user before big refactors" -- add to `guardrails.md`
2. **From `beads-claude-code.md`**: Status line JSON config -- append to `beads.md`
3. **From `before-implementing.md`**: "Plan for context loss" -- already in beads workflow, no action needed

## Updates to Surviving Files

### `dev-inner-loop/a_readme_first.md`

Replace index with:

```markdown
## Core Conventions (read and follow)

- clean-code.md - Code quality standards
- clean-commits.md - Commit message standards
- pr-workflow.md - Pull request process
- guardrails.md - Safety rules requiring user approval
- repo-modes.md - AI-tools vs Human-supervised modes
- retros.md - Periodic retrospective process

## Covered by Skills/Plugins (use these instead)

- Bug investigation -> superpowers `systematic-debugging` skill
- Before implementing -> superpowers `brainstorming` skill
- Workflow recommendations -> Claude Code auto-memory (~/.claude/projects/\*/memory/)
- Beads + Claude Code -> beads plugin (status line config in beads.md)

## CLI Tips

- Pager issues: `unset PAGER`
- Git truncation: `git --no-pager diff`
- Use `uv` instead of `python`
- Check justfile for available commands
```

### `dev-inner-loop/guardrails.md`

Add one line to guardrail list:

```
- Ask user before big refactors discovered during bug fixes
```

### `dev-setup/beads.md`

Append status line configuration section from `beads-claude-code.md`.
