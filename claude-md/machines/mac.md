# mac CLAUDE.md fragment — macOS laptops

Rules here apply only on macOS. Paths, shells, and toolchain defaults that
encode Apple-specific facts live here.

Loaded via `@~/.claude/claude-md/machine.md`, where the symlink points at
this file when `classify_machine` returns `"mac"`.

## Destructive commands: confirm before running

**Never run destructive commands without confirmation** — `rm -rf`, `git reset --hard`, `DROP TABLE`, force-push, etc. Show the command and ask before running.
