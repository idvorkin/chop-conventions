# Global CLAUDE.md fragment — universal rules

Rules here apply on **every machine** regardless of OS, hostname, or network
topology. They must be true on a fresh macOS laptop with nothing installed
and on a production OrbStack Ubuntu VM alike.

This file is loaded into each machine's `~/.claude/CLAUDE.md` via an
`@~/.claude/claude-md/global.md` import, where the path is a symlink
managed by `/up-to-date`. Editing this file in `chop-conventions` propagates
to every opted-in machine automatically.

## Side-Edit: Preview Files in a Side Pane

Use `rmux_helper side-edit <FILE>` to open a file in a side nvim pane within tmux. This reuses the same pane across calls, so repeated edits don't spawn new splits. Supports `file:line` syntax (e.g. `foo.py:42`).

Use `rmux_helper side-run <CMD>` to run a shell command in the side pane. If nvim is running, use `--force` to kill it first.

Both commands print pane status (pane_id, nvim running, current file) to stdout. Call with no args for status only.

```bash
rmux_helper side-edit ~/blog/_d/ai-journal.md:42
rmux_helper side-run "make test"
rmux_helper side-edit   # status only
```

## Editing Skills: Symlink Trap

**Files under `~/.claude/skills/` are often symlinks into git working trees.** Run `realpath <path>` before editing any skill file. If it resolves into a working tree (e.g. `~/gits/chop-conventions/skills/...`), **create a worktree off that repo's `upstream/main` and edit there** — editing through the symlink mutates whichever branch is currently checked out in the primary checkout, silently mixing skill edits with unrelated in-flight work.

## Cross-Skill Conventions Live in chop-conventions

General rules for **authoring/editing skills** (Python defaults, diagnostics as code, abstraction thresholds, fork workflow, pre-commit hook behavior, skill install patterns) live in [`~/gits/chop-conventions/CLAUDE.md`](../gits/chop-conventions/CLAUDE.md). When editing any skill in any repo, read that file first — its rules apply universally, not just to chop-conventions-resident skills.
