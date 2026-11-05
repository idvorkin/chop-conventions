---
name: outline-editor
description: Build an interactive outline page for restructuring a document or blog post — drag sections between target pages, nest them under headings, mark keep/rewrite/cut, then send the arrangement back as one prompt. Use for any restructure, rewrite, split or merge of one or more markdown docs, and when the user asks to "move sections around", "see it as a table of contents" or "do an outline".
allowed-tools: Bash, Read, Write, Edit
---

# Outline Editor

Rearranging a long post in chat is slow: the reader can't see the shape, and a
list of "move X under Y" instructions goes stale on the next reply. This skill
makes a single HTML page where the reader moves the sections themselves, then
sends the whole arrangement back in one structured prompt.

The page has:

- **A contents tree** (left on wide screens): one line per row, page → heading → section,
  numbered `1 / 1.1 / 1.1.1`. Rows move with drag (pointer events, so iPad touch
  works) or ▲ ▼ ◀ ▶. Dragging sideways changes depth. A row carries the rows
  nested under it.
- **Cards** (right): one per section, with its notes, source, a status picker and a
  "move to" picker. Same model as the tree, so a change in one shows in the other.
- **Views**: Outline (tree + cards), By status (drag between statuses), Original order
  (read-only, shows where each section ended up).
- **Pages** that set a status: dropping into a page with `"status": "cut"` marks it cut;
  dragging it back restores its starting status.
- A tray for topics that don't exist yet, `+ Heading` to group rows, optional
  suggested headings, and an optional set of single-choice questions.
- Saved in `localStorage` (per `storage_key`), so a reload keeps the arrangement.

## When to use

- Restructuring, splitting or merging posts or docs ("split the history out",
  "these two pages overlap", "modernize this post").
- The reader wants to drag things around or see the shape as a table of contents.

Skip it for a single-section edit; just edit the file.

## Make the page

`outline.py` is a uv-script (Typer). Run it directly so the shebang resolves deps.

```bash
SK=~/.claude/skills/outline-editor   # or the chop-conventions checkout
# one page per markdown file, each heading becomes a section card
$SK/outline.py from-md post.md [other.md ...] --json outline.json --html .lavish/outline.html
# after editing outline.json by hand
$SK/outline.py render outline.json --html .lavish/outline.html
```

`from-md` skips code fences and front matter, uses the front-matter `title:` as
the page name, drops a lone `#` title heading (`--keep-h1` keeps it), and sets
depth from heading level. Ids are `A01, A02…` for the first file, `B01…` for the
second. It adds an empty **Cut** page and a **Not placed** tray.

Then make it worth reviewing. Edit the JSON:

- `why` on each item: your one-line reasoning (stale facts, duplicates, where it
  should go). This is what the reader decides from.
- `status`/`page` on each item: your proposed first pass.
- Extra pages (for example a new `history` page with `"status": "history"`), new
  topics in the tray (`"page": "tray", "status": "new"`), `seed_headings`,
  `intro_html` (a TL;DR) and `extra_html` (reference material, questions).

Open it with Lavish (`npx -y lavish-axi <page>.html`) so **Send outline** and the
question buttons can queue prompts. Opened without Lavish, the page still works
and the arrangement still saves, but sending says "Open this page in Lavish".

## JSON shape

```jsonc
{
  "title": "Restructure: My post",
  "subtitle": "optional line under the title",
  "storage_key": "outline-editor:my-post", // bump it when the items change a lot
  "default_status": "keep", // used when leaving a status-setting page
  "statuses": [
    { "key": "keep", "label": "keep", "note": "stays", "color": "#2e7d4f" },
  ],
  "pages": [
    { "key": "post", "title": "/my-post", "sub": "_d/my-post.md" },
    { "key": "cut", "title": "Cut", "status": "cut" }, // dropping here sets status
    { "key": "tray", "title": "Not placed", "tray": true }, // no status change
  ],
  "origins": [{ "key": "post", "title": "My post, as written" }], // Original order view
  "items": [
    {
      "id": "A01",
      "title": "Intro",
      "page": "post",
      "origin": "post",
      "home": "post",
      "status": "keep",
      "depth": 0,
      "why": "reasoning",
      "src": "my-post.md:12",
    },
  ],
  "headings": [{ "title": "Part one", "page": "post", "before": "A01" }], // optional, pre-placed
  "seed_headings": {
    "page": "post",
    "label": "Add suggested headings",
    "titles": ["…"],
  },
  "prompt": "optional text that starts the Send outline prompt",
  "question_prefix": "optional prefix for question answers",
  "intro_html": "<h2>TL;DR</h2>…",
  "extra_html": "<form class=\"q\" data-lavish-question=\"q1\">…radios…<button>Queue answer</button><span class=\"status\"></span></form>",
}
```

`home` is the page a section returns to when its status changes from a
status-setting value. Item ids must be unique and must not start with `~h`
(that prefix is reserved for headings added on the page). `render` validates
pages, statuses and ids and exits 1 on problems.

## What Send outline sends

One `window.lavish.queuePrompt` call, tag `outline`, `queueKey: "outline"` (a
re-send replaces the unsent copy). The prompt text is readable on its own:

```
/my-post (8)
  1 A02 Picking a plant [keep]
    1.1 A03 Light [keep]
  2 # Part one
    2.1 A01 Why keep plants [rewrite]
Not placed (1)
  1 N01 Pests [new]
```

`data.outline` carries the same thing structured: a list of
`{page, key, items: [{kind: "section"|"heading", num, depth, parent, title, id, status, started}]}`,
where `parent` is the parent section's id (or the heading's title) and `started`
is the section's original page and status. Account for every section id when
acting on it.

## Example

`example/` has two neutral markdown files and the `outline.json` built from them
(plus a tray topic, suggested headings and one question). To try it:

```bash
$SK/outline.py render $SK/example/outline.json --html /tmp/outline-example.html
```

## Tests

`test_outline.py` covers heading parsing, ids and depths, validation and the
`</script>` escaping (`just fast-test` runs it). The page itself was checked in
Playwright: drag in the tree with a depth change, indent, outdent, a heading
moving as a block, send, reload, and no sideways scroll at 390px.
