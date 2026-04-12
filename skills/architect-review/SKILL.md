---
name: architect-review
description: Run iterative architect review passes on a design spec, tracking convergence. Use when a spec is written and needs hardening before implementation — each pass finds fewer issues until the design stabilizes.
allowed-tools: Agent, Read, Edit, Bash, Glob, Grep
---

# Architect Review

Run multiple architect review passes on a design spec, with each pass reading the spec as modified by the previous. Track changes in a changelog file to measure convergence.

## When to use

- After writing or updating a design spec, before moving to implementation planning
- When you want independent review of architectural decisions
- When the user says "review the plan", "harden the spec", "architect review"

**Do NOT use when:**
- The spec is still a rough draft the user is actively editing -- wait until they say it's ready for review
- The document is implementation code, not a design spec -- use code review instead
- The spec is under 1 page -- a single careful read is enough, no iterative passes needed

## How it works

Each pass is a background Opus agent that reads the current spec, makes concrete edits, and reports what it changed. Passes run sequentially — each reads the output of the previous. A changelog file beside the spec tracks every change across passes.

The pattern converges: pass 1 finds many issues, pass 2 finds fewer, pass 3 finds edge cases, pass 4 typically finds nothing. Stop when a pass makes 0-2 changes.

## Process

### 1. Set up the changelog

Create `<spec-name>-changelog.md` beside the spec file:

```markdown
# <Spec Title> — Review Changelog

Tracks changes across iterative architect review passes.

## Convergence Tracking

| Pass | Changes |
|------|---------|
```

### 2. Run passes as background agents

Launch each pass using the **Agent tool** with `model: "opus"` and `run_in_background: true`. Wait for each pass to complete before launching the next. Each agent gets this prompt structure:

```
You are a senior software architect doing pass <N> on a design spec.
Previous passes made [X, Y, Z] changes respectively (see the changelog).

Read the changelog at <changelog-path> first to understand what previous passes changed.
Then read the spec at <spec-path> carefully.

Context: <brief description of the system>

Review for:
- [pass 1] Architectural soundness, interface design, missing concerns, unnecessary complexity, consistency
- [pass 2] Anything pass 1 missed or got wrong, contradictions from pass 1 edits, over-engineering
- [pass 3+] Subtle implementation bugs, remaining ambiguity, over-engineering from previous reviewers

Make specific, concrete edits using the Edit tool. Each edit is a "change" — cosmetic rewording does NOT count. If the spec is solid, make zero changes — don't change for the sake of changing.

Report:
1. Numbered list of changes made and why
2. Things reviewed but left unchanged
3. Assessment: is this ready for implementation?
```

### 3. After each pass completes

- Update the changelog with the numbered changes
- Update the convergence table
- If changes > 2: launch next pass (background)
- If changes <= 2: stop — spec has converged

### 4. Report convergence

Tell the user the final state:

```
Architect review complete — 4 passes, converged.
Pass 1: 21 changes | Pass 2: 13 | Pass 3: 5 | Pass 4: 1
Changelog: <path>
```

## Key rules

- **Always run agents in background** — don't block the conversation
- **Sequential, not parallel** — each pass must read the previous pass's edits
- **Opus model** for all passes — architecture review needs the strongest reasoning
- **Changelog is mandatory** — without it, you can't measure convergence
- **Don't over-run** — 4 passes is typical. Stop at 5 max even if not fully converged. Report what's still open.
- **Read the spec's repo CLAUDE.md first** — understand project conventions before reviewing
- **A "change" is a substantive architectural edit** — fixing a typo or rewording a sentence for style does not count toward the convergence threshold

## Anti-patterns

- Running passes in parallel (each would review stale state)
- Making cosmetic/wordsmith changes (wastes a pass)
- Continuing past convergence (diminishing returns)
- Not creating the changelog (can't track progress)
