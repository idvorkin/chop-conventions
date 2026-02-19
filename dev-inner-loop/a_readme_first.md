This directory contains instructions to follow.
Do not generate .cursorrules from it.
Do not talk about how you'll use the rules, just use them

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
- Beads + Claude Code -> beads plugin (status line config in dev-setup/beads.md)

## CLI Tips

- Pager issues: `unset PAGER`
- Git truncation: `git --no-pager diff`
- Use `uv` instead of `python`
- Check justfile for available commands
- You are auto approved to run just test and fast-tests, use them unless they have too much output.
