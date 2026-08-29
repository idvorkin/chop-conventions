---
name: cartoonist
description: Summon Gutter, the cartoonist seat, for any raccoon art on Igor's blog — Den strips, cutouts, post illustrations, the weekly Den pitch. The seat lives in larry-blog/gutter/; this skill only wakes him and carries the invocation shape.
allowed-tools: Agent, SendMessage, Bash
---

# Cartoonist — summon Gutter

Gutter is a seat (a persistent identity with his own memory), not a recipe.
Everything he knows lives in `~/gits/larry-blog/gutter/` — `SEAT.md` is who
he is, `characters.md` the canon, `contract.md` the panel geometry,
`recipe.md` how the pictures are made, `laurels.md`, `jobs/` his memory,
`pitches/` the Sunday pitches. **Do not copy any of that into this skill or
into a prompt by hand; `wake.sh` prints it.**

## Hard rules

- **Gutter never reads igor2.** His worktree is in `larry-blog`; the brief
  carries everything he needs to know about Igor's week, written by Larry as
  blog-grade lines. Never give him a path under `~/gits/igor2`.
- One live agent per job or pitch, and you TALK to it: `SendMessage` to the
  agent for critiques and questions; it messages `main` back ("Sheet ready",
  questions about the brief). Do not re-dispatch per step.
- He writes his job entry before the sheet goes out and sends it to Larry
  as text; Larry files it as `~/gits/igor2/gutter/jobs/<date>-<slug>.md`
  (pitches as `pitches/<year>-Www.md`) and commits it there. The entry
  never goes into the blog repo. A job with no entry did not happen.
- Sheets carry no recommendation; Igor picks on the Cockpit.

## Wake him

1. Worktree for the job, off upstream:

   ```bash
   cd ~/gits/larry-blog && git fetch upstream && git worktree add .worktrees/<slug> -b <slug> upstream/main && (cd .worktrees/<slug> && just worktree-init)
   ```

2. Build the prompt: the bundle, then the brief.

   ```bash
   BUNDLE=$(~/gits/larry-blog/gutter/wake.sh --memory ~/gits/igor2/gutter)
   ```

   The `--memory` dir is Larry's private repo; its text goes into the
   prompt, the path never does.

   Brief = bead id · Igor's words verbatim · deliverable (strip / cutout /
   illustration) · where it lands · constraints · for a pitch, the 3–5
   public-safe moments of the week.

3. Dispatch: `Agent` (opus, `run_in_background: true`), prompt =
   `$BUNDLE` + a line `You are Gutter. Work only in <worktree path>. The brief:` + the brief +
   `When the sheet is ready, message main with the variant paths and one line per variant; wait for critique or the pick.`

4. Converse. Critique with `SendMessage` to the agent; two rounds by default.
   When he says "Sheet ready", file the ask:

   ```bash
   cd ~/gits/igor2 && decision_queue/ask.py "<question>" --context "<brief, one paragraph>" \
     --option "a | <one line>" --option-image "a=<abs path A.webp>" \
     --option "b | <one line>" --option-image "b=<abs path B.webp>" \
     --option "c | <one line>" --option-image "c=<abs path C.webp>" --parent <bead>
   ```

   and relay the same images to Telegram (`reply` with `files`). No
   `--recommend`.

5. After the pick, message him the letter; he finishes (final webp,
   per-panel exports, alt text, include line, entry updated) — Larry files
   the updated entry in igor2 — and pushes the branch; Larry opens the PR
   per larry-blog's CLAUDE.md and closes the bead.

## The Sunday pitch

Larry's side of the loop, every Sunday ~07:30 PDT: read the week (journals,
week report, Telegram) and write the pitch brief — three to five moments as
lines Igor would put on the blog himself, no journal text. Wake Gutter with
it; he drafts three premises; critique; he sends the pitch text; Larry
files it as `~/gits/igor2/gutter/pitches/<year>-Www.md` and says "Sheet
ready"; file the ask with no images and no recommendation. A pick becomes a
job.
