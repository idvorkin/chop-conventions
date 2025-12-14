This directory contains instructions to follow.
Do not generate .cursorrules from it.
Do not talk about how you'll use the rules, just use them

## Core Conventions

Read and follow:

- clean-code.md - Code quality standards
- clean-commits.md - Commit message standards
- pr-workflow.md - Pull request process
- guardrails.md - Safety rules requiring user approval

## Workflow Processes

Read and follow:

- before-implementing.md - Checklist before starting work
- bug-investigation.md - Protocol for fixing bugs
- retros.md - Periodic retrospective process
- workflow-recommendations.md - Capturing session patterns

### CLI usage and errors

- If get errors with head or cat (they are in the pager command), start by unsetting PAGER `unset PAGER`
- If git output is truncated, use git --no-pager e.g. (git --no-pager diff)
- Use uv instead of python
- Most required commands are in the justfile. Use them there if they exist.
- You are auto approved to run just test and fast-tests, use them unless they have too much output.
