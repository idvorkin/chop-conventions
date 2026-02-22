Always run `git status` before committing to review staged files. Remove untracked files that shouldn't be committed and use `git reset` to unstage unwanted files.

### Keeping distinct commits distinct

When making commits propose to the user to split them to be logical users.

### Avoid mixing linting/formatting with edits.

Before editing a file, try to run the prek hooks on it.
If something is changed, propose committing it by itself.

E.g.

```
git stash # store other changes
git add file_to_change
prek run --files file_to_change
git add file_to_change  # if it was changed
git commit -m "chore: apply formatting to file_to_change"
```

### Seeing full diffs

If you are getting partial diff output it's because I'm using a funny terminal, pipe through /bin/cat instead.

### Writing nice commmit messages

Since you are calling the terminal commands, write the commit message to a temp file called COMMIT_MSG,
Make sure you overwrite COMMIT_MSG, confirm it's correct

Include Sections Summary, The Issue or Feature, The Fix, Testing Results, Backwards Compat Risks

Then check with user if they like the commit message, or want to change things

Then commit with that COMMIT_MSG

An example of a good commit message

````markdown
"fix: display cycling commands dnext/dprev use arrangement index - yabai display --focus expects arrangement index not display ID"

```

## The Problem

The refactored `_cycle_display()` function was using display IDs (like 1, 10) for both window movement and display focusing. However, yabai has different expectations:

- **Window movement** (`-m window --display`) expects the display ID
- **Display focusing** (`-m display --focus`) expects the arrangement index (1-based sequential numbering)

## The Fix

I updated the `_cycle_display()` function to:

1. **For window actions**: Continue using `target_display.id` (the actual display ID)
2. **For display actions**: Use `target_index + 1` (the arrangement index, which is 1-based)

## Testing

Both commands now work correctly:

- `y dnext` - cycles to the next display
- `y dprev` - cycles to the previous display

The fix maintains the DRY principle while correctly handling the different parameter requirements for yabai's window vs display commands. This follows the dev-inner-loop conventions by fixing the bug and ensuring the code works as expected.
```
````

### Standard commit workflow:

```bash
git status
git add specific_file.py another_file.js
git status  # Verify only intended files are staged
echo "Summary: Committing bug fix to authentication module"
git commit -m "Fix authentication timeout issue"
```

When you accidentally stage everything with `git add -A`:

```bash
git add -A  # Accidentally staged everything
git status  # Review what was staged
git reset   # Unstage everything
git add intended_file.py  # Stage only intended files
git status  # Verify staging
echo "Summary: Adding new feature X to module Y"
git commit -m "Add feature X"
```

**Never commit without reviewing** what's staged and providing a summary of changes. Avoid blind commits like `git add -A && git commit -m "Some changes"`.

Try to avoid grouping independent changes in 1 checkin. If it makes sense, offer the user to split them into logical commits

Run prek before trying to commit and after staging.
