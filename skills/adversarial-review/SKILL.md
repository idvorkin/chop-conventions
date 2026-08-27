---
name: adversarial-review
description: Drive an external agent through hostile review-until-clean of a document or artifact before it ships. Two modes — adversarial-verify (hunt leaks, false claims, contradictions until the verdict flips to SAFE) and rubric-rewrite (de-slop prose against a rubric file with the facts frozen). Use before publishing a doc/gist/PR/post/report that could carry a leak (private IPs, hostnames, tokens, real names) or a factual/logic error, or when prose needs to be tightened against a written style rubric.
allowed-tools: Agent, Bash, Read, Edit, Grep, Glob
---

# Adversarial Review

Run an **external, hostile** agent over a document and iterate until it can find nothing wrong. Each pass the reviewer re-reads from scratch, stays adversarial to the *current* wording, and emits a hard verdict. You apply surgical fixes between passes and re-scan. Stop when the verdict flips to SAFE — not before.

This is the external-adversary sibling of `architect-review`. Where architect-review drives a spec toward *internal design convergence* (a friendly reviewer hardening decisions), adversarial-review is a *stranger trying to break your artifact before the world does* — leaks, contradictions, false claims, unsafe advice. The reviewer is told, explicitly, that its job is to find problems, not to agree.

## Two review types

Both use the same engine, the same output contract, and the same convergence gate. They differ in what the reviewer is hunting and what the author is allowed to change.

| | **adversarial-verify** | **rubric-rewrite** |
|---|---|---|
| Hunts | leaks, false claims, contradictions, bad arithmetic | AI-slop phrasing, bloat, voice drift |
| Judged against | reality / a stated source file | a **rubric file** you pass in |
| Author may change | anything needed to fix a finding | prose only — **facts stay frozen** |
| Gate | `VERDICT: SAFE TO PUBLISH` | rubric-clean **and** word count down, facts byte-identical |

**rubric-rewrite is documented in [`content-review.md`](content-review.md)** — read it when you have a rubric file in hand. The rest of this file is the shared protocol plus adversarial-verify.

## When to use

- Before shipping something with an **audience and a blast radius**: a gist or blog post headed public, a PR description, a report, a README, a config or transcript you're about to paste somewhere it can't be un-pasted.
- When the artifact could carry a **leak** (private IPs incl. Tailscale `100.64.0.0/10` CGNAT, internal hostnames, session UUIDs, tokens/credentials, real names) or a **factual/logic error** you're too close to see in your own text.
- When the author is *you* — you cannot proofread your own blind spots, and the value here is a fresh adversary catching what the author's eye slides past.

**Do NOT use when:**

- The artifact is cheap to fix after the fact or has no real audience — a scratch file, a local note, a throwaway.
- It's still a rough draft you're actively rewriting — wait until you believe it's *done*. Reviewing a moving target wastes passes.
- You need *design* feedback on an unwritten plan — that's `architect-review`, not this.
- A single careful read plus `prepass.py leaks` is genuinely enough (a two-line snippet, a one-word typo fix).

**Cost, honestly.** A converged loop is **minutes to tens of minutes** of wall time and several agent invocations — today's production runs were 7 passes on a gist and 4 on a blog post. The machine pre-pass below is seconds and nearly free; the LLM loop is not. Gate the loop on the artifact being worth it. Trivial edits get the pre-pass and nothing else.

## The fast path

Three changes make the loop cheap enough to actually run. All three come from measured production use, not theory.

### 1. Engine: `codex exec`, not a driven pane

Codex has a non-interactive CLI (`codex exec`, v0.149+). It takes a prompt, runs to completion, and returns — **no pane, no approval prompts, no sleep-polling**. Pane-driving a live agent cost **491 seconds of pure `sleep`** plus an approval loop in a single session; `codex exec` removes that entirely.

```bash
REVIEW=~/tmp/agent/skill/adversarial-review
codex exec \
  -s read-only \                 # the reviewer must NOT edit the artifact
  --skip-git-repo-check \        # the scratch review dir is not a repo
  -C "$REVIEW" \
  -o "$REVIEW/verdict-p1.txt" \  # verdict lands in a file you can grep
  "$(cat "$REVIEW/prompt-p1.txt")"
```

- **`-s read-only` is not optional.** The reviewer's job is to find problems, not to fix them. A reviewer that can write will "helpfully" edit the artifact and then review its own edit — you lose the adversary and the audit trail in one move.
- **`-o <file>`** gives you the last message verbatim. Grep it for `^VERDICT:` instead of parsing scrollback.
- **`-m <model>`** varies the reviewer between lenses (see below) so you get genuinely different eyes.
- **`--json`** emits JSONL events if you want to script the loop.

Measured: a trivial two-blocker document reviewed in **13.5 s** wall, verdict written cleanly to the `-o` file, zero prompts.

**herdr pane-driving is now the fallback**, not the default. Use it when you want to *watch* the review live, or when `codex exec` is unavailable. The `herdr` skill owns all pane mechanics; this skill does not re-document them.

Any agent works — a background Agent-tool subagent is fine too. Use a fresh one per pass so it can't lean on its own prior context.

### 2. Step 0: the machine pre-pass

**A finding a regex can catch must never cost an LLM pass.** `tools/prepass.py` (stdlib-only, `uv run` shebang) sweeps the mechanical classes first:

```bash
skills/adversarial-review/tools/prepass.py all <target> \
  [--source <file-the-numbers-come-from>] \
  [--rubric <style-rubric.md>] \
  [--baseline <pre-rewrite-version>] \
  [--json] [--strict]
```

| Subcommand | Catches |
|---|---|
| `leaks` | CGNAT/RFC1918 IPs, `*.ts.net` hosts, GitHub/Slack/Anthropic/OpenAI/AWS keys, JWTs, private-key blocks, `secret=` assignments, session UUIDs, `/home/<user>` paths, emails |
| `numbers` | numeric literals in the target that appear **nowhere** in the stated source file |
| `links` | dead relative-path links, dead in-document anchors |
| `rubric` | banned phrases extracted from a rubric file |
| `frozen` | the facts-frozen rewrite contract (see `content-review.md`) |

Run it before pass 1 and again before the final pass. Exit 0 clean, 1 findings, 2 usage error; `--strict` promotes warnings to failures. `--json` works on either side of the subcommand.

Validation: swept **269 real blog posts in 10.2 s** with zero crashes, and surfaced a genuine `.ts.net` hostname leak in a published post. Iterating the false-positive classes against that corpus took dead-anchor reports from 93 down to 12 — do the same if you point it at a new corpus, and use `--allow <regex>` for identifiers an artifact intentionally contains.

### 3. Pass 1 is parallel; passes 2..N are serial on purpose

**Pass 1 fans out.** Run N reviewers concurrently, each with one lens and its own output file, then merge and dedupe their findings into a single list:

```bash
for lens in disclosure factual style; do
  codex exec -s read-only --skip-git-repo-check -C "$REVIEW" \
    -o "$REVIEW/p1-$lens.txt" "$(cat "$REVIEW/prompt-$lens.txt")" &
done
wait
```

Three lenses that carry their weight:

- **disclosure** — leaks, private identifiers, anything that can't be un-published.
- **factual-vs-source** — every claim checked against a named source file; recompute all arithmetic.
- **style-rubric** — the rubric file's rules (see `content-review.md`).

One narrow lens per reviewer beats one reviewer with a long checklist — attention doesn't split for free, and the lenses find genuinely disjoint problems.

**Passes 2..N stay serial, deliberately.** Each pass must see the *result* of the previous pass's fixes, because **revisions introduce new errors** — in today's runs a fix introduced a brand-new false universal claim twice. Parallelising the re-read loop would review stale text and miss exactly the class of error the loop exists to catch. Fan out to find; converge to verify.

## Process

### 1. Fix a review filename and copy the artifact into it

Every pass reads the **same** fixed path, overwritten fresh each pass:

```bash
cp /abs/path/to/real-doc.md ~/tmp/agent/skill/adversarial-review/review-target.md
```

The reviewer never sees your working file's name, path, or git state — nothing that hints "this is a draft, be lenient." Your edits land on the real doc; the copy is disposable.

### 2. Pass 1 — hostile first read with an explicit threat model

Each lens gets the **anti-agreeableness frame**, its **threat model**, and the **output contract**:

```
Adversarial review of the file at <review-target-path>.

Do NOT be agreeable. Your job is to find problems, not to bless the file.
Assume there IS something wrong and go find it. A review that says
"looks good" without having genuinely hunted is a failed review.

Threat model — this artifact is <headed public / going into a PR / …>.
Hunt specifically for:
  <this lens only:>
  - LEAKS: private IPs (incl. Tailscale 100.64.0.0/10 CGNAT), internal
    hostnames, session UUIDs, tokens/keys/credentials, real names.
  - CORRECTNESS: false factual claims, unsafe or wrong advice,
    internal contradictions, numbers that don't add up.

Output contract — follow it exactly:

BLOCKERS: numbered. Each: line number + the exact offending text +
  why it's a blocker.
NOTES: numbered, same format — real but non-blocking.
Finish with exactly one line:
  VERDICT: SAFE TO PUBLISH
or
  VERDICT: DO NOT PUBLISH
```

Tailor the threat-model bullets per lens. Keep the anti-agreeableness frame and the output contract **verbatim** — they're what make the loop work.

### 3. Between passes — scripted fixes, itemized back

Fix each blocker on the **real doc** with a self-verifying edit — the Edit tool, or a Python `replace` guarded by an assert:

```python
python3 - <<'PY'
p = "/abs/path/to/real-doc.md"
s = open(p).read()
def rep(old, new):
    global s
    assert old in s, f"NOT FOUND: {old!r}"   # fail loud if the text drifted
    s = s.replace(old, new)
s = rep("100.64.12.34", "<internal-host>")
open(p, "w").write(s)
PY
```

The `assert old in s` guard is the point: a fix that silently no-ops is worse than no fix — you'll believe a blocker is resolved when it isn't.

You may **decline** a finding — the reviewer is hostile, not infallible. Record the rationale; you hand declines back next pass so the reviewer can re-argue or drop them on the merits.

### 4. Passes 2..N — re-read from scratch, trust nothing

Re-copy the edited doc, then prompt for a cold re-read that assumes your fixes are wrong:

```
Adversarial review, pass <N>, of the file at <review-target-path>.

Re-read the file from scratch. Do NOT assume your prior findings were
fixed correctly — verify each against the CURRENT text, and stay hostile
to the new wording. Revisions introduce new errors: a fix for one
blocker routinely creates a contradiction somewhere else. Hunt for those.

Changes claimed since your last pass:
  B1: <what was changed> …
  B2: <…>
  DECLINED — B3: <finding> — rationale: <why it was left as-is>

Verify every claimed change actually landed and actually resolves the
finding. Re-argue or drop each DECLINED item on the merits.
<When numbers are involved:> VERIFY THE ARITHMETIC YOURSELF — recompute,
don't trust the doc's totals or mine.

Same output contract: BLOCKERS / NOTES / one VERDICT line.
```

The itemized change list — **including the DECLINED items** — is what keeps the reviewer honest about what moved without letting it assume the move was correct.

### 5. Converge on the gate — and distrust a too-early SAFE

Loop steps 3–4 until a pass returns `VERDICT: SAFE TO PUBLISH`. Then:

- **Distrust an early SAFE.** If the reviewer flips to SAFE right after finding substantive blockers, run **one more** pass — a clean read after a heavy-edit pass is exactly where a fresh contradiction hides.
- **Re-run the pre-pass** before believing the final verdict. It's seconds, and it catches anything a fix reintroduced.
- **Guard the last pass against invented findings.** The end-game failure mode is the opposite of agreeableness — a reviewer manufacturing nits to look thorough. Add:

  ```
  If you are out of substantive findings, say "none" plainly.
  Do NOT invent marginal issues to look thorough. A clean file with a
  real hunt behind it is the correct outcome; padding it is not.
  ```

- **Cap the loop.** If it hasn't converged in ~7 passes, stop and hand the human the open blockers with your assessment. Thrashing past that usually means a genuine disagreement about a finding — a human call, not another pass.

### 6. Report

State the outcome, the pass count, and the residual risk:

```
Adversarial review complete — 7 passes, DO NOT PUBLISH ×6 → SAFE.
Caught: 2 self-contradictions introduced during fixes, 1 false "no X" claim.
Safe to publish.
```

If you stopped at the cap without a SAFE verdict, say so explicitly and list what's open — never round a `DO NOT PUBLISH` up to "probably fine."

## Why one script now exists

Earlier this skill was pure markdown, on the repo's "pure markdown is the default" rule. `tools/prepass.py` earns its place under both halves of the exception: it needs **unit-tested classification logic** (is this string a leak, is this anchor dead, is this quoted phrase a banned one — 41 tests, several of which encode bugs the corpus sweep found: GitHub does not collapse space runs in slugs, duplicate headings get `-1`/`-2` suffixes, headings slug their *rendered* text) and it **replaces LLM passes with a subprocess sweep**, which is the whole speed argument.

Everything else stays prose, because the remaining value is the **prompt protocol** — the anti-agreeableness frame, the threat model, the verbatim output contract, the "revisions introduce new errors" re-read, the SAFE gate. That's instructions to an agent, which is what a SKILL.md is for. Loop-driving logic, if it's ever scripted, belongs in the `herdr` skill's tooling, not here.

## Key rules

- **The reviewer's job is to find problems, not to bless the file** — say it in every pass's prompt.
- **`codex exec` is the default engine; `-s read-only` is mandatory** — a reviewer that can edit stops being an adversary.
- **Machine pre-pass before any LLM pass** — never spend a model call on what a regex catches.
- **Fan out to find, converge to verify** — parallel lenses on pass 1, strictly serial re-reads after.
- **Fresh fixed-name copy every pass** — the reviewer sees the bytes, never your file's name, path, or git state.
- **Verbatim output contract** — BLOCKERS / NOTES / one `VERDICT:` line. The hard binary verdict is what makes convergence unambiguous.
- **Cold re-read every pass** — "do NOT assume prior findings were fixed correctly." Revisions introduce new errors.
- **Surgical fixes with `assert old in s`** — a silently no-op'd fix is worse than no fix.
- **Hand back declined findings with rationale** — the reviewer is hostile, not infallible.
- **VERIFY THE ARITHMETIC** whenever numbers are in play — recompute independently.
- **Distrust a too-early SAFE** — one more pass after any heavy-edit pass flips clean.
- **Last pass: "say 'none' plainly, don't invent marginal findings."**
- **Gate on worth-it** — minutes to tens of minutes per loop. Trivial edits get the pre-pass only.

## Anti-patterns

- Letting the reviewer be agreeable — a review that doesn't genuinely hunt is theater.
- Giving the reviewer write access, then reviewing its own edits.
- Burning an LLM pass on a leak or dead link `prepass.py` would have found in milliseconds.
- Parallelising the re-read loop — later passes exist to catch errors the *previous fix* introduced; run them on stale text and they catch nothing.
- Handing the reviewer your real file / path / git state — biases it toward leniency.
- Trusting your own fixes across passes.
- Believing the first SAFE after a heavy-edit pass without one more read.
- Padding the final pass with marginal findings to look thorough.
- Running the loop on a throwaway artifact with no audience — cost without payoff.
- Reaching for this when you want design feedback on a plan — that's `architect-review`.
