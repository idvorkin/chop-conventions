---
name: gist-image
description: Use when you need to host images (screenshots, diagrams, PNGs) on GitHub for use in PR descriptions, issues, or markdown docs. Solves the problem that `gh gist create` rejects binary files.
allowed-tools: Bash, Read, Write
---

# Gist Image Upload

Host binary images on GitHub via gists. Gists are git repos — you can clone them and push binary files that the web UI and CLI reject.

## Setup

Ensure `.tmp/` is ignored in the project (gist clones live here). Use the
local-only `.git/info/exclude`, not the tracked `.gitignore` — this is a
per-machine scratch dir and must not land in the project's branch history.
`--git-common-dir` resolves the shared git dir, so it also works from a linked
worktree:

```bash
EXCLUDE="$(git rev-parse --path-format=absolute --git-common-dir)/info/exclude"
mkdir -p "$(dirname "$EXCLUDE")"
grep -qxF '.tmp/' "$EXCLUDE" 2>/dev/null || echo '.tmp/' >> "$EXCLUDE"
```

Skip this if the project's `.gitignore` already covers `.tmp/`.

## Quick Reference

```bash
GIST_NAME="pr-135-screenshots"  # descriptive name for this batch

# 1. Create gist with placeholder
GIST_URL=$(gh gist create --desc "$GIST_NAME" - <<< "# $GIST_NAME" 2>&1 | grep gist.github.com)
GIST_ID=$(basename "$GIST_URL")
GH_USER=$(gh api user --jq '.login')

# 2. Clone into .tmp/<name>, add images, push
git clone "$GIST_URL" ".tmp/$GIST_NAME"
cp /path/to/*.png ".tmp/$GIST_NAME/"
# git -C keeps the shell in the project root even when a step fails;
# the quoted pathspec is matched by git inside the clone, not by the shell
git -C ".tmp/$GIST_NAME" add -- '*.png' &&
  git -C ".tmp/$GIST_NAME" commit -m "add images" &&
  git -C ".tmp/$GIST_NAME" push

# 3. Use raw URLs (permanent as long as gist exists)
# https://gist.githubusercontent.com/$GH_USER/$GIST_ID/raw/FILENAME.png
```

## URL Format

```
https://gist.githubusercontent.com/{user}/{gist_id}/raw/{filename}
```

GitHub serves these with correct `content-type: image/png` headers, so they render inline in markdown:

```markdown
![Screenshot](https://gist.githubusercontent.com/user/abc123/raw/screenshot.png)
```

## Grouping Images per PR

Put all images for a PR in a single gist. Name the gist after the PR for traceability:

```bash
GIST_NAME="pr-135-row-selection"
# Follow Quick Reference steps above with this name
# All screenshots end up in .tmp/pr-135-row-selection/
```

One `gh gist delete` removes them all. One `rm -rf .tmp/pr-135-row-selection` cleans up locally.

## Updating PR Descriptions

A screenshot that shows the change belongs in the human summary at the top, above the folded agent write-up (the details-summary rule in `claude-md/global.md`).

Setting `body` **replaces the whole description** — never PATCH it with only
the screenshots block. Fetch the current body, prepend the block, and write the
combined text back:

```bash
BODY_FILE=$(mktemp)
{
  printf '%s\n\n' "## Screenshots"
  printf '%s\n\n' "![feature](https://gist.githubusercontent.com/$GH_USER/$GIST_ID/raw/feature.png)"
  gh pr view NUMBER --repo OWNER/REPO --json body --jq .body
} > "$BODY_FILE"
gh pr edit NUMBER --repo OWNER/REPO --body-file "$BODY_FILE"
rm -f "$BODY_FILE"
```

## Cleanup

```bash
gh gist delete $GIST_ID --yes
rm -rf ".tmp/$GIST_NAME"
```

## Common Mistakes

- **Forgetting to push** — images only exist locally until you `git push`
- **Using gist web URL instead of raw** — `gist.github.com/...` shows the HTML page, not the image. Use `gist.githubusercontent.com/.../raw/...`
- **Creating public gists for private repos** — use `gh gist create` without `--public` (default is secret)
- **Treating "secret" as private** — a secret gist is only _unlisted_. Its raw URLs need no authentication, so anyone with the link (including anyone who can read the PR or issue it is pasted into) can fetch the image. Don't host screenshots containing credentials, customer data, or other sensitive content this way
- **`cd`-ing into the gist clone** — a failed `cd dir && … && cd -` chain leaves the shell inside the clone; use `git -C` as above so the project root stays the cwd
- **Overwriting the PR description** — `-f body=` / `--body` replace the whole text; combine with the existing body first
