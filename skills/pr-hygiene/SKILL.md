---
name: pr-hygiene
description: Surface open PRs that have genuinely unaddressed review feedback, filtering out CodeRabbit auto-summaries and CI-bot noise. Use before ending a session, when asked "which of my PRs need attention", or to gate a session close on unresolved review threads.
allowed-tools: Bash, Read
---

# PR Hygiene

`git-safe-push` guards one PR footgun (pushing to a merged branch). This guards
its sibling: **open PRs quietly piling up with review comments you never
resolved or replied to.** GitHub gives no nudge, and a naive "the last comment
isn't mine" filter over-flags ~3× — it can't tell a CodeRabbit auto-summary or a
CI-bot ping from a real ask.

`pr-hygiene` enumerates your open PRs across GitHub identities and sorts them
into three tiers so you act only on the ones that need it.

## When to use

- **Before ending a work session** — run it, resolve/reply to anything 🔴.
- "Which of my PRs need attention?" / "Do I have stale review comments?"
- As a close-out gate: it exits nonzero when any 🔴 remains.

## Install

It is a single self-bootstrapping `uv run --script` file — no packaging needed.
Symlink it onto `$PATH` the same way as `git-safe-push`:

```bash
ln -sf ~/gits/chop-conventions/skills/pr-hygiene/pr_hygiene.py ~/.local/bin/pr-hygiene
```

The PEP 723 shebang resolves `typer` on first run. Requires `gh` (authenticated).

## Usage

```bash
pr-hygiene                        # ranked markdown table (🔴 first)
pr-hygiene --json                 # machine-readable JSON (+ counts, errors)
pr-hygiene --repo idvorkin/chop-conventions   # filter to one repo
pr-hygiene --author octocat       # override author (repeatable)
pr-hygiene --no-fail              # always exit 0 (don't gate on 🔴)
```

Default authors are `idvorkin` and `idvorkin-ai-tools`. Bitbucket repos (e.g.
`igor2`) are invisible to `gh` and simply aren't listed. Per-PR GraphQL failures
are reported inline (tier `error`), never crash the run.

## How it classifies — the whole point

For each PR one GraphQL round-trip pulls review threads (`isResolved`),
`reviewDecision`, reviews, and issue comments. Tiers:

- **🔴 needs action** — ≥1 **unresolved** review thread that is a real ask
  (a human reviewer, **or** a bot _finding_ that isn't an auto-summary), OR
  `reviewDecision == CHANGES_REQUESTED`, OR a human reviewer has the last word
  and the author hasn't responded since (push, comment, or review).
- **🟡 awaiting merge** — flagged only because a CodeRabbit auto-summary or a
  github-actions/CI comment is the last event, with no open threads. Merge-ready,
  not stale.
- **🟢 clean** — no review activity at all.

Two noise rules make this work:

1. **CodeRabbit walkthrough/summary comments and CI-bot (github-actions)
   comments are NOISE, never asks** — screened by bot login at the _issue_
   level.
2. **A bot finding _inside a review thread_ is a real ask** — CodeRabbit and
   Copilot post genuine inline findings under the same login, so thread comments
   are screened on the _body_ (auto-summary markers) rather than the login. Get
   this backwards and either every summary flags red, or every real finding gets
   dropped.

Cross-identity nuance: a comment counts as a _reviewer_ ask only when its author
differs from the PR author — so a self-note on your own PR is ignored, while a
thread left by your _other_ identity (e.g. `idvorkin` reviewing an
`idvorkin-ai-tools` PR) correctly counts.

The classification logic lives in pure, unit-tested functions in
`pr_hygiene.py` (see `tests/`); `gh` shell-outs are injected so tests run
offline.
