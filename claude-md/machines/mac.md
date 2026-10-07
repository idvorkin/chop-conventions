# mac CLAUDE.md fragment — macOS laptops

Rules here apply only on macOS. Paths, shells, and toolchain defaults that
encode Apple-specific facts live here.

Loaded via `@~/.claude/claude-md/machine.md`, where the symlink points at
this file when `classify_machine` returns `"mac"`.

## Destructive commands: confirm before running

**Never run destructive commands without confirmation** — `rm -rf`, `git reset --hard`, `DROP TABLE`, force-push, etc. Show the command and ask before running.

## The unified log: call it as `/usr/bin/log`

In zsh, `log` is a shell builtin. `log show …` runs the builtin and prints nothing, which looks like "nothing was logged". Always write `/usr/bin/log show`. The unified log keeps routine info only about a day and errors a few days, so capture it within the hour of the event.
