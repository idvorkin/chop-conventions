# orbstack-dev CLAUDE.md fragment — OrbStack Ubuntu dev VMs

Rules here apply only on OrbStack Ubuntu dev VMs. Paths, hostnames,
architecture, and shell aliases encoded here reflect the current dev VM
topology (`/home/developer` home, `c-500X` hostnames, aarch64 architecture,
Ubuntu with systemd).

Loaded via `@~/.claude/claude-md/machine.md`, where the symlink points at
this file when `classify_machine` returns `"orbstack-dev"`.

## Shell aliases

- `ps` is aliased to a **pager wrapper** in this shell — invocations with positional args error with `invalid value for '--pager'`. Use `/bin/ps -f` explicitly, or `pgrep -f <pattern>` for PID lookup.
