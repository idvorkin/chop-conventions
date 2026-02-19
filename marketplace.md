# Claude Code Setup

## Linking Chop-Conventions Into Your Project

Add this to your project's `CLAUDE.md`:

```markdown
Before starting any work, clone the chop-conventions repository:

\`\`\`bash
git clone https://github.com/idvorkin/chop-conventions.git repo_tmp/chop-conventions
\`\`\`

Then read: `repo_tmp/chop-conventions/dev-inner-loop/a_readme_first.md`
```

The conventions are cloned fresh each session so you always get the latest version.

## Plugin Marketplaces

Claude Code has three plugin marketplaces. Add them all:

```bash
claude plugin marketplace add anthropics/claude-code-plugins
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin marketplace add steveyegge/beads
```

## Recommended Plugins

### Official Plugins (claude-plugins-official)

| Plugin              | What It Does                                                 |
| ------------------- | ------------------------------------------------------------ |
| `superpowers`       | Brainstorming, TDD, debugging, git worktrees, plan execution |
| `pyright-lsp`       | Python type checking via LSP                                 |
| `typescript-lsp`    | TypeScript language server                                   |
| `rust-analyzer-lsp` | Rust language server                                         |

```bash
claude plugin install superpowers@claude-plugins-official
claude plugin install pyright-lsp@claude-plugins-official
claude plugin install typescript-lsp@claude-plugins-official
claude plugin install rust-analyzer-lsp@claude-plugins-official
```

### Community Plugins (claude-code-plugins)

| Plugin              | What It Does                                                  |
| ------------------- | ------------------------------------------------------------- |
| `code-review`       | Multi-agent code review with confidence scoring               |
| `feature-dev`       | Guided feature development with architecture focus            |
| `frontend-design`   | Production-grade UI/frontend generation                       |
| `pr-review-toolkit` | Specialized PR review agents (tests, types, errors, comments) |

```bash
claude plugin install code-review@claude-code-plugins
claude plugin install feature-dev@claude-code-plugins
claude plugin install frontend-design@claude-code-plugins
claude plugin install pr-review-toolkit@claude-code-plugins
```

### Beads (beads-marketplace)

| Plugin  | What It Does                                                                 |
| ------- | ---------------------------------------------------------------------------- |
| `beads` | Git-backed issue tracking for AI agents (see [beads.md](dev-setup/beads.md)) |

```bash
claude plugin install beads@beads-marketplace
```

## One-Liner Install

```bash
claude plugin marketplace add anthropics/claude-code-plugins && \
claude plugin marketplace add anthropics/claude-plugins-official && \
claude plugin marketplace add steveyegge/beads && \
claude plugin install superpowers@claude-plugins-official && \
claude plugin install pyright-lsp@claude-plugins-official && \
claude plugin install typescript-lsp@claude-plugins-official && \
claude plugin install rust-analyzer-lsp@claude-plugins-official && \
claude plugin install code-review@claude-code-plugins && \
claude plugin install feature-dev@claude-code-plugins && \
claude plugin install frontend-design@claude-code-plugins && \
claude plugin install pr-review-toolkit@claude-code-plugins && \
claude plugin install beads@beads-marketplace
```
