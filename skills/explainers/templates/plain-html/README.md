# EXPLAINER NAME

One-line description of what this explainer covers.

**Live:** https://OWNER.github.io/REPO/

## The problem

One-paragraph problem statement — what the reader has trouble with
that this explainer addresses.

## The answer

One-paragraph summary of the mechanism or insight.

## Layout

```
index.html                   single-page explainer (embeds transcripts)
docs/research/               research notes that preceded the page
  findings.md                full writeup
  mental-model.md            one-page cheat sheet for the adjacent concept
diagrams/                    *.puml sources + rendered *.svg images
scripts/                     reproducible scenarios + shared lib
transcripts/                 pre-rendered scenario output (fetched by index.html)
runs/                        materialized state from scenario runs (gitignored)
```

## Prereqs

- (List tools the scenarios need, e.g., `dolt`, `git`, `plantuml`)

## Running

```bash
./scripts/run-all.sh           # run every scenario, write runs/
./scripts/clean.sh             # wipe runs/
just rebuild-transcripts       # re-render transcripts/ from a fresh run
just build-diagrams            # render diagrams/*.svg from .puml sources
```

## Hosting

GitHub Pages, legacy mode, served from `main` branch root. `.nojekyll`
keeps Pages from running Jekyll over plain HTML.
