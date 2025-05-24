### Intro/Why?

Having pre-checkin tests are a great way to setup a pit of success.

We use precommit to enforce this.

### Environment Setup

#### Important Considerations

⚠️ **Before modifying existing configurations:**

- Always check with the user before changing a pre-existing `.pre-commit-config.yaml`
- Review current hooks and versions to understand what's already in place
- Ask about project-specific requirements or customizations

⚠️ **File type coverage warnings:**

- The current configuration may not cover all file types in your project
- **Biome** supports: JS, TS, JSX, TSX, JSON, JSONC, CSS, GraphQL
- **Prettier** (in this config) is limited to: Markdown, HTML
- **Ruff** supports: Python, .pyi, Jupyter notebooks
- **Dasel** validates: YAML, JSON, YML files

**Check your project for other file types that might need formatting/linting:**

- TOML, XML, SQL, Dockerfile, Shell scripts, etc.
- Consider adding appropriate hooks if needed

#### Defaults

.pre-commit-config.yaml

```yaml
repos:
  # 🐍 Python: Linting and formatting
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

  # ⚡ Biome: JS/TS/JSON/CSS/etc.
  - repo: https://github.com/biomejs/pre-commit
    rev: v2.0.0-beta.5
    hooks:
      - id: biome-check
        name: Biome Lint & Format
        additional_dependencies: ["@biomejs/biome@1.9.4"]
        # ✅ Languages: JS, TS, JSX, TSX, JSON, JSONC, CSS, GraphQL

  # ✨ Prettier: Only for Markdown and HTML
  - repo: https://github.com/pre-commit/mirrors-prettier
    rev: "v4.0.0-alpha.8"
    hooks:
      - id: prettier
        name: Prettier (Markdown & HTML only)
        files: \.(md|html)$

  # 🧾 Dasel: YAML and JSON schema/structure validator
  - repo: https://github.com/TomWright/dasel
    rev: v2.8.1
    hooks:
      - id: dasel-validate
        name: Dasel YAML/JSON Validator
        files: \.(json|yaml|yml)$

  # 🧪 Local fast tests
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

run `pre-commit autoupdate`
