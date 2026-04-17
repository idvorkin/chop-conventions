# Chop Conventions

> Development conventions, best practices, and standardized workflows for productive AI-assisted coding

## Intro

This repository contains codified development practices, conventions, and setup guidelines designed to maximize productivity when working with AI coding assistants. **These conventions are designed to be pulled into many projects**—copy, symlink, or reference them as needed.

The magic of "vibe coding" lies in AI's ability to infer intent from minimal input, but that magic becomes fragile without proper specifications and verification processes.

This collection of conventions serves as the **specifications layer** that makes AI-assisted development consistent, reliable, and scalable across projects. Use this repo as a central source of truth, pulling in the conventions you need for each project.

## Why These Conventions Matter

As explained in [Vibe Coding: Best Practices for Flow, Fun, and Results](https://idvork.in/vibe), successful AI-assisted development requires:

- **Specifications**: Clear constraints and guidelines for business logic, architecture, code style, and environment setup
- **Verification**: Robust testing and validation processes

Without these foundations, AI coding can lead to inconsistent results and frustrating debugging sessions. These conventions provide the scaffolding needed to harness AI's power effectively.

## Getting Started

Paste this into Cursor.

Read the directions in https://github.com/idvorkin/chop-conventions/tree/main/dev-setup and then apply it

## What's Included

### 🛠 Development Environment

- Comprehensive `.gitignore` configurations covering 10+ programming languages
- Editor and IDE setup guidelines (VSCode, IntelliJ, Vim, etc.)
- Platform-specific configurations (macOS, Windows, Linux)
- Security best practices for credentials and sensitive files

### 📋 Code Standards

- Language-specific best practices
- Clean code guidelines
- Architecture decision frameworks
- Testing and verification strategies

### 🔧 Tooling & Automation

- Build tool configurations
- CI/CD pipeline templates
- Pre-commit hooks and automated checks
- Development workflow optimization
- **[Beads](./dev-setup/beads.md)** - Git-backed issue tracking for AI agents

## Philosophy

These conventions embody the principle that **constraints enable creativity**. By establishing clear guidelines upfront, you free AI (and human developers) to focus on solving problems rather than making endless micro-decisions about formatting, structure, and tooling.

The goal is to create a feedback loop where:

1. **Specifications** guide AI output toward desired outcomes
2. **Verification** catches issues early and validates results
3. **Iteration** refines both specs and verification based on learnings

## Skills

Reusable Claude Code skills live in `skills/`. Each skill is a directory with a `SKILL.md` file.

### Installing Skills

Skills are installed by symlinking into Claude Code's skill directories:

```bash
# Machine-level (available in ALL projects):
ln -s /path/to/chop-conventions/skills/<skill-name> ~/.claude/skills/<skill-name>

# Project-level (available in one project):
ln -s /path/to/chop-conventions/skills/<skill-name> <project>/.claude/skills/<skill-name>
```

Machine-level skills go in `~/.claude/skills/` and are available everywhere. Project-level skills go in `<project>/.claude/skills/` and are scoped to that repo.

### Install Globally

Several skills ship CLI helpers (Gemini TTS, Telegram diagnostics/watchdog, up-to-date diagnose/hook-trust). Install them as proper `uv tool install` packages so you can invoke them from anywhere:

```bash
just install-tools        # installs every registered chop-conventions package
```

Under the hood this runs `./install-tools.py`, which calls `uv tool install --force --reinstall <path>` for each skill directory that ships a `pyproject.toml`. Each tool gets its own isolated venv; `uv` symlinks the console-script entry points into `~/.local/bin/`.

Currently registered entry points:

| CLI                     | Package               | Source dir               |
| ----------------------- | --------------------- | ------------------------ |
| `gen-tts`               | `chop-gen-tts`        | `skills/gen-tts`         |
| `tg-doctor`             | `chop-telegram-tools` | `skills/harden-telegram` |
| `tg-watchdog`           | `chop-telegram-tools` | `skills/harden-telegram` |
| `up-to-date-diag`       | `chop-up-to-date`     | `skills/up-to-date`      |
| `up-to-date-hook-trust` | `chop-up-to-date`     | `skills/up-to-date`      |

Other targets:

```bash
just install-tools-dry-run   # enumerate planned `uv tool install` commands, no writes
just uninstall-tools         # `uv tool uninstall` every registered package
```

After install, a `which` check prints where each binary landed — if any says `NOT ON $PATH`, add `~/.local/bin` to your shell rc.

**Back-compat note.** The old shebang-driven scripts (e.g. `skills/gen-tts/generate-tts.py`, `skills/harden-telegram/tools/telegram_debug.py`, `skills/up-to-date/diagnose.py`) remain as deprecated thin shims during the transition — they bootstrap the same code via PEP 723 `uv run --script`, so in-flight callers don't break. Prefer the packaged entry points going forward; the shims will be removed once downstream callers migrate.

### Available Skills

| Skill                    | Scope   | Description                                                                      |
| ------------------------ | ------- | -------------------------------------------------------------------------------- |
| `ammon`                  | machine | Look up the current time in Denmark for Ammon                                    |
| `architect-review`       | machine | Iterative architect review passes on design specs, tracking convergence          |
| `background-usage`       | machine | Check Claude Code plan usage without blocking the session                        |
| `build-bd-static`        | machine | Build a static `bd` fallback when Homebrew is unavailable or not portable enough |
| `clock`                  | machine | Schedule recurring session tasks (time checks, reminders)                        |
| `delegate-to-other-repo` | machine | Delegate cross-repo work to a subagent with an isolated context; ends with a PR  |
| `docs`                   | machine | Fetch fresh library/framework docs via Context7 (`ctx7`)                         |
| `gen-image`              | machine | Generate illustrations via Gemini image API                                      |
| `gist-image`             | machine | Host images on GitHub gists for PRs/issues                                       |
| `image-explore`          | machine | Brainstorm and compare visual directions                                         |
| `learn-from-session`     | machine | Extract durable lessons from a session into the right CLAUDE.md files            |
| `machine-doctor`         | machine | Diagnose system health, kill rogue processes                                     |
| `showboat`               | machine | Create executable demo documents with screenshots                                |
| `up-to-date`             | machine | Sync git repo with upstream                                                      |

## Usage

1. **Start with Setup**: Follow the [dev-setup guides](./dev-setup/) to establish your foundation
2. **Customize for Your Project**: Adapt the conventions to your specific needs
3. **Iterate and Improve**: Update conventions based on what you learn
4. **Share Across Projects**: Reuse successful patterns in new codebases

## Learn More

- 📖 **[Vibe Coding Practices](https://idvork.in/vibe)** - Deep dive into AI-assisted development philosophy
- 🛠 **[Dev Setup Guide](./dev-setup/)** - Practical implementation instructions
- 💡 **[Contributing](./CONTRIBUTING.md)** - How to improve these conventions _(coming soon)_

## Referenced Repositories

External repos we pull conventions from. Periodically check for updates.

| Repository                                                                                                    | What We Use              | Last Reviewed          |
| ------------------------------------------------------------------------------------------------------------- | ------------------------ | ---------------------- |
| [misc_coding_agent_tips_and_scripts](https://github.com/Dicklesworthstone/misc_coding_agent_tips_and_scripts) | Safety hooks, beads tips | 2025-12-21 @ `02e68d8` |

---

_Part of [idvork.in](https://idvork.in)'s ecosystem for thoughtful technology practices._
