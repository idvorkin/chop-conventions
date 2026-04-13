---
name: gist-image
description: Use when you need to host images (screenshots, diagrams, PNGs) on GitHub for use in PR descriptions, issues, or markdown docs. Solves the problem that `gh gist create` rejects binary files.
---

# Gist Image Upload

Host binary images on GitHub via gists. Gists are git repos — you can clone them and push binary files that the web UI and CLI reject.

## Setup

Ensure `.tmp/` is gitignored in the project (gist clones live here):

```bash
grep -q '^\.tmp/' .gitignore 2>/dev/null || echo '.tmp/' >> .gitignore
```

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
cd ".tmp/$GIST_NAME" && git add *.png && git commit -m "add images" && git push && cd -

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

```bash
gh api repos/OWNER/REPO/pulls/NUMBER -X PATCH -f body="
## Screenshots
![feature](https://gist.githubusercontent.com/$GH_USER/$GIST_ID/raw/feature.png)
"
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
- **Forgetting `cd -`** — always return to the project root after pushing to the gist clone
