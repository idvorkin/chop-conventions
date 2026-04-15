# Chop Conventions

Reusable development conventions, skills, and agent definitions designed to be pulled into multiple projects.

**Scope.** Claude Code workflow tooling and shared dev conventions: git workflows, telegram MCP, image lifecycle, host doctors, scripting defaults, skill packaging. **NOT for**: personal/body/health content for one human, project-specific data, or anything another developer pulling these conventions into their own machine wouldn't get value from. When in doubt, file an issue with the proposal rather than a PR. See open issues #77 (skill grouping) and #83 (back-care wrong-repo proposal) for prior scope discussions.

## Commands

```bash
just              # List available targets
just fast-test    # Quick test pass
just test         # Full test suite
```

Pre-commit hooks (biome, prettier, ruff, dasel, fast tests) run on `git commit`
and **will reformat staged files in place**. If a commit fails with "files were
modified by this hook", re-stage and re-commit — don't fight the formatter.

## Git Workflow

This repo uses fork workflow via `idvorkin-ai-tools`. Check `gh auth status` — if running as `idvorkin-ai-tools`, push to the fork:

```bash
git push -u origin <branch>
gh pr create --repo idvorkin/chop-conventions
```

## Git Inner-Loop

- **`.git/info/exclude` for local-only ignores.** Ephemeral or per-machine ignore entries (worktree dirs, caches, editor state) that must NOT touch branch history go in `.git/info/exclude`, not `.gitignore`. Untracked, branch-independent, shared across linked worktrees via `git rev-parse --git-common-dir`. `git check-ignore` respects it the same as `.gitignore`.
- **`git fetch origin` does NOT refresh `refs/remotes/origin/HEAD`.** Run `git remote set-head origin --auto` before reading `git symbolic-ref --short refs/remotes/origin/HEAD` or you'll get stale values when the default branch was renamed (e.g. master → main) since clone. Idempotent no-op if origin/HEAD already matches.
- **Fix-style PR rebases: grep after resolving.** Conflict markers only cover regions both sides touched — a concurrent refactor can introduce new instances of the anti-pattern the fix PR was replacing in non-conflicting code the PR author never saw. After clearing `<<<<<<<` markers, grep the whole file for the pattern and fix every hit before `rebase --continue`.
- **Pre-commit `test` hook can corrupt the outer repo.** `skills/up-to-date/test_diagnose.py` shells out to `git` in a tempdir but inherits `GIT_INDEX_FILE` / `GIT_WORK_TREE` from the enclosing pre-commit, writing stub content to the real index AND injecting `core.worktree = /tmp/...` into `.git/config` (breaks every subsequent git command with `fatal: Invalid path`). After any failed `test` hook run, check `git status` and `grep /tmp .git/config`. Workaround: `SKIP=test git commit ...` for changes that don't exercise `up-to-date`. Tracked in #109.
- **`git check-ignore` on directory-only patterns needs a real directory.** A trailing-slash pattern (`.worktrees/`) only matches when the target exists on disk as a directory — git can't classify a non-existent path as a directory, so `git check-ignore -q .worktrees` returns exit 1 and the rule looks broken when it's fine. Verify by querying a concrete subpath (`git check-ignore .worktrees/x`) or `mkdir` the dir first; plain patterns without trailing slash match non-existent paths normally.

## Process-Signaling Safety

Scripts that signal processes by pattern (cpulimit, pkill, kill by comm match) MUST exclude lifeline processes or risk wedging the VM / locking out the SSH session: `tailscaled`, `etserver`, **`etterminal`**, `tmux` (bare + `"tmux:"*`), `sshd`, init-like (`sh`, `init`, `systemd`), and kernel threads (`kthreadd`, `kworker*`, `ksoftirqd*`, `migration*`, `rcu_*`). Test the exclude list with a unit test before deploying — see `skills/machine-doctor/doctor-guards.md` for an example.

**Tests for these signalling functions MUST mock their subprocess/OS calls.** An unmocked "smoke test" that invokes the real `pkill`/`os.kill`/`create_subprocess_exec` against the dev box's real process table will match legitimate running processes and SIGTERM them — this nuked a live Telegram MCP bridge mid-session on 2026-04-12. Patch `asyncio.create_subprocess_exec` / `subprocess.run` / `os.kill` via `monkeypatch` before invoking the target. The test verifies return-type and error-handling, never the real kill path.

## Scripting Language Defaults

**Default to Python for any non-trivial script, not shell.** Shell is for one-liners and the occasional `just` target. Anything with branching, data structures, or more than ~20 lines should be Python.

- Use the `uv run --script` shebang with an inline PEP 723 dependency block so scripts self-bootstrap — no venvs, no `pip install`, no setup steps for the user. See [`python/uv-shebang-deps.md`](python/uv-shebang-deps.md) and [`python/python-cli.md`](python/python-cli.md).
- Stdlib is enough for most helpers. Only add dependencies when they earn their keep (Typer + Rich for user-facing CLIs; plain stdlib for programmatic JSON-emitting helpers).
- Put pure logic behind functions that can be unit-tested without subprocess mocking. Shell out to external tools (`git`, `gh`, etc.) through a thin wrapper so the business logic stays testable.
- Working example: [`skills/up-to-date/diagnose.py`](skills/up-to-date/diagnose.py) — stdlib-only, `uv run` shebang, parallelized subprocess calls, unit-tested pure functions.

## Parsing Claude Session Data

- **Subagents live at `~/.claude/projects/<proj>/<session-uuid>/subagents/agent-*.jsonl`**, NOT inside the main session JSONL. Scripts scanning session data must glob `**/subagents/*.jsonl` or undercount tokens by ~18% (measured against ~40k assistant messages).
- **Bash `tool_use.input.command` is frequently compound** (`cd ~/gits/foo && gh pr create …`, `git push && …`). Use `re.search(r"\bcmd\b", command)`, not `re.match(r"^\s*cmd", command.lstrip())` — anchored match drops ~25% of real invocations. Word boundary handles both standalone and chained forms.

## Diagnostics: Code Over Prose

**Diagnostic checks belong in scripts, not in skill/doc prose.** Skills describe WHEN to diagnose and HOW to recover; code describes WHAT to check. Paths move — code errors loudly, prose rots silently. Reference implementation: `skills/harden-telegram/tools/telegram_debug.py` (`--doctor` with `ok`/`warn`/`fail`/`note` accumulators, `--paths` file-map inventory, inline log tails). **Vendor the doctor _into_ the skill** (`skills/<name>/tools/`), never into a source repo it diagnoses — source-repo coupling kills portability on any machine without that repo checked out. Parameterize runtime/source paths via env vars (e.g. `LARRY_TELEGRAM_DIR`, `TELEGRAM_SOURCE_DIR`), not constants. If you catch yourself writing "check X at path Y" prose in a skill, stop and move it to the doctor.

## Report Generators: Measure, Don't Hardcode

**Factual claims in generated output must be computed from the actual data, not hardcoded as constants.** LLM-written report code naturally reaches for narrative footnotes that _sound_ measured ("0 X observed", "no errors found") but are string literals that rot silently and can flip from true to false without anyone noticing. A TTL-bug footnote in `skills/cost-impact/_impl.py` unconditionally claimed `0 ephemeral_5m_input_tokens observed` — real data was 10.5M / $57.70. If a report asserts a number or category, derive it from the input; if you can't derive it, drop the assertion or phrase it as a question the reader must answer, not a fact.

## Compiled-Tool Staleness Check

**When a user reports "I don't see the new feature" after code changes to a compiled tool, first check the installed binary's mtime, not the source.** `ls -la $(which <tool>)` or `stat` — compare against commit time. `cargo test` / `cargo build` validates fresh source but does NOT replace `~/.cargo/bin/<tool>`; use `cargo install --path . --force`. Don't open the debugger until you've confirmed the binary you're running contains the change.

## Diagnostics: Out-of-Band Notification

**A diagnostic tool must not depend on the thing it's diagnosing.** A watchdog that notifies via the MCP bridge it's watching, a healthcheck running inside the process it's checking, backup verification using the backup system itself — all foot-guns that go silent at exactly the moment they need to scream. Notify out-of-band. Reference: `skills/harden-telegram` watchdog uses `telegram_debug.py --direct-send` (POSTs straight to Telegram Bot API) for alerts, never the MCP `reply` tool, so it works even when `server.ts` is dead.

## Abstractions: Wait for N=2

**Don't generalize a pattern into templates or a framework until you have at least two concrete instances.** One-instance abstractions are copy-paste bait — they fork on day one, rot, and rediscover the same bugs on every downstream consumer. When a single instance is all you've got, write it directly and point at it as a _reference implementation_, not a template to clone. Extract at N=2 minimum.

## GitHub Actions + Claude Code SDK

Before modifying any workflow that uses `anthropics/claude-code-action@v1`, read [`dev-inner-loop/github-action-claude-code-action-gotcha.md`](dev-inner-loop/github-action-claude-code-action-gotcha.md). Critical traps: the YAML must byte-match the default branch or token exchange 401s, `show_full_output` defaults to `false` (hiding Claude's activity), fork PRs can't get OIDC tokens (use `pull_request_target`), and Node 20 forced to Node 24 on June 2, 2026.

## Shared CLAUDE.md Fragments

`claude-md/global.md` holds rules that are **both universally applicable AND ones the user intends to share.** When migrating a flat `~/.claude/CLAUDE.md` bullet into fragments, confirm per-rule — a rule being *defensible* on every machine doesn't mean the user *wants* it on every machine. Personal-preference rules (caution defaults, push-handling conventions) belong in `machines/<name>.md` or stay flat, even when the rule reads as universally good practice. Scrub project-specific identifiers (project names, issue IDs) when migrating — shared fragments propagate to every opted-in machine.

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
- **Pure markdown is the default.** Helpers (Python, shell) only earn their place when they (a) parallelize subprocess calls AND (b) need unit-tested classification logic. For "figure out which thing the user means" reasoning, use the markdown prompt. Reference: `up-to-date` earned its `diagnose.py`; `learn-from-session` and most others stay pure markdown.
- Skills are installed by **symlinking** into Claude Code's skill directories:
  - Machine-level (all projects): `~/.claude/skills/<name>` -> `<chop-conventions>/skills/<name>`
  - Project-level (one project): `<project>/.claude/skills/<name>` -> `<chop-conventions>/skills/<name>`
- After adding a skill, create the symlink and document it in the README skills table
- After `/up-to-date` pulls new commits, check the pull delta for newly-added `skills/<name>/` dirs and offer to symlink them into `~/.claude/skills/`. If the delta added no skills, say nothing. Never link automatically.
- **Editing an installed skill: always use a worktree, never the symlink.** Because `~/.claude/skills/<name>` points directly into the primary `chop-conventions` checkout, editing through that path mutates whichever branch is currently checked out — silently mixing skill-fix work with any unrelated in-flight branch. Before touching any file under `~/.claude/skills/`, run `realpath` to confirm where it resolves, then create a worktree off `upstream/main` (`git -C ~/gits/chop-conventions worktree add .worktrees/<slug> -b delegated/<slug> upstream/main`) and edit there. The self-referential case (editing `delegate-to-other-repo` while using it) does NOT get a free pass.

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

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->

## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
