# Chop Conventions

Reusable development conventions, skills, and agent definitions designed to be pulled into multiple projects.

## Commands

```bash
just              # List available targets
just fast-test    # Quick test pass
just test         # Full test suite
```

## Git Workflow

This repo uses fork workflow via `idvorkin-ai-tools`. Check `gh auth status` — if running as `idvorkin-ai-tools`, push to the fork:

```bash
git push -u origin <branch>
gh pr create --repo idvorkin/chop-conventions
```

## Process-Signaling Safety

Scripts that signal processes by pattern (cpulimit, pkill, kill by comm match) MUST exclude lifeline processes or risk wedging the VM / locking out the SSH session: `tailscaled`, `etserver`, **`etterminal`**, `tmux` (bare + `"tmux:"*`), `sshd`, init-like (`sh`, `init`, `systemd`), and kernel threads (`kthreadd`, `kworker*`, `ksoftirqd*`, `migration*`, `rcu_*`). Test the exclude list with a unit test before deploying — see `skills/machine-doctor/doctor-guards.md` for an example.

## Structure

- `dev-setup/` - Development environment configuration (beads, hooks, gitignore, justfile, tailscale)
- `dev-inner-loop/` - Development workflow conventions (clean code, commits, PRs, guardrails)
- `skills/` - Reusable Claude Code skills (each is a directory with a `SKILL.md`)
- `claude-agents/` - Agent definitions (code-review, conversation-log-publisher, image-content-analyzer, etc.)
- `deployment/` - Deployment guides (surge.sh)
- `copied_prompts/` - Reference prompts from other sources
- `python/` - Python-specific conventions
- `pwa/` - PWA-specific specifications and patterns
- `useful-snippets/` - Reusable patterns for cross-project tasks
- `docs/` - Design specs and documentation
- `zz-chop-logs/` - Session logs

## Skills

Skills are Claude Code slash commands that live in `skills/<name>/SKILL.md`.

### Conventions

- Each skill is a directory containing at minimum a `SKILL.md` with YAML frontmatter (`name`, `description`, `allowed-tools`)
- Skill names must not collide with Claude Code built-in commands (e.g., use `machine-doctor` not `doctor`)
- Skills are installed by **symlinking** into Claude Code's skill directories:
  - Machine-level (all projects): `~/.claude/skills/<name>` -> `<chop-conventions>/skills/<name>`
  - Project-level (one project): `<project>/.claude/skills/<name>` -> `<chop-conventions>/skills/<name>`
- After adding a skill, create the symlink and document it in the README skills table

### Size Guideline

When a skill's `SKILL.md` exceeds ~500 lines, or a single tier's detail exceeds ~100 lines, factor the tier into a supplementary `.md` in the same directory with a "loaded on demand" note at the top. `SKILL.md` stays navigable at a glance; detailed runbooks live next door. See `skills/machine-doctor/doctor-guards.md` as a reference.

## Agents

Agent definitions live in `claude-agents/`. These are markdown files that define specialized agents for use with the Agent tool. See `AGENTS.md` for beads integration and session workflow.

## Plugins

Marketplace plugins I rely on across projects (user scope). Install with `/plugin install <name>@<marketplace>`.

**`claude-plugins-official`** — `anthropics/claude-plugins-official`
- `superpowers` — core skills framework: brainstorming, TDD, debugging, planning, etc.
- `claude-md-management` — audit and improve CLAUDE.md files
- `code-simplifier` — review and simplify changed code
- `pyright-lsp`, `typescript-lsp`, `rust-analyzer-lsp` — language servers for the LSP tool

**`claude-code-plugins`** — `anthropics/claude-code`
- `frontend-design` — distinctive, production-grade UI generation
- `pr-review-toolkit` — specialized agents for PR review (comments, tests, silent failures, type design)

**`beads-marketplace`** — `steveyegge/beads`
- `beads` — AI-supervised issue tracker; hooks into `SessionStart` / `PreCompact` to prime context
