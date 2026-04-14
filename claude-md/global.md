# Global CLAUDE.md fragment — universal rules

Rules here apply on **every machine** regardless of OS, hostname, or network
topology. They must be true on a fresh macOS laptop with nothing installed
and on a production OrbStack Ubuntu VM alike.

This file is loaded into each machine's `~/.claude/CLAUDE.md` via an
`@~/.claude/claude-md/global.md` import, where the path is a symlink
managed by `/up-to-date`. Editing this file in `chop-conventions` propagates
to every opted-in machine automatically.

<!-- Content migration from the current flat `~/.claude/CLAUDE.md` is
     tracked as a follow-up; this file starts as a stub and grows as
     rules are categorized. -->
