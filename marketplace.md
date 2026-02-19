# Claude Code Setup

## Linking Chop-Conventions Into Your Project

Add this to your project's `CLAUDE.md`:

```markdown
Before starting any work, clone the chop-conventions repository:

\`\`\`bash
mkdir -p repo_tmp && cd repo_tmp
git clone https://github.com/idvorkin/chop-conventions.git
\`\`\`

Then read: `repo_tmp/chop-conventions/dev-inner-loop/a_readme_first.md`
```

The conventions are cloned fresh each session so you always get the latest version.

## Plugin Marketplaces

Claude Code has three plugin marketplaces. Add them all:

```bash
claude plugins marketplace add anthropics/claude-code-plugins
claude plugins marketplace add anthropics/claude-plugins-official
claude plugins marketplace add steveyegge/beads-marketplace
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
claude plugins install superpowers@claude-plugins-official
claude plugins install pyright-lsp@claude-plugins-official
claude plugins install typescript-lsp@claude-plugins-official
claude plugins install rust-analyzer-lsp@claude-plugins-official
```

### Community Plugins (claude-code-plugins)

| Plugin              | What It Does                                                  |
| ------------------- | ------------------------------------------------------------- |
| `code-review`       | Multi-agent code review with confidence scoring               |
| `feature-dev`       | Guided feature development with architecture focus            |
| `frontend-design`   | Production-grade UI/frontend generation                       |
| `pr-review-toolkit` | Specialized PR review agents (tests, types, errors, comments) |

```bash
claude plugins install code-review@claude-code-plugins
claude plugins install feature-dev@claude-code-plugins
claude plugins install frontend-design@claude-code-plugins
claude plugins install pr-review-toolkit@claude-code-plugins
```

### Beads (beads-marketplace)

| Plugin  | What It Does                                                                 |
| ------- | ---------------------------------------------------------------------------- |
| `beads` | Git-backed issue tracking for AI agents (see [beads.md](dev-setup/beads.md)) |

```bash
claude plugins install beads@beads-marketplace
```

## One-Liner Install

```bash
claude plugins marketplace add anthropics/claude-code-plugins && claude plugins marketplace add anthropics/claude-plugins-official && claude plugins marketplace add steveyegge/beads-marketplace && claude plugins install superpowers@claude-plugins-official pyright-lsp@claude-plugins-official typescript-lsp@claude-plugins-official rust-analyzer-lsp@claude-plugins-official code-review@claude-code-plugins feature-dev@claude-code-plugins frontend-design@claude-code-plugins pr-review-toolkit@claude-code-plugins beads@beads-marketplace
```
