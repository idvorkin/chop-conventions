---
name: explainers
description: Build an interactive explainer — a standalone web page that teaches one concept by letting the reader play with it. Use when the user says "build an explainer for X", "help me understand Y interactively", or "make a page that demonstrates Z". Ships a minimum HTML scaffold + methodology guide with pointers to reference implementations (dolt-explainer, larry-voice-samples, monitor-explainer, activation-energy-game, religion-evolution-explorer, how-long-since-ai). Prefers GitHub Pages + plain HTML by default; graduates to Vite + TypeScript + React only when interaction genuinely requires it.
allowed-tools: Bash, Read, Edit, Write, Glob, Grep, WebFetch
---

# Explainers

Build a standalone interactive page that teaches one concept by letting the
reader play with it. Everything Igor calls an explainer follows the same
shape — a single repo, a live URL, a research-doc beside a visualization,
GitHub Pages or Surge for hosting.

**Announce at start:** "I'm using the explainers skill to build a new
interactive explainer for _\<topic\>_."

## When to use

- "Build an explainer for X" / "make a page that demonstrates Y"
- "Help me understand Z interactively" when the answer will be a live page
- Simon Willison's [interactive explanations](https://simonwillison.net/guides/agentic-engineering-patterns/interactive-explanations/) pattern applied to a specific topic
- Right after Claude generates something complex and it's clear the
  explanation should be an artifact, not a chat reply

## The five archetypes

Each of the existing explainer repos is the reference implementation for
one archetype. **Read the reference repo live when building a new
instance** — do not extract templates from them (N=1 each; see the
Wait-for-N=2 rule in [`../../CLAUDE.md`](../../CLAUDE.md)).

| Archetype                      | Use when the topic is...                                                | Reference implementation                                                                                                          |
| ------------------------------ | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Catalog / artifact-browser** | A curated list of artifacts the reader scans and interacts with lightly | [`larry-voice-samples`](https://github.com/idvorkin-ai-tools/larry-voice-samples) — voice samples with a speed slider             |
| **Narrative**                  | A sequence of insights with emotional arc, earned through chapters      | [`activation-energy-game`](https://github.com/idvorkin-ai-tools/activation-energy-game) — 8 chapters, Canvas 2D, Nicky Case style |
| **Comparison**                 | Side-by-side differences across N things                                | [`monitor-explainer`](https://github.com/idvorkin/monitor-explainer) — React + SVG, draggable                                     |
| **Timeline / explorer**        | A history, lineage, or multi-actor story                                | [`religion-evolution-explorer`](https://github.com/idvorkin/religion-evolution-explorer) — D3 timeline, pan/zoom                  |
| **Tracker**                    | Live/changing data the reader can filter and monitor                    | [`how-long-since-ai`](https://github.com/idvorkin/how-long-since-ai) — PWA, since-last dashboard                                  |

A sixth non-archetype — **research-dense** — uses plain HTML to present
reproducible demos of something technical. [`dolt-explainer`](https://github.com/idvorkin-ai-tools/dolt-explainer)
is the reference: prose + diagrams + embedded shell transcripts +
`scripts/` directory for self-serve replay. When in doubt, start here.

## Two tiers of scaffolding

1. **Tier 1 — plain HTML** (default). One `index.html` with inline CSS +
   inline JS (or no JS). No npm, no build step. GitHub Pages legacy mode
   serves from `main` branch root. `.nojekyll` keeps Pages from running
   Jekyll. Reference: `dolt-explainer`, `larry-voice-samples`. Template
   in [`templates/plain-html/`](templates/plain-html/).

2. **Tier 2 — Vite + TypeScript + React** (upgrade path). Use ONLY when
   the interaction genuinely exceeds what ~50 lines of vanilla JS can
   hold: animation with a scrubber, D3 layouts, Canvas procedural art.
   Reference: `monitor-explainer`, `religion-evolution-explorer`,
   `activation-energy-game`. No template shipped here — read the
   reference repos when graduating.

## The shape of an explainer repo (regardless of tier)

```
<name>-explainer/
  README.md              pointer to live URL + prereqs
  index.html             (Tier 1) OR src/ + package.json (Tier 2)
  docs/research/         the research notes that preceded the visualization
    findings.md          what you learned before you built
    mental-model.md      one-page cheat sheet for someone who knows a related concept
  scripts/               reproducible scenarios if the topic has runnable demos
  diagrams/              *.puml source + *.svg rendered output
  .github/workflows/     only if Tier 2 or using Surge; Tier 1 + legacy Pages needs none
  justfile               serve, build-diagrams, deploy-check, open-live
  .nojekyll              (Tier 1 only — disables Pages' Jekyll pass)
  .gitignore             runs/, _*, anything per-run
```

## Workflow

See [`methodology.md`](methodology.md) for the full set of principles.
High-level checklist:

1. **Research first.** Do the scenarios, run the commands, read the
   docs, take notes. The explainer is the artifact that falls out of
   understanding — you cannot fake this step.
2. **Write `docs/research/`** before touching `index.html`. This becomes
   the source of the final prose and also a standalone artifact for
   readers who want depth.
3. **Pick the archetype** by comparing against the reference repos. Most
   topics map to research-dense (dolt-explainer shape) on first pass.
4. **Scaffold Tier 1** by copying `templates/plain-html/` and swapping
   the placeholders. Do not start Tier 2 without a concrete reason.
5. **Ship diagrams from day one.** Every explainer includes at minimum
   one PlantUML diagram. Sometimes they're rendered to SVG and embedded;
   sometimes they animate or interact. Use `just build-diagrams` to
   regenerate.
6. **Embed scenarios** if the topic has them. Pre-render transcripts
   into `transcripts/*.txt` and fetch them from `index.html` so the page
   stays small and the transcripts stay authoritative.
7. **Deploy to GitHub Pages** (legacy, `main` branch root). Verify the
   live URL renders and every linked asset fetches.
8. **Link from the blog.** Add a row to
   [`_d/explainers.md`](https://github.com/idvorkin/idvorkin.github.io/blob/main/_d/explainers.md)
   and [`_d/pet-projects.md`](https://github.com/idvorkin/idvorkin.github.io/blob/main/_d/pet-projects.md).

## Principles (one-liners; full reasoning in methodology.md)

- **Research first.** The explainer grows out of the notes.
- **Pictures ≥ prose.** Every argument at the top of the page has a
  diagram next to it.
- **Plain HTML unless proven otherwise.** Ship something static before
  reaching for a build step.
- **One page, one URL, one repo.** No npm unless you need it.
- **Scenarios are runnable.** If you can't `./run-all.sh` the demos, the
  explainer isn't self-sufficient.
- **Shared footer.** Every explainer ends with a footer linking back to
  the source repo, this skill, and `idvork.in/explainers`. Baked into
  `templates/plain-html/index.html` — swap `OWNER/REPO` per project.
- **Respect N=2.** Four archetype reference repos ≠ four sub-templates.
  Read the reference live; do not extract a framework.

## Do not

- Build a templating engine or framework layer above the scaffold.
  Copy, edit, ship. (Wait-for-N=2.)
- Skip the research doc. An explainer without `docs/research/` is a
  demo; they're not the same thing.
- Assume Vite/React. Always check whether plain HTML suffices first.
- Use Surge.sh by default. GitHub Pages legacy is simpler (zero
  secrets, zero workflow) and matches the plain-HTML default.

## Pointers

- [`methodology.md`](methodology.md) — principles and archetype details
- [`templates/plain-html/`](templates/plain-html/) — the minimum starter
- Reference repos: linked in the archetype table above
- Blog context: [Explainers: Interactive Understanding](https://idvork.in/explainers)
