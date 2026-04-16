# Explainer methodology

The full reasoning behind what `SKILL.md` summarizes. Read this when
building a new explainer and the one-liner principles need unpacking.

## The core claim

An explainer is the artifact that falls out of understanding a concept
well enough to let someone else play with it. The mistake is to treat it
as a presentation task — design the page, then back-fill content. That
produces slop. The path that works every time:

1. Do the thing. Run the commands. Break it on purpose. Take notes.
2. Write the notes down in `docs/research/`.
3. Build the visualization that the notes demand.

If you cannot do step 1 yet, you cannot do step 3 yet. Research is not
optional and not delegable to a reader.

## Narrative structure: back up further than you think

Lead from what the reader doesn't know, not from the mechanism
that makes this explainer interesting. For each concept you'll
reference, add a "what is it" / "why does it need this property"
/ "why doesn't the obvious fix work" section above it. Repeat
until you hit vocabulary a cold reader already owns.

## Archetypes, in depth

### Catalog / artifact-browser

**When the topic is:** a curated collection — voice samples, model
outputs, design variants, dataset entries — where the reader wants to
scan, compare, and lightly interact (play, filter, toggle).

**Shape:** static HTML with a list or grid. Interaction budget: ~30
lines of vanilla JS (a slider, a filter, a toggle). Optional assets
(WAV, images, PDFs) committed to the repo.

**Reference:** [`larry-voice-samples`](https://github.com/idvorkin-ai-tools/larry-voice-samples)
— 17 voice samples with a global speed slider and three custom presets.
Total interactivity budget: one slider, three buttons, ~30 lines of JS.
Hosted on GitHub Pages legacy.

**Smell check:** if you're reaching for React, you're not in catalog
territory anymore.

### Narrative

**When the topic is:** a sequence of insights with an emotional arc,
earned chapter by chapter. The reader feels the concept, doesn't just
see it.

**Shape:** multi-page Vite app, Canvas 2D or SVG, per-chapter
interaction (drag, scrub, toggle). Procedural graphics preferred —
sprites couple content to art.

**Reference:** [`activation-energy-game`](https://github.com/idvorkin-ai-tools/activation-energy-game)
— 8 chapters covering willpower, starting energy, stopping energy,
fibers, death spirals, levers, sandbox. Nicky Case–style explorable
explanations. Scripted via `docs/plans/2026-03-01-activation-energy-script.md`.

**Smell check:** narrative requires a story with stakes. "Drag to
explore" is not a narrative; it's a comparison.

### Comparison

**When the topic is:** "X vs Y" (vs Z). Side-by-side differences the
reader can align and scrub.

**Shape:** React + SVG or plain HTML grid. Minimal state (which items
are selected, how they're aligned).

**Reference:** [`monitor-explainer`](https://github.com/idvorkin/monitor-explainer)
— draggable monitor shapes side by side, aspect-ratio overlay,
"p" vs "K" decoder.

**Smell check:** if the thing isn't visually alignable, it's probably a
table, not a comparison explainer.

### Timeline / explorer

**When the topic is:** a history, lineage, or multi-actor story
unfolding over time.

**Shape:** React + D3 for pan/zoom/filter, or plain HTML with a
left-to-right chronological list.

**Reference:** [`religion-evolution-explorer`](https://github.com/idvorkin/religion-evolution-explorer)
— D3 timeline of how world religions branched and influenced each
other. Filter by branch; compare siblings.

**Smell check:** if the reader doesn't need to reorder or filter over
time, a diagram or comparison is simpler.

### Tracker

**When the topic is:** live or refreshable data the reader dips into
(like a dashboard) — milestones, feeds, leaderboards.

**Shape:** React PWA, data loaded from a JSON file or a feed, filter
UI.

**Reference:** [`how-long-since-ai`](https://github.com/idvorkin-ai-tools/how-long-since-ai)
— days since ChatGPT, GPT-4, Claude 3.5, Gemini 2. PWA, offline
capable.

**Smell check:** trackers imply the data changes. If the data is
static, it's a comparison or a catalog.

### Research-dense (the default non-archetype)

**When the topic is:** a technical mechanism you've just figured out and
want to pin down so you can explain it later. Not entertainment — an
actual artifact of understanding.

**Shape:** plain HTML. Inline CSS + inline JS. Prose paragraphs carry
the argument; diagrams (PlantUML rendered to SVG) do the heavy
visualization; `<details>` blocks collapse embedded scenario
transcripts. `scripts/` directory with reproducible shell commands that
the page's transcripts are captured from.

**Reference:** [`dolt-explainer`](https://github.com/idvorkin-ai-tools/dolt-explainer)
— how beads task state and git code share one GitHub fork via
`refs/dolt/data`, with seven reproducible shell scenarios, six PlantUML
diagrams, and a research doc.

**Start here unless you have a specific reason to pick one of the
five archetypes.** The research-dense shape is the cheapest to build,
the most reusable, and teaches the underlying methodology (research →
diagrams → page) cleanly.

## Why two tiers of scaffolding

A build step costs you onboarding time, a `node_modules` directory, a
deploy workflow with secrets, and a class of failure modes (lockfile
drift, Vite config quirks, CI flakes) that plain HTML does not have.

**Tier 1 (plain HTML)** has zero of those costs. GitHub Pages legacy
mode serves from `main` branch root with no workflow file; `.nojekyll`
tells Pages not to preprocess. You push, Pages serves, done.

**Tier 2 (Vite + React)** pays the build-step cost in exchange for
real interactivity — animations with timelines and scrubbers, D3
layouts, Canvas procedural art. Justified for narrative and
timeline/explorer archetypes. Unjustified for catalogs and most
comparisons and all research-dense explainers.

**When to graduate:** when your inline JS passes ~50 lines and is still
growing; when you need a real framework (D3, Three.js, React state);
when you find yourself building a templating system inside vanilla JS
to avoid HTML duplication.

**Do not graduate prematurely.** The cost of downgrading later (ripping
out Vite) is higher than the cost of upgrading later (adding it when
the need is concrete).

## Diagrams

Every explainer ships at least one diagram. PlantUML is the default:

- Source (`diagrams/*.puml`) is committed alongside the rendered SVG
  (`diagrams/*.svg`), so someone without PlantUML installed can still
  see the image.
- `just build-diagrams` runs `plantuml -tsvg diagrams/*.puml`.
- Install once with `brew install plantuml`.
- Each diagram has a meaningful `alt=` on its `<img>` tag — describing
  the content of the diagram, not "diagram showing X." Someone reading
  the page via screen reader or in a text client needs the argument to
  still land.

Pictures should appear at the top of the page, alongside each argument
they support. "More pictures at the top" is a design brief — the
reader decides whether to keep reading in the first screen, and
diagrams are what compress an argument enough to keep them there.

## Scenarios

If the topic has runnable demos (shell commands, HTTP flows, SQL
queries), ship them as `scripts/NN-scenario-name.sh` files in the repo:

**Offline-first scenarios when the topic is a remote.** If the
mechanism involves a remote server (GitHub, DoltHub, S3), model
it locally with a bare git repo (`git init --bare`) or a
`file://` URL. Removes auth, rate limits, cleanup. Keep a live
variant (e.g. `06b-*-live.sh`) for the real-network sanity check;
default to offline.

- Each script is standalone: sources a shared `lib.sh`, wipes its own
  run directory under `runs/`, sets up fresh state, runs the demo,
  leaves artifacts for the reader to poke at.
- `scripts/run-all.sh` runs them all in order and tees a combined
  transcript.
- `scripts/clean.sh` wipes `runs/`.
- `just rebuild-transcripts` runs every scenario, captures output with
  ANSI escapes stripped, writes to `transcripts/NN-scenario-name.txt`.
- `index.html` embeds the transcripts with `fetch()` so they stay
  authoritative — edit the script, rebuild transcripts, commit; the
  page updates automatically.

See [`../../skills/explainers/`](../explainers/) for the full pattern via
`dolt-explainer` as the reference.

## Hosting

**GitHub Pages legacy (default).** Enable with:

```bash
gh api --method POST "repos/<owner>/<repo>/pages" \
    -f "source[branch]=main" -f "source[path]=/"
```

No workflow file. No secrets. First build takes ~1–2 minutes; subsequent
pushes are ~30 s.

**Surge.sh (opt-in).** Use when you need PR preview URLs (`pr-123-<name>.surge.sh`).
Requires `SURGE_TOKEN` + `SURGE_DOMAIN` per-repo. Pattern for the
workflow lives in `monitor-explainer/.github/workflows/deploy-surge.yml`
and friends — copy from there rather than maintaining a template.

**Do not use Surge by default.** The preview URL is nice, the secret
rotation and per-repo setup is not. Graduate to Surge when you've
actually wanted a preview twice.

## Link the explainer from the blog

The blog has a `/explainers` page at `_d/explainers.md` and a
`/pet-projects` page at `_d/pet-projects.md`. Both have a table that
lists built explainers. Add a row:

```markdown
| [Name](live-url) | What it does | Instead of | [gh-icon](gh-url) |
```

Keep the "Instead of" column short — it's what the reader would have to
do without the explainer ("reading spec sheets", "staring at
`git ls-remote` output").

## Respect the Wait-for-N=2 rule

Four of the five archetypes have exactly one reference repo. Do not
extract templates from a single instance — the N=2 rule in
[`chop-conventions/CLAUDE.md`](../../CLAUDE.md) says "copy-paste bait —
they fork on day one, rot, and rediscover the same bugs on every
downstream consumer." Point at the reference repo and read it live.

The plain-HTML archetype is the exception: it has two instances
(`dolt-explainer` and `larry-voice-samples`), which is the threshold
that earns the minimum template shipped in `templates/plain-html/`.
If a third archetype hits N=2, extract a template at that point — not
before.
