### Intro/Why?

Pre-checkin tests are a great way to setup a pit of success. We use **prek** to enforce this.

### prek over pre-commit

[prek](https://github.com/j178/prek) is a Rust-based drop-in replacement for the Python `pre-commit` framework. Use prek, not pre-commit.

**Why prek:**

- Faster startup (no Python runtime)
- Single static binary via `brew install prek`
- Reads the same `.pre-commit-config.yaml` — no config changes needed
- Compatible with all existing pre-commit hooks

**If you see pre-commit in a repo, migrate it:**

```bash
brew install prek
# If pre-commit is installed, replace it with a shim that warns:
brew uninstall pre-commit  # optional
prek install               # installs hook at .git/hooks/pre-commit
```

**Common commands:**

```bash
prek run --files <files>   # Run hooks on specific files
prek run --all-files       # Run hooks on all files
prek autoupdate            # Update hook versions
prek list                  # List configured hooks
```

### Environment Setup

#### Important Considerations

- Always check with the user before changing a pre-existing `.pre-commit-config.yaml`
- Review current hooks and versions to understand what's already in place
- Ask about project-specific requirements or customizations

**File type coverage:**

- **Biome** supports: JS, TS, JSX, TSX, JSON, JSONC, CSS, GraphQL
- **Prettier** (in this config) is limited to: Markdown, HTML — do this via prettierignore file
- **Ruff** supports: Python, .pyi, Jupyter notebooks
- **Dasel** validates: YAML, JSON, YML files

Check your project for other file types that might need formatting/linting (TOML, XML, SQL, Dockerfile, Shell scripts, etc.).

#### Default .pre-commit-config.yaml

```yaml
repos:
  # Python: Linting and formatting
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.11
    hooks:
      - id: ruff
        name: Ruff Linter
        types_or: [python, pyi, jupyter]
        args: [--fix]
      - id: ruff-format
        name: Ruff Formatter
        types_or: [python, pyi, jupyter]

  # Biome: JS/TS/JSON/CSS/etc.
  - repo: https://github.com/biomejs/pre-commit
    rev: v2.0.0-beta.5
    hooks:
      - id: biome-check
        name: Biome Lint & Format
        additional_dependencies: ["@biomejs/biome@1.9.4"]

  # Prettier: Only for Markdown and HTML
  - repo: https://github.com/pre-commit/mirrors-prettier
    rev: "v4.0.0-alpha.8"
    hooks:
      - id: prettier
        name: Prettier (Markdown & HTML only)

  # Dasel: YAML and JSON schema/structure validator
  - repo: https://github.com/TomWright/dasel
    rev: v2.8.1
    hooks:
      - id: dasel-validate
        name: Dasel YAML/JSON Validator
        files: \.(json|yaml|yml)$

  # Local fast tests
  - repo: local
    hooks:
      - id: test
        name: Run Fast Tests
        entry: just fast-test
        language: system
        pass_filenames: false
        always_run: true
```

#### Ensure latest configs

```bash
prek autoupdate
```

#### Checkpoint with a commit

Stage all new/changed files explicitly. Run prek on them, then ask user to commit manually.

### Beads Integration

If using [beads](./beads.md) for issue tracking, use `bd hooks install --chain` instead of the old `.githooks/` + `core.hooksPath` approach. This chains bd hooks on top of prek so both run on every commit.

```bash
prek install               # Install prek hook
bd hooks install --chain   # Chain bd hooks on top
```

This creates the chain: **bd shim** (`.git/hooks/pre-commit`) → **prek** (`.git/hooks/pre-commit.old`).

**Do NOT use `core.hooksPath`** — it conflicts with prek. If a repo has it set, unset it:

```bash
git config --local --unset-all core.hooksPath
```

### Setup checklist for a new repo

```bash
# 1. Install prek hook
prek install

# 2. If using beads, chain bd hooks
bd hooks install --chain

# 3. Run hooks on all files to establish baseline
prek run --all-files

# 4. Commit any auto-fixed files
git add -A && git commit -m "chore: apply prek formatting"
```
