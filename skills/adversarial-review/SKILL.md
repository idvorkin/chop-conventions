---
name: adversarial-review
description: Drive an external agent through hostile review-until-clean of a document or artifact before it ships. Use before publishing a doc/gist/PR/post/report that could carry a leak (private IPs, hostnames, tokens, real names) or a factual/logic error — when the artifact is worth the round trips. The reviewer's job is to find problems, not to bless the file; loop until its verdict flips to SAFE.
allowed-tools: Agent, Bash, Read, Edit, Grep, Glob
---

# Adversarial Review

Run an **external, hostile** agent over a document or artifact and iterate until it can find nothing wrong. Each pass the reviewer re-reads from scratch, stays adversarial to the *current* wording, and emits a hard verdict. You apply surgical fixes between passes and re-scan. Stop when the verdict flips to SAFE — not before.

This is the external-adversary sibling of `architect-review`. Where architect-review drives a spec toward *internal design convergence* (a friendly reviewer hardening decisions), adversarial-review is a *stranger trying to break your artifact before the world does* — leaks, contradictions, false claims, unsafe advice. The reviewer is told, explicitly, that its job is to find problems, not to agree.

## When to use

- Before shipping something with an **audience and a blast radius**: a gist or blog post headed public, a PR description, a report, a README, a config or transcript you're about to paste somewhere it can't be un-pasted.
- When the artifact could carry a **leak** (private IPs incl. Tailscale `100.64.0.0/10` CGNAT, internal hostnames, session UUIDs, tokens/credentials, real names) or a **factual/logic error** you're too close to see in your own text.
- When the author is *you* — you cannot proofread your own blind spots, and the value here is a fresh adversary catching what the author's eye slides past.

**Do NOT use when:**

- The artifact is cheap to fix after the fact or has no real audience — a scratch file, a local note, a throwaway. The loop costs several agent round trips; gate on the artifact being worth them.
- It's still a rough draft you're actively rewriting — wait until you believe it's *done*. Reviewing a moving target wastes passes.
- You need *design* feedback on an unwritten plan — that's `architect-review`, not this.
- A single careful read plus a `grep` for the obvious secrets is genuinely enough (a two-line snippet). Reserve the loop for artifacts where a missed problem actually hurts.

## How it works

You orchestrate; a separate agent reviews. Each pass:

1. You copy the current artifact to a **fixed review filename** the reviewer reads.
2. The reviewer reads it cold, hunts for problems against an explicit threat model, and returns a structured verdict.
3. You apply fixes surgically, then start the next pass.

The pattern converges like architect-review's, but on a **binary gate** rather than a change count: pass 1 finds real blockers, later passes find fewer, and the reviewer's own re-reads catch errors *you introduced while fixing the last batch*. Stop when a pass returns `VERDICT: SAFE TO PUBLISH` — and only then.

Real data point (the loop that earned this skill): **7 passes on one document, `DO NOT PUBLISH` ×6 → `SAFE`**. It caught two self-contradictions the author introduced *while fixing other findings*, plus a confidently-stated "this tool has no X" claim that was simply false. None of those were visible to the author.

### Driving the reviewer

The reviewer can be **any** agent. Two common rigs:

- **Codex (or another CLI agent) in a herdr pane** — see the `herdr` skill for all pane mechanics (create workspace, `agent start`, `agent prompt --wait`, `agent read`). This skill does **not** re-document the multiplexer; it owns the *review protocol* (the prompts, the gate, the anti-agreeableness). A different agent than the one authoring the doc is ideal — genuinely fresh eyes.
- **A background Agent-tool subagent** — cheaper to wire up, no external CLI. Use a fresh subagent per pass so it can't lean on its own prior context; the point is a cold re-read every time.

Either way the protocol below is identical. Keep the driving thin; the value is in *what you ask*, not the transport.

## Process

### 1. Fix a review filename and copy the artifact into it

Every pass reads the **same** fixed path — e.g. `review-target.md` in a scratch dir — and you overwrite it fresh each pass:

```bash
cp /abs/path/to/real-doc.md /abs/path/to/review/review-target.md
```

Why a fixed copy and not the real file:

- The reviewer never sees your working file's **name, path, or git state** — nothing that hints "this is a draft, be lenient" or reveals where it lives.
- Each pass reviews exactly the bytes you copied, with no ambiguity about which version is under review.
- Your edits land on the real doc; the copy is disposable and regenerated every pass.

Put the copy under `~/tmp/agent/skill/adversarial-review/` (per the agent-temp-files convention), not beside the real artifact.

### 2. Pass 1 — hostile first read with an explicit threat model

Prompt the reviewer with three parts: the **anti-agreeableness frame**, the **threat model for this artifact**, and the **output contract**.

```
Adversarial review of the file at <review-target-path>.

Do NOT be agreeable. Your job is to find problems, not to bless the file.
Assume there IS something wrong and go find it. A review that says
"looks good" without having genuinely hunted is a failed review.

Threat model — this artifact is <headed public / going into a PR / being
pasted into Slack / …>. Hunt specifically for:
  <pick what fits the artifact:>
  - LEAKS: private IPs (incl. Tailscale 100.64.0.0/10 CGNAT), internal
    hostnames, session UUIDs, tokens/keys/credentials, real names,
    anything that identifies private infrastructure or people.
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

Tailor the threat-model bullets to the artifact — a public doc weights leaks; a how-to weights unsafe/wrong advice; a report weights arithmetic and contradictions. Keep the anti-agreeableness frame and the output contract **verbatim** across artifacts; they're what make the loop work.

### 3. Between passes — apply fixes surgically, then re-scan

Read the reviewer's BLOCKERS (and the NOTES you agree with). Fix each on the **real doc** with a surgical, self-verifying edit — the Edit tool, or a Python one-liner that asserts the old text was present before replacing it:

```python
python3 - <<'PY'
p = "/abs/path/to/real-doc.md"
s = open(p).read()
def rep(old, new):
    assert old in s, f"NOT FOUND: {old!r}"   # fail loud if the text drifted
    return s.replace(old, new)
s = rep("100.64.12.34", "<internal-host>")
s = rep("session 3f2a…", "<session-id>")
open(p, "w").write(s)
PY
```

The `assert old in s` guard is the point: a fix that silently no-ops (because the text moved or you mis-quoted it) is worse than no fix — you'll believe a blocker is resolved when it isn't. Fail loud.

You may **decline** a finding — the reviewer is hostile, not infallible, and will sometimes flag a non-issue. Record declines with a reason; you'll hand them back next pass so the reviewer can re-argue or drop them.

### 4. Passes 2..N — re-read from scratch, trust nothing

Re-copy the (now edited) doc into the fixed review filename, then prompt for a **cold re-read that assumes your fixes are wrong**:

```
Adversarial review, pass <N>, of the file at <review-target-path>.

Re-read the file from scratch. Do NOT assume your prior findings were
fixed correctly — verify each against the CURRENT text, and stay hostile
to the new wording. Revisions introduce new errors: a fix for one
blocker routinely creates a contradiction somewhere else. Hunt for those.

Changes claimed since your last pass:
  B1: <what was changed> …
  B2: <…>
  N1: <…>
  DECLINED — B3: <finding> — rationale: <why it was left as-is>

Verify every claimed change actually landed and actually resolves the
finding. Re-argue or drop each DECLINED item on the merits.
<When numbers are involved:> VERIFY THE ARITHMETIC YOURSELF — recompute,
don't trust the doc's totals or mine.

Same output contract: BLOCKERS / NOTES / one VERDICT line.
```

Re-stating claimed changes (including declines) keeps the reviewer honest about *what moved* without letting it assume the move was correct. "Revisions introduce new errors" is load-bearing — it's what caught the author-introduced contradictions in the 7-pass run.

### 5. Converge on the gate — and distrust a too-early SAFE

Loop steps 3–4 until a pass returns `VERDICT: SAFE TO PUBLISH`. Then:

- **Distrust an early SAFE.** If the reviewer flips to SAFE after finding substantive blockers the pass before, run **one more** pass before believing it — a clean read after a heavy-edit pass is exactly where a fresh contradiction hides.
- **Guard the last pass against invented findings.** The failure mode at the end is the opposite of agreeableness — a reviewer manufacturing marginal nits to look thorough. Add to the final prompt:

  ```
  If you are out of substantive findings, say "none" plainly.
  Do NOT invent marginal issues to look thorough. A clean file with a
  real hunt behind it is the correct outcome; padding it is not.
  ```

- **Cap the loop.** If it hasn't converged in ~7 passes, stop and hand the human the remaining open blockers with your assessment — thrashing past that usually means a genuine disagreement about a finding, which is a human call, not another pass.

### 6. Report

Tell the user the outcome and the pass count, and name the residual risk if any:

```
Adversarial review complete — 7 passes, DO NOT PUBLISH ×6 → SAFE.
Caught: 2 self-contradictions introduced during fixes, 1 false "no X" claim.
Safe to publish.
```

If you stopped at the cap without a SAFE verdict, say so explicitly and list what's still open — never round a `DO NOT PUBLISH` up to "probably fine."

## Why a proven pattern, not a one-off (N=2)

This abstraction is **earned**, not speculative (per the repo's "Abstractions: Wait for N=2" rule):

1. The loop ran **7 times in a single session** to clean one document — the same prompt shape, gate, and anti-agreeableness frame each pass, demonstrably catching errors the author could not.
2. It's the direct **sibling of `architect-review`'s** convergence loop — same orchestrate-a-reviewer-until-stable shape, specialized from friendly internal design-hardening to hostile external ship-gating.

Two concrete instances of the pattern, so it's extracted as a skill rather than left as copy-paste.

## Why prose, not code

No helper script. The value here is entirely the **prompt protocol** — the anti-agreeableness frame, the threat model, the verbatim output contract, the "revisions introduce new errors" re-read, the SAFE gate. That's instructions to an agent, which is exactly what a SKILL.md is for. The only mechanical bits (copying a file, a `rep()`-with-`assert` edit) are two shell/Python snippets inline above; wrapping them in a vendored CLI would add a tool to maintain without making the review any better. Per the repo's skill conventions, pure markdown is the default and helpers only earn their place when they parallelize subprocess calls *and* need unit-tested classification logic — neither applies. If a future rig needs to script the herdr drive loop, that logic belongs in the `herdr` skill's tooling, not here.

## Key rules

- **The reviewer's job is to find problems, not to bless the file** — say this in every pass's prompt. A frictionless "looks good" is a failed review.
- **Fresh fixed-name copy every pass** — the reviewer sees the bytes, never your working file's name, path, or git state.
- **Verbatim output contract** — BLOCKERS / NOTES / one `VERDICT:` line. The hard binary verdict is what makes convergence unambiguous.
- **Cold re-read every pass** — "do NOT assume prior findings were fixed correctly; verify against the current text." Revisions introduce new errors.
- **Surgical fixes with `assert old in s`** — a silently no-op'd fix is worse than no fix. Fail loud when the text drifted.
- **Hand back declined findings with rationale** — the reviewer is hostile, not infallible; let it re-argue or drop them.
- **VERIFY THE ARITHMETIC** whenever numbers are in play — recompute independently, trust neither the doc nor a prior pass.
- **Distrust a too-early SAFE** — run one more pass after any heavy-edit pass flips clean.
- **Last pass: "say 'none' plainly, don't invent marginal findings"** — guard the tail against manufactured nits.
- **Gate on worth-it** — the loop is several round trips; run it on artifacts where a missed leak or error actually costs something.
- **Keep the driving thin** — the `herdr` skill owns pane control; this skill owns the review protocol.

## Anti-patterns

- Letting the reviewer be agreeable — a review that doesn't genuinely hunt is theater.
- Handing the reviewer your real file / path / git state — leaks context that biases it toward leniency.
- Trusting your own fixes across passes — the whole value is the adversary re-checking them cold.
- Believing the first SAFE after a heavy-edit pass without one more read.
- Padding the final pass with marginal findings to look thorough.
- Running the loop on a throwaway artifact with no audience — cost without payoff.
- Reaching for this when you actually want design feedback on a plan — that's `architect-review`.
