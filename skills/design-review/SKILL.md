---
name: design-review
description: Audit a real UI against its own design principles, with measurements instead of opinions. Mines the product's testable principles into a PRINCIPLES.md, then drives Playwright over every surface at the real viewports (phone first) measuring px heights, tap targets, truncation and vocabulary against each principle — breaking things on purpose to test the failure-state rules. Findings are ranked by user pain and shipped as a separate gated fix batch. Use before or after a UI push when "does this still hold up?" needs an answer with numbers in it.
allowed-tools: Agent, Bash, Read, Edit, Write, Grep, Glob
---

# Design Review

Audit a working UI **against its own principles**, and report **measurements, not opinions**. Every finding is a number a script produced — a px height, a tap-target box, a count of clipped titles — tied to a numbered principle the product already claims to follow. No "feels cramped." No "maybe consider."

This is the **DESIGN sibling of [`adversarial-review`](../adversarial-review/SKILL.md)**: same shape (a stated protocol plus machine-checkable gates, a hostile reader who is not allowed to be agreeable), different domain. Where adversarial-review hunts leaks and false claims in a *document* and converges on `VERDICT: SAFE TO PUBLISH`, design-review hunts principle violations in a *running surface* and converges on a ranked, measured findings list the human triages. The third sibling is `architect-review`, which hardens a spec before anything is built.

## When to use

- After a UI has accreted a few features and nobody has stepped back — the classic "each change was fine, the sum is a mess."
- Before a UI push, to hold the new work against the rules the old work was built to.
- When the user says some version of *"scan this on my phone and tell me what's wrong"* — that request is asking for measurements whether or not it says so.
- When the product has real design rules living somewhere unstructured (commit messages, code comments, a founding issue) and they need to become an auditable set.

**Do NOT use when:**

- **The change is a single widget or one-line tweak.** Moving a button, renaming a label, changing one color. The setup cost — mining principles, standing up a Playwright walk, screenshots — exceeds the whole change. Look at it, fix it, ship it.
- The UI does not exist yet. Design principles for an unbuilt thing are `architect-review` territory; you cannot measure a mockup's tap targets.
- You want *aesthetic direction* — a look, a typographic voice, a palette. That is the `frontend-design` skill. This skill is a compliance audit, and it will happily certify an ugly page that obeys every rule.
- You cannot run the app. A design review without a browser is an opinion with extra steps.

## The shape, in one line each

1. **Extract** the product's principles from the product itself, into a numbered `PRINCIPLES.md` where every entry is *testable*.
2. **Audit** by measuring every surface against every principle, at the real viewports, including the failure states — which you cause on purpose.
3. **Escalate** the judgment calls; do not resolve them yourself.
4. **Fix** as a separate, gated batch after the review is read.

---

## Phase 1 — Extract the principles from the artifact itself

**Do not import a generic design checklist.** A real product's rules are specific, load-bearing, and already written down — just scattered. Mine them.

Where they hide, in rough order of value:

- **The founding issue / bead / ticket** that says why the thing exists. This usually contains the one principle everything else derives from.
- **Commit messages.** A good commit body argues *why* a design choice was made — that argument is a principle with the serial numbers filed off. (The Cockpit's density rule was recoverable verbatim from three separate commit bodies.)
- **Code comments at the point of a deliberate weirdness.** `/* 34px disc, padded to 44 hit area */` is a principle. So is every comment starting "we tried X and it..."
- **The README**, especially any "why it works this way" section.
- **Past design decisions the human made in chat** — if they were durable enough to be implemented, they were principles.

Write them to `PRINCIPLES.md` **next to the code**, not in a scratch dir. It is a product artifact; it gets reviewed, committed, and cited by ID in later sessions.

### Every principle is a triple: rule / why / check

```markdown
**P3 — Every interactive element has a ≥44px hit area.**
The visible disc/pill may be smaller (34px, 16px); the hit area is
padded or negative-margined out to 44.
_Why:_ Igor operates this one-handed on a phone, in a webview.
_Check:_ Playwright `boundingBox()` over every `a`, `button`,
`summary`, `input`, `textarea` — height and width both ≥44 (or ≥44
after accounting for the deliberate negative-margin variants).
Read-only elements (status lines, chips that aren't links) owe nothing.
```

**The `_Check:_` line is the whole point.** If you cannot write a check a person or a Playwright script could actually run, you have written a slogan, not a principle — rewrite it or drop it. "Keep it clean" is a slogan. "No primary title anywhere ends in an ellipsis at 390px" is a principle.

Three more from the reference instance ([`igor2/decision_queue/DESIGN.md`](https://bitbucket.org/idvorkin/igor2/src/master/decision_queue/DESIGN.md), 16 principles), abbreviated to show the range:

- **Density comes from deleting non-content, never from shrinking a tap target.** *Why:* dense on mobile means more visible per screen, which *is* glanceability; the two goals only conflict if you buy density from the target. *Check:* any density change must leave every hit area ≥44px. Chrome that repeats its own section heading is a bug.
- **Collapsed is not hidden: every fold states its own answer.** *Why:* a collapsed section with no count recreates the exact invisibility bug this dashboard exists to fix. *Check:* collapse everything; every visible `<summary>` still shows a number, and the window that number covers is stated ("in 48h"), never left to guess.
- **Failure is loud; an empty list is never how a failure looks.** *Why:* an empty list reads as "nothing awaiting you," which is the precise failure mode this page exists to prevent. *Check:* kill the upstream CLI and reload — every load-bearing surface shows a red error in place, and the folded summary goes red too.

### Calibration on count and scope

- **10–20 principles.** Under ~8 and you have not actually mined the product; over ~25 and you are writing a style guide nobody will audit against. The Cockpit landed on 16.
- **Number them P1..PN and never renumber.** Findings, commits and future sessions cite them by ID.
- **Include a scope note** — where this thing runs, who uses it, what it must never do. It resolves half the judgment calls before they arise ("local/Tailscale-only, carries private life data, never deploy public").
- **Get the principles blessed before auditing.** Five minutes of human review here is worth more than an entire audit run against the wrong rules.

---

## Phase 2 — Audit: measure, don't opine

A design review that returns adjectives has failed. **Every finding carries a number**, and the number came from a script, not from looking at a screenshot.

### Walk every surface at the real viewports, phone first

Phone first is not politeness to mobile — it is where every density and truncation bug is *visible*. A desktop pass first will certify a layout that is broken on the device the human actually holds.

```javascript
// review-walk.cjs — run from a dir with node_modules/playwright
const { chromium } = require("playwright");
const VIEWPORTS = [
  { name: "phone", width: 390, height: 844 }, // measure here first
  { name: "phone-sm", width: 360, height: 780 },
  { name: "desktop", width: 1440, height: 900 },
];
```

For each surface × viewport: screenshot, then run the per-principle measurements below. Screenshots are evidence attached to findings, **never** the measurement itself.

### The measurements that carry their weight

**Tap targets** — the check that finds the most real bugs, because a control's own comment often demands 44 while its computed box is 26:

```javascript
const small = await page.$$eval(
  "a, button, summary, input, textarea, [role=button]",
  (els) =>
    els
      .map((e) => {
        const r = e.getBoundingClientRect();
        return {
          tag: e.tagName,
          cls: e.className,
          text: (e.innerText || "").slice(0, 40),
          w: Math.round(r.width),
          h: Math.round(r.height),
        };
      })
      .filter((m) => m.h > 0 && (m.h < 44 || m.w < 44)),
);
```

**Vertical budget** — what each block of chrome *costs* in px before the user reaches content. This is how "the header feels big" becomes "the header is 178px, which is 21% of the viewport, for two lines of text and one link nobody clicks":

```javascript
const cost = await page.$eval("header", (e) => e.getBoundingClientRect().height);
const tabsY = await page.$eval("#tabs", (e) => e.getBoundingClientRect().top);
// tabsY is the real number: how far down the page the user's first choice is.
```

Measure `boundingClientRect().top` of the **first thing the user came for**. A header height is a fact; the y-coordinate of the tab bar is an argument.

**Truncation** — count it, as a percentage, not "some titles are cut off":

```javascript
const clipped = await page.$$eval(".row-title", (els) =>
  els
    .map((e) => ({
      text: e.innerText,
      clipped: e.scrollWidth > e.clientWidth || e.scrollHeight > e.clientHeight,
    }))
    .filter((m) => m.clipped),
);
// report: `${clipped.length}/${total} (${pct}%) titles truncate at 390px`
```

**Horizontal overflow** — `document.documentElement.scrollWidth > window.innerWidth` is a one-line pass/fail nobody should ever ship without.

**Vocabulary consistency** — a grep, not a browser check, and it finds real drift: duplicate duration formats (`3d` vs `3 days ago`), hard-coded colors outside `:root`, animations with no `prefers-reduced-motion` guard, two chip classes that render the same shape.

### Break things on purpose

**Failure-state principles cannot be audited on a healthy system.** If a principle says "failure is loud," the only honest check is to cause the failure:

- Kill the upstream CLI / rename the binary on `PATH` → reload → confirm every load-bearing surface renders a red error *in place*, and no surface renders an empty list.
- `page.route("**/api/**", r => r.abort())` → confirm the stale-data stamp flips and the toast says so.
- Emulate the constrained environment the principle names — `prefers-reduced-motion`, plain-http origin (no `navigator.mediaDevices`), dark mode, an offline reload.
- Feed a row that is missing every optional field → confirm absence renders as absence, not as a placeholder gap.

A review that never broke anything has not tested the principles that matter most, because the failure paths are exactly the ones nobody exercises by hand.

### The finding format

Rank by **user pain**, not by principle number and not by how easy it is to fix. Tag effort separately so the human can pick off cheap wins without the ranking lying to them.

```markdown
### F1 — Header eats 178px (21% of the viewport) before any content — P2, P1

**Measured:** header height 178px at 390×844; tab bar top at y=241.
Line 3 ("asks via bd human · PRs live via gh") explains the plumbing to
the one person who built the plumbing.
**Pain:** every glance pays it. This is the first thing on every load.
**Effort:** S — delete line 3, put Refresh/Note on line 1 as icons.
**Screenshot:** `review/phone-header-before.png`
```

- **S / M / L** effort, estimated honestly. An `S` that turns out to be an `L` poisons the whole list's credibility.
- **Cite the principle IDs** — a finding that cannot name a principle is either a missing principle (add it, and say so) or your personal taste (drop it).
- **Lead with the measurement.** "178px, 21% of the viewport" is a finding; "the header feels heavy" is a vibe with a number bolted on afterward.

---

## Phase 3 — Judgment calls go to the human

Some violations are not bugs. Flag these; **do not fix them**:

- **Two principles conflict.** Density (P2) says cut chrome; glanceability (P1) says show the counts. When the fix trades one principle against another, the trade is the human's to make — present both readings and the measurements for each, and recommend, but do not act.
- **The fix is a product choice.** "The PR strip pushes the tab bar to y≈3100" has at least three fixes — collapse by default, cap the rows, move the strip below the tabs — and they are different products, not different implementations.
- **The principle itself may be wrong.** If a rule is violated everywhere by deliberate, working code, the rule probably lost an argument it was never told about. Say so and propose the amended principle; do not quietly re-implement the page to match a stale rule.
- **Anything touching what the product promises not to do.** Scope-note violations are escalations, always.

State each as: *the two readings, the measurement supporting each, your recommendation, and the fact that you are not acting on it.* This mirrors adversarial-review's **declined findings** — the reviewer is hostile, not infallible, and the record of what was consciously left alone is as valuable as the fix list.

## Phase 4 — Fixes ship as a separate gated batch

**Never fix during the audit.** Three reasons, all learned the hard way:

1. A reviewer who edits is reviewing its own edits — the same trap that makes `-s read-only` mandatory in adversarial-review.
2. The findings list is the artifact the human reads. A list that is already half-implemented cannot be triaged, only ratified.
3. Fixes interact. Cutting the header changes what "above the fold" means for every other finding; batching lets you re-measure once instead of N times.

The batch:

- Human reads the ranked findings and picks. Not all of them — picking is the point of ranking.
- Implement the picked set as **one commit per coherent change**, with the commit body carrying the measurement and the principle ID. `HEADER 178px -> 86px at 390 wide` in a commit body is documentation that cannot rot, because the next audit re-measures it.
- **Re-measure after.** The audit's own numbers are the acceptance test. A fix that claims 86px and measures 94px is not done.
- Findings the human did not pick become issues/beads, not silent drops.

---

## Calibration: what a real run produced

The reference instance is the Cockpit audit (`igor2/decision_queue`, Aug 2026) — 16 principles, one phone-first Playwright walk, findings ranked and shipped as two gated commits. Real numbers, so you know what "measured" looks like:

| Finding | Measurement | Fix |
|---|---|---|
| Header too heavy | **178px → 86px** at 390 wide; one of three lines explained the plumbing to the person who built the plumbing | Two-line header, icon-only controls, delete line 3 |
| PR strip buried the tabs | Tab bar at **y≈3100** — nearly four screens before the primary surface; **y≈530** after | Collapse by default; the folded summary already stated the whole answer |
| Agents strip cost | **432px** of vertical budget above the content it introduced | Cut |
| Titles unreadable | **48% of row titles truncated mid-word** at 390px | Two-line clamp, not `nowrap` |
| Tap target lied | A control measuring **26px** whose own source comment demanded 44px | Padded to a 44px hit area, 34px visible disc |
| Row height | Open rows wrapped to a **third line: 112px vs 84px**, because a 310px meta line needed 323px. 25 rows × 28px = **700px of scroll for one repeated word** | Drop `"ago"` (41px → 20px), flex gap 10 → 8 |

Note what every row has in common: a number that a script produced, and a fix small enough to state in one clause. That last one — 700px of scroll bought by the word "ago" — is the shape of finding that only exists because something got measured. Nobody eyeballs their way to it.

## Why this skill stays prose

`adversarial-review` earned `prepass.py` because it needed **unit-tested classification logic** (is this string a leak, is this anchor dead) and it **replaced LLM passes with a subprocess sweep**. Neither holds here:

- The classification is `h < 44`. There is no logic to unit-test, and a script that wraps one comparison is ceremony.
- The measurement code is Playwright `evaluate()` against a specific app's DOM — selectors, surfaces and viewports differ per product. Vendoring that as a generic tool at **N=1** would ship a template that forks on day one (see the repo's *Abstractions: Wait for N=2* rule). The snippets above are reference implementations to adapt, deliberately not a framework to call.
- There is no subprocess fan-out to parallelize; the browser walk is inherently serial per surface.

What generalizes is the **protocol** — mine testable principles from the product, measure instead of opining, break the failure paths on purpose, escalate the trades, gate the fixes. That is instructions to an agent, which is what a `SKILL.md` is for. If a second product's audit reuses the same selectors and viewports end-to-end, extract the walker then, and not before.

## Key rules

- **Principles come from the product, not from a checklist.** Mine the founding issue, the commit bodies, and the comments at deliberate weirdnesses.
- **Every principle is rule / why / check.** No runnable check means it is a slogan — rewrite or drop it.
- **Get the principles blessed before you audit.** Auditing against the wrong rules wastes the whole run.
- **Phone viewport first.** Desktop-first certifies layouts that are broken on the device in the hand.
- **Every finding carries a number a script produced.** Screenshots are evidence, never measurement.
- **Break the failure states on purpose** — kill the backend, abort the routes, emulate the constrained environment. Healthy-system audits skip the principles that matter most.
- **Rank by user pain; tag effort separately.** Ranking by ease of fix is a lie about priority.
- **Cite principle IDs on every finding.** No ID means it is a missing principle or your taste.
- **Judgment calls escalate, they do not get fixed** — conflicting principles, product choices, and rules that may themselves be wrong.
- **Never fix during the audit.** One batch, gated on the human's picks, re-measured after.
- **Put the measurement in the commit body.** `178px -> 86px` cannot rot; "improved the header" already has.

## Anti-patterns

- Returning adjectives. "Cramped", "cluttered", "feels off" — all unactionable, all unfalsifiable.
- Importing a generic mobile-UX checklist instead of mining the product's real rules. It will flag things the product decided on purpose and miss everything specific.
- Writing principles with no check line, then "auditing" by re-reading them.
- Auditing only the happy path — the loud-failure and stale-data principles are the ones a healthy system silently exempts.
- Measuring on desktop and spot-checking mobile.
- Fixing as you go, then presenting a findings list that is already implemented.
- Resolving a two-principle conflict yourself because the fix was obvious to you.
- Using screenshots as the measurement — a screenshot shows *that* something is clipped, never that 48% of titles are.
- Running this for a one-widget change. The setup costs more than the change.
- Reaching for this when the ask was aesthetic direction (`frontend-design`) or design feedback on an unbuilt plan (`architect-review`).
