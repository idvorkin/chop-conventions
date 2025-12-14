# Running Commands

## Justfile

Put all command scripts into the justfile. Check justfile to see if the command you want to run is already there.

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

## CLI Troubleshooting

| Problem              | Fix                   |
| -------------------- | --------------------- |
| Git output truncated | `git --no-pager diff` |
| head/cat errors      | `unset PAGER`         |
