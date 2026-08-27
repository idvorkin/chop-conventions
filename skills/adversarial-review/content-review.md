# Content review: rubric-rewrite mode

*Loaded on demand by [`SKILL.md`](SKILL.md). Read this when you have a written style rubric and prose that needs to meet it.*

Everything here reuses the parent skill's engine (`codex exec -s read-only`), output contract, and convergence discipline. What changes is the target and the constraint: the reviewer judges against **a rubric file you pass in**, and the author may change **prose only** — every fact stays byte-identical.

## There is no separate "blog content skill"

If you came here looking for one: **the rubric file IS the skill.** A style guide like a blog's `content_guidelines.md` already encodes the rules; this mode consumes it directly rather than paraphrasing it into a second document that immediately drifts from the first. Point `--rubric` at the guide and the guide stays the single source of truth.

Nothing here hardcodes a path. This repo's skills are cross-project — the rubric is always a **parameter**. (A fuller *authoring* skill — one that helps write the post in the first place, rather than review it — is tracked separately as igor2 bead `igor2-88g.35`. This mode reviews; it does not draft.)

## The rubric file contract

`prepass.py rubric` extracts banned phrases mechanically, so a rubric can opt into the machine pre-pass by following one convention:

- A line containing **❌** opens a banned block; a line containing **✅**, or a new markdown heading, closes it.
- Inside the block, every **double-quoted string on a bullet** is a banned phrase.
- Bullets starting `Instead` are the remedy, not the ban — skipped.
- Fenced code is skipped entirely (a "wrong structure" sample quotes layout, not phrasing).
- Phrases shorter than `--min-phrase-len` (default 4) are dropped — they're illustrative pronouns like `"I"` / `"we"`, not literal strings to grep.

A rubric that doesn't follow the convention still works for the **LLM** lens; it just doesn't get the free regex sweep. `prepass.py` warns on stderr when a rubric yields zero phrases, which is your signal the format didn't match.

Verified against a real 500-line style guide: **32 banned phrases extracted, zero false positives** after the fence and bullet guards.

## The facts-frozen contract

A de-slop rewrite is allowed to change how something is said and nothing about what is true. Concretely:

1. **Numbers, tables, front matter, and fenced code are byte-identical** to the pre-rewrite baseline.
2. **Word count must go DOWN.** A "tightening" pass that grows the document has failed on its face.
3. Headings and internal anchors survive, or every link to them breaks.

This is mechanically checkable, so check it mechanically — never by eye:

```bash
cp post.md ~/tmp/agent/skill/adversarial-review/baseline.md   # BEFORE the rewrite
# … rewrite happens …
skills/adversarial-review/tools/prepass.py frozen post.md \
  --baseline ~/tmp/agent/skill/adversarial-review/baseline.md
```

It reports `number-dropped` / `number-added` (per numeral, with counts), `front-matter-changed`, `table-changed`, `code-changed`, and `word-count-not-reduced`, plus an informational line with the actual percentage cut. Today's production rewrite: **17% shorter, facts byte-identical.**

Freeze the baseline *before* the first rewrite hunk lands. Reconstructing it from git afterwards works but invites reviewing the wrong version.

## The overseer diff is not optional

The rewriting agent proposes hunks. **You read every hunk as a diff before accepting it.** In today's run this rejected two Codex hunks — one that fabricated a detail not present in the original, one that was simply more awkward than the sentence it replaced.

This is not ceremony. A style rewriter is optimising for "sounds less like AI," and the cheapest way to satisfy that objective is to *invent a concrete-sounding specific* or to reword into something merely different. `prepass.py frozen` catches invented **numbers**; it cannot catch an invented adjective, a claim that drifted, or prose that got worse. Only a human-or-overseer read of the diff catches those.

Practical shape:

```bash
git diff --word-diff=color -- post.md      # word-level, so reworded lines read clearly
```

Accept a hunk only if it (a) removes rubric-banned phrasing or genuine bloat, (b) adds no new claim, and (c) reads better — not merely differently. Reject the rest and say why; declines go back to the reviewer like any other decline.

## Running the mode

```bash
REVIEW=~/tmp/agent/skill/adversarial-review
mkdir -p "$REVIEW"
cp post.md "$REVIEW/baseline.md"
cp post.md "$REVIEW/review-target.md"

# Step 0 — machine sweep: banned phrases + leaks + dead links, seconds, ~free
skills/adversarial-review/tools/prepass.py all "$REVIEW/review-target.md" \
  --rubric /path/to/content_guidelines.md

# Pass 1 — parallel lenses (style-rubric is the one that matters here;
# keep disclosure running too, since prose edits can reintroduce a leak)
codex exec -s read-only --skip-git-repo-check -C "$REVIEW" \
  -o "$REVIEW/p1-style.txt" "$(cat "$REVIEW/prompt-style.txt")"
```

The style lens prompt keeps the parent skill's anti-agreeableness frame and output contract, and swaps in the rubric:

```
Adversarial style review of the file at <review-target-path>.
The rubric is at <rubric-path>. Read the rubric FIRST, then the target.

Do NOT be agreeable. Your job is to find writing that violates the rubric,
not to bless the file. Assume there IS slop and go find it.

Hunt for:
  - Phrases and patterns the rubric explicitly bans.
  - Voice drift: the rubric names a voice; flag where the text leaves it.
  - Bloat: sentences that could lose a third of their words and say the same
    thing. Quote the tighter version.
  - Inconsistency: mixed person, mixed tone, mixed metaphor.

CONSTRAINT — this is a style pass. Do NOT propose changing any number,
table, code block, front-matter field, or factual claim. If a fact looks
wrong, report it as a NOTE; do not rewrite it.

Output contract — follow it exactly:

BLOCKERS: numbered. Each: line number + the exact offending text +
  the rubric rule it breaks + a concrete tighter replacement.
NOTES: numbered, same format — real but non-blocking.
Finish with exactly one line:
  VERDICT: RUBRIC CLEAN
or
  VERDICT: NEEDS REWRITE
```

Then the parent skill's loop applies unchanged: scripted `assert`-guarded fixes, an itemized change list including DECLINED items handed back each pass, cold re-reads, and a distrusted early-clean verdict.

## The gate for this mode

`VERDICT: RUBRIC CLEAN` is necessary but **not sufficient**. The mode is done when all three hold:

1. The reviewer returns `RUBRIC CLEAN` on a cold re-read.
2. `prepass.py frozen` reports no `number-*`, `front-matter-changed`, `table-changed`, `code-changed`, or `word-count-not-reduced`.
3. The overseer has read the full diff and accepted every remaining hunk.

Any one of these alone will ship a worse document than you started with.

## Distill, don't accrete

When the rewrite is on an *existing* document, tightening beats appending. Ask whether overlapping prose can be replaced by a tighter version of both, whether an existing paragraph can absorb the new idea, and whether a proposed new section really wants to be one sharpened sentence. Long-and-loose is the default drift. A document that grows should mostly grow sharper, not fatter — which is exactly why word count is a hard gate here and not a suggestion.

## Anti-patterns specific to this mode

- Copying a rubric's rules into the prompt instead of passing the rubric file — the copy drifts from the source the day it's made.
- Hardcoding one project's rubric path into a shared skill.
- Accepting rewrite hunks in bulk without reading the diff.
- Treating `prepass.py frozen` passing as proof the facts survived — it proves the *numbers* survived. Invented adjectives and drifted claims are diff-review's job.
- Letting a style pass "fix" a fact it thinks is wrong. Facts get reported as NOTES and handled in a separate adversarial-verify loop.
- Capturing the baseline after the rewrite started.
