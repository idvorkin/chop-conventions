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

### 2. Navigate and screenshot

```bash
showboat note docs/demo.md "## Home Screen"
showboat exec docs/demo.md bash "rodney open https://your-app.example.com"
showboat exec docs/demo.md bash "rodney screenshot -w 1280 -h 720 docs/home.png"
showboat image docs/demo.md docs/home.png
```

### 3. Interact and capture more

```bash
showboat note docs/demo.md "## Settings Panel"
showboat exec docs/demo.md bash "rodney click 'button[aria-label=Settings]'"
showboat exec docs/demo.md bash "rodney sleep 0.5"
showboat exec docs/demo.md bash "rodney screenshot -w 1280 -h 720 docs/settings.png"
showboat image docs/demo.md docs/settings.png
```

### 4. Accessibility audit

```bash
showboat note docs/demo.md "## Accessibility Audit"
showboat exec docs/demo.md bash "rodney ax-tree --depth 3"
showboat exec docs/demo.md bash "rodney ax-find --role button"
```

### 5. Clean up

```bash
rodney stop
```

### 6. Verify later

```bash
rodney start
showboat verify docs/demo.md
rodney stop
```

## Serving the Document

To view the rendered markdown with images locally:

```bash
# grip renders GitHub-flavored markdown with images
grip docs/demo.md 0.0.0.0:5002

# Then open: http://$(hostname):5002/
```

Or serve the raw files:

```bash
cd docs && python3 -m http.server 5001
# Images at: http://$(hostname):5001/screenshot.png
```

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

## Tips

- **Undo mistakes:** `showboat pop` removes the last entry (failed command, bad screenshot, etc.)
- **Viewport size:** Use `rodney screenshot -w 1280 -h 720` for consistent dimensions
- **Wait for animations:** `rodney sleep 0.5` or `rodney waitstable` before screenshots
- **Element screenshots:** `rodney screenshot-el ".modal"` to capture just a component
- **Selectors:** Prefer `[data-testid=...]` or `[aria-label=...]` for stability
- **Accessibility wins:** `rodney ax-find --role button` quickly reveals unlabeled controls
- **Images with alt text:** `showboat image demo.md '![Settings panel](settings.png)'`
