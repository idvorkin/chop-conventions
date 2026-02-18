---
name: showboat
description: Create executable demo documents with screenshots using Showboat + Rodney. Use when the user wants to document an app, create a visual walkthrough, take screenshots of a deployed site, run an accessibility audit, or build self-verifying documentation.
allowed-tools: Bash, Read, Write, Glob, Grep
---

# Showboat - Executable Demo Documents

Create markdown documents that mix commentary, screenshots, and captured command output. These docs are **self-verifying** — `showboat verify` re-runs everything and diffs the output.

## Prerequisites

Both tools are Go binaries. Install if missing:

```bash
go install github.com/simonw/showboat@latest
go install github.com/simonw/rodney@latest
```

Ensure they're on PATH:

```bash
export PATH="$HOME/go/bin:$PATH"
```

### Chrome Setup

Rodney needs a Chrome/Chromium binary. If none is installed system-wide, point it at Playwright's:

```bash
# Find Playwright's Chromium (pick the latest version)
ls ~/.cache/ms-playwright/chromium-*/chrome-linux/chrome

# Set the env var
export ROD_CHROME_BIN=~/.cache/ms-playwright/chromium-<VERSION>/chrome-linux/chrome
```

If Playwright isn't installed either:

```bash
npx playwright install chromium --with-deps
```

## Workflow

### 1. Start Rodney and create the document

```bash
rodney start
showboat init docs/demo.md "App Walkthrough"
```

### 2. Add Table of Contents

After `showboat init`, add a TOC using `<table>` (GitHub/pandoc sanitize `<div>` styles and `<style>` blocks, so use `<table>` and `<p align="center">` instead).

```bash
showboat note docs/demo.md '## Table of Contents

<table><tr>
<td><a href="#home-screen">1. Home Screen</a></td>
<td><a href="#settings-panel">2. Settings Panel</a></td>
</tr><tr>
<td><a href="#accessibility-audit">3. Accessibility Audit</a></td>
<td><a href="#summary">4. Summary</a></td>
</tr></table>

---'
```

### 3. Navigate and screenshot with styled nav bars

Each section gets a styled nav bar with prev/TOC/next pill buttons.

```bash
showboat note docs/demo.md '## Home Screen
<p align="center"><a href="#table-of-contents">📋 TOC</a> · <a href="#settings-panel">Settings Panel →</a></p>'
showboat exec docs/demo.md bash "rodney open https://your-app.example.com"
showboat exec docs/demo.md bash "rodney screenshot -w 1280 -h 720 docs/home.png"
showboat image docs/demo.md docs/home.png
```

### 4. Interact and capture more

```bash
showboat note docs/demo.md '## Settings Panel
<p align="center"><a href="#home-screen">← Home Screen</a> · <a href="#table-of-contents">📋 TOC</a> · <a href="#accessibility-audit">Accessibility →</a></p>'
showboat exec docs/demo.md bash "rodney click 'button[aria-label=Settings]'"
showboat exec docs/demo.md bash "rodney sleep 0.5"
showboat exec docs/demo.md bash "rodney screenshot -w 1280 -h 720 docs/settings.png"
showboat image docs/demo.md docs/settings.png
```

### 5. Accessibility audit

```bash
showboat note docs/demo.md '## Accessibility Audit
<p align="center"><a href="#settings-panel">← Settings</a> · <a href="#table-of-contents">📋 TOC</a> · <a href="#summary">Summary →</a></p>'
showboat exec docs/demo.md bash "rodney ax-tree --depth 3"
showboat exec docs/demo.md bash "rodney ax-find --role button"
```

### 6. Add attribution footer

Every walkthrough gets a footer linking to the showboat skill, the current repo, and the current PR (if any). Detect these dynamically:

```bash
# Get repo and PR info
REPO_URL=$(gh repo view --json url -q .url 2>/dev/null)
REPO_NAME=$(basename "$REPO_URL")
PR_NUM=$(gh pr view --json number -q .number 2>/dev/null)
PR_URL=$(gh pr view --json url -q .url 2>/dev/null)

# Build the footer
FOOTER="---
*Generated with [Showboat](https://github.com/idvorkin/chop-conventions/tree/main/skills/showboat) + [Rodney](https://github.com/simonw/rodney)*"

if [ -n "$REPO_URL" ]; then
  FOOTER="$FOOTER | [$REPO_NAME]($REPO_URL)"
fi
if [ -n "$PR_NUM" ]; then
  FOOTER="$FOOTER | [PR #$PR_NUM]($PR_URL)"
fi

showboat note docs/demo.md "$FOOTER"
```

### 7. Clean up

```bash
rodney stop
```

### 8. Verify later

```bash
rodney start
showboat verify docs/demo.md
rodney stop
```

## Serving the Document

**Use pandoc + python http.server** to render and serve the walkthrough locally. This avoids grip's GitHub API rate limits (60 req/hour unauthenticated).

**Important:** Run from the document's directory so relative image paths resolve correctly.

```bash
cd docs/walk-the-store

# Generate HTML with pandoc (includes nav styling)
pandoc walkthrough.md -o walkthrough.html --standalone \
  --metadata title="Walkthrough Title" \
  --template pandoc-template.html

# Find an available port and serve
running-servers suggest
python3 -m http.server <port> --bind 0.0.0.0
```

The rendered walkthrough will be at `http://$(hostname):<port>/walkthrough.html`

**Pandoc template** — save as `pandoc-template.html` in the document directory:

```html
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>$title$</title>
    <style>
      body {
        max-width: 900px;
        margin: 40px auto;
        padding: 0 20px;
        font-family:
          -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
          sans-serif;
        line-height: 1.6;
        color: #24292e;
      }
      h1 {
        border-bottom: 1px solid #eaecef;
        padding-bottom: 8px;
      }
      h2 {
        border-bottom: 1px solid #eaecef;
        padding-bottom: 6px;
        margin-top: 32px;
      }
      table {
        border-collapse: collapse;
      }
      td {
        padding: 8px 16px;
        border: 1px solid #ddd;
      }
      td a {
        text-decoration: none;
        color: #0366d6;
        font-weight: 500;
      }
      p[align="center"] a {
        display: inline-block;
        padding: 4px 14px;
        border-radius: 16px;
        background: #f0f0f0;
        color: #333;
        text-decoration: none;
        font-size: 14px;
        border: 1px solid #ddd;
      }
      p[align="center"] a:hover {
        background: #dbeafe;
        border-color: #93c5fd;
      }
      pre {
        background: #f6f8fa;
        border-radius: 6px;
        padding: 16px;
        overflow-x: auto;
        font-size: 13px;
      }
      code {
        background: #f6f8fa;
        padding: 2px 6px;
        border-radius: 3px;
        font-size: 85%;
      }
      pre code {
        background: none;
        padding: 0;
      }
      img {
        max-width: 100%;
        border: 1px solid #e1e4e8;
        border-radius: 6px;
        margin: 8px 0;
      }
      hr {
        border: none;
        border-top: 2px solid #0366d6;
        margin: 24px 0;
      }
    </style>
  </head>
  <body>
    $body$
  </body>
</html>
```

### Navigation HTML patterns

**TOC grid** (use `<table>` — GitHub/pandoc sanitize `<div>` styles):

```html
<table>
  <tr>
    <td><a href="#section-slug">1. Section Name</a></td>
    <td><a href="#other-slug">2. Other Section</a></td>
  </tr>
</table>
```

**Section nav bar** (centered pill links with arrows):

```html
<p align="center">
  <a href="#prev-section">← Previous</a> ·
  <a href="#table-of-contents">📋 TOC</a> · <a href="#next-section">Next →</a>
</p>
```

**Note on anchor IDs:** Pandoc generates IDs by lowercasing, removing special chars, and replacing spaces with hyphens. Em dashes become single hyphens, numbers at the start are kept. Example: `## 3. Featured Post — Eulogy` → `#featured-post-eulogy`. Verify with `rodney js` if unsure.

## Showboat Command Reference

| Command                              | Purpose                           |
| ------------------------------------ | --------------------------------- |
| `showboat init <file> <title>`       | Create a new document             |
| `showboat note <file> [text]`        | Add commentary (text or stdin)    |
| `showboat exec <file> <lang> [code]` | Run code, capture output          |
| `showboat image <file> <path>`       | Copy image into document          |
| `showboat pop <file>`                | Remove the most recent entry      |
| `showboat verify <file>`             | Re-run all blocks and diff output |
| `showboat extract <file>`            | Emit commands to recreate the doc |

## Rodney Command Reference (Key Commands)

| Command                                  | Purpose                   |
| ---------------------------------------- | ------------------------- |
| `rodney start [--show]`                  | Launch headless Chrome    |
| `rodney stop`                            | Shut down Chrome          |
| `rodney open <url>`                      | Navigate to URL           |
| `rodney screenshot [-w N] [-h N] [file]` | Take screenshot           |
| `rodney screenshot-el <selector> [file]` | Screenshot an element     |
| `rodney click <selector>`                | Click an element          |
| `rodney input <selector> <text>`         | Type into a field         |
| `rodney js <expression>`                 | Run JavaScript            |
| `rodney wait <selector>`                 | Wait for element          |
| `rodney waitstable`                      | Wait for DOM to stabilize |
| `rodney sleep <seconds>`                 | Sleep N seconds           |
| `rodney ax-tree [--depth N]`             | Dump accessibility tree   |
| `rodney ax-find [--name N] [--role R]`   | Find accessible nodes     |
| `rodney ax-node <selector>`              | Inspect element a11y info |
| `rodney title`                           | Print page title          |
| `rodney url`                             | Print current URL         |
| `rodney pdf [file]`                      | Save page as PDF          |

## Chartroom - Embed Charts

[Chartroom](https://github.com/simonw/chartroom) generates chart PNGs from CSV/JSON/SQL data. No install needed with `uvx`:

```bash
# Bar chart from inline CSV
echo 'name,value
Alice,42
Bob,28' | uvx chartroom bar --csv --title "Sales" -o chart.png

# Line chart from a SQLite database
uvx chartroom line --sql data.db "select date, count from metrics" -o trend.png

# Embed in showboat doc
showboat image docs/demo.md chart.png
```

Supports `bar`, `line`, `scatter`, and `histogram`. Use `-f alt` to generate alt text for accessibility, or `-f markdown` for ready-to-embed markdown with image.

## Remote Viewing with datasette-showboat

For real-time viewing of docs as they're built, set `SHOWBOAT_REMOTE_URL`:

```bash
# Start the viewer
uvx --with datasette-showboat datasette showboat.db --create \
  -s plugins.datasette-showboat.database showboat \
  -s plugins.datasette-showboat.token secret123

# Tell showboat to stream to it
export SHOWBOAT_REMOTE_URL="http://$(hostname):8001/-/showboat/receive?token=secret123"
```

Every showboat command will POST updates to the viewer in real-time.

## Publishing to Gisthost

[Gisthost](https://gisthost.github.io/) renders HTML gists in the browser. It's Simon Willison's improved fork of gistpreview that handles large files and Substack URL mangling.

### Constraints

- **GitHub API truncates gist files over 1MB** — keep `index.html` under 1MB
- **Gists don't support binary uploads via API** — use `git clone` + `git push` for images
- **Gisthost uses `document.write()`** — relative image paths won't resolve. Use absolute raw URLs
- **Gisthost looks for `index.html` by name** — always name your HTML file `index.html`
- **Max 300 files per gist** — split into multiple gists if needed

### Publish Flow

```bash
# 1. Generate HTML from showboat markdown
pandoc walkthrough.md -o index.html --standalone \
  --metadata title="Title" --template pandoc-template.html

# 2. Create the gist with just the HTML (small, no images)
gh gist create --public -d "Walkthrough Title" index.html
# Returns: https://gist.github.com/USER/GIST_ID

# 3. Clone the gist as a git repo
git clone https://gist.github.com/GIST_ID.git /tmp/gist-publish
cd /tmp/gist-publish

# 4. Convert screenshots to JPEG for smaller size
for png in /path/to/screenshots/*.png; do
  name=$(basename "$png" .png)
  magick "$png" -quality 70 "${name}.jpg"
done

# 5. Update image src attributes to use absolute gist raw URLs
#    Replace: src="uuid.png"
#    With:    src="https://gist.githubusercontent.com/USER/GIST_ID/raw/name.jpg"
python3 << 'PYEOF'
import re
GIST_RAW = "https://gist.githubusercontent.com/USER/GIST_ID/raw"
# ... replace src attributes with absolute URLs pointing to GIST_RAW/filename.jpg
PYEOF

# 6. Add images and push via git (API doesn't support binary files)
git remote set-url origin https://x-access-token:$(gh auth token)@gist.github.com/GIST_ID.git
git add *.jpg index.html
git commit -m "Add screenshots"
git push
```

### View the result

```
https://gisthost.github.io/?GIST_ID
```

### Image naming convention

Use numbered, descriptive names that match the walkthrough sections:

```
01-landing.jpg
02-user-menu.jpg
03-load-demo-data.jpg
04-weekly-tracker.jpg
```

### Why not `<base href>`?

A `<base>` tag would let you use relative image paths, but gisthost uses `document.write()` which replaces the entire page — the `<base>` tag affects gisthost's own resource resolution and breaks the page. Always use absolute raw URLs.

### Why not base64-embedded images?

Base64 encoding inflates file size ~33%. A walkthrough with 8 screenshots easily exceeds the 1MB API truncation limit. Gisthost handles truncated files via `raw_url` fallback, but keeping files small is more reliable.

## Tips

- **Undo mistakes:** `showboat pop` removes the last entry (failed command, bad screenshot, etc.)
- **Viewport size:** Use `rodney screenshot -w 1280 -h 720` for consistent dimensions
- **Wait for animations:** `rodney sleep 0.5` or `rodney waitstable` before screenshots
- **Element screenshots:** `rodney screenshot-el ".modal"` to capture just a component
- **Selectors:** Prefer `[data-testid=...]` or `[aria-label=...]` for stability
- **Accessibility wins:** `rodney ax-find --role button` quickly reveals unlabeled controls
- **Images with alt text:** `showboat image demo.md '![Settings panel](settings.png)'`
