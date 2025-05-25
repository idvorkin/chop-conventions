Always run `git status` before committing to review staged files. Remove untracked files that shouldn't be committed and use `git reset` to unstage unwanted files.

Standard commit workflow:

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
