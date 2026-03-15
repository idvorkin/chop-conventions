---
name: gist-image
description: Use when you need to host images (screenshots, diagrams, PNGs) on GitHub for use in PR descriptions, issues, or markdown docs. Solves the problem that `gh gist create` rejects binary files.
---

# Gist Image Upload

Host binary images on GitHub via gists. Gists are git repos — you can clone them and push binary files that the web UI and CLI reject.

## Quick Reference

```bash
# 1. Create gist with placeholder
GIST_URL=$(gh gist create --desc "screenshots for PR #123" - <<< "# Images" 2>&1 | grep gist.github.com)
GIST_ID=$(basename "$GIST_URL")

# 2. Clone, add images, push
git clone "$GIST_URL" /tmp/gist-images
cp /path/to/*.png /tmp/gist-images/
cd /tmp/gist-images && git add *.png && git commit -m "add images" && git push

# 3. Use raw URLs (permanent as long as gist exists)
# https://gist.githubusercontent.com/USERNAME/GIST_ID/raw/FILENAME.png
```

## URL Format

```
https://gist.githubusercontent.com/{user}/{gist_id}/raw/{filename}
```

GitHub serves these with correct `content-type: image/png` headers, so they render inline in markdown:

```markdown
![Screenshot](https://gist.githubusercontent.com/user/abc123/raw/screenshot.png)
```

## Usage in PR Descriptions

```bash
# After pushing images, update PR body
gh api repos/OWNER/REPO/pulls/NUMBER -X PATCH -f body="
## Screenshots
![feature](https://gist.githubusercontent.com/user/$GIST_ID/raw/feature.png)
"
```

## Grouping Images per PR

When uploading verification screenshots for a PR, put all images in a single gist. Name the gist after the PR for traceability:

```bash
gh gist create --desc "PR #135 row-selection screenshots" - <<< "# PR #135 Screenshots"
# Then add all screenshots to this one gist
cp normal.png selected.png comparison.png /tmp/gist-images/
```

This keeps related images together and makes cleanup easy — one `gh gist delete` removes them all.

## Cleanup

```bash
# Delete gist when no longer needed
gh gist delete $GIST_ID --yes
rm -rf /tmp/gist-images
```

## Common Mistakes

- **Forgetting to push** — images only exist locally until you `git push`
- **Using gist web URL instead of raw** — `gist.github.com/...` shows the HTML page, not the image. Use `gist.githubusercontent.com/.../raw/...`
- **Creating public gists for private repos** — use `gh gist create` without `--public` (default is secret)
