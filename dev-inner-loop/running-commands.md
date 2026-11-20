# Running Commands

## Justfile

Put all command scripts into the justfile. Check justfile to see if the command you want to run is already there.

### Standard entry points

Every repo that builds something offers the same four names, so people, setup scripts and agents can run them in any repo without reading its justfile first:

| Recipe         | Does                                                                                                                              |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `just build`   | The quickest full compile of the main target (a Mac app bundle, an iOS app for the simulator, a package)                          |
| `just test`    | The fast first rung of the test ladder: host tests, seconds not minutes                                                           |
| `just install` | Puts the app where it is used (`/Applications`, `~/.local/bin`). Skip it when there is nothing to install, as for an iOS-only app |
| `just run`     | Builds and opens it                                                                                                               |

Keep the repo's own descriptive recipe names and point the standard ones at them with aliases, rather than renaming:

```just
# Shared entry points: just build, test, install, run.
alias build := app
alias run := run-sim
```

A recipe that takes arguments, or that needs a device, is not a standard entry point: `build` and `run` must work with no arguments on a Mac with only Xcode and the simulators.

## Python Scripts

When running python scripts with UV script shebangs, run them directly:

```bash
./foo.py
```

## Tmux + Neovim

Open files for user review in a tmux split (keeps Claude's terminal available):

```bash
# Open file in 2/3 width split on right
tmux split-window -h -l 66% "nvim /path/to/file"

# Open git diff in nvim
tmux split-window -h -l 66% "nvim -c 'Git diff'"

# Open specific file at line number
tmux split-window -h -l 66% "nvim +42 /path/to/file"

# Vertical split (top/bottom) - useful for logs
tmux split-window -v -l 50% "nvim /path/to/file"
```

This is useful when:

- Showing retros, diffs, or long files for user review
- User wants to edit while Claude continues working
- Comparing files side-by-side with the terminal

## Just + npm Pattern for PWAs

For PWA projects using npm, use this pattern to ensure proper dependency handling:

### Justfile

```just
# Justfile

default:
    @just --list

dev:
    npm run dev-called-from-just

build:
    npm run build-called-from-just

# test depends on build - ensures version info and types are current
test: build
    npm run test-called-from-just

e2e:
    npx playwright test

deploy: test build
    # your deploy command
```

### package.json scripts

```json
{
  "scripts": {
    "dev": "just dev",
    "dev-called-from-just": "vite",
    "build": "just build",
    "build-called-from-just": "bash scripts/generate-version.sh && tsc -b && vite build",
    "test": "just test",
    "test-called-from-just": "vitest run"
  }
}
```

### Why this pattern?

1. **Dependency handling**: `just test` automatically runs `build` first, ensuring TypeScript compiles and version info is generated
2. **Single entry point**: Both `npm run build` and `just build` work correctly
3. **Self-documenting**: Script names like `build-called-from-just` make it clear these shouldn't be called directly
4. **No duplicate work**: Version generation happens in build, test just runs tests

## CLI Troubleshooting

| Problem              | Fix                   |
| -------------------- | --------------------- |
| Git output truncated | `git --no-pager diff` |
| head/cat errors      | `unset PAGER`         |
