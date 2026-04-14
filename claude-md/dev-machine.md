# Dev-machine CLAUDE.md fragment — Tailscale-served hosts

Rules here apply only on machines served to the user over Tailscale — i.e.
the browser reaching dev servers is on a different device than the host
running `claude`. Machines that do not match this host class skip this
file entirely (the symlink is absent, the `@`-import silently no-ops).

This file is loaded via `@~/.claude/claude-md/dev-machine.md`, where the
symlink is created only on machines whose `diagnose.py` classifies them
as `dev_machine: true` (Tailscale installed **and** hostname matches the
dev-VM pattern).

<!-- Content migration from the current flat `~/.claude/CLAUDE.md` is
     tracked as a follow-up; this file starts as a stub. Typical
     content: URL-sharing conventions, bind-host defaults, Tailnet
     name resolution. -->
