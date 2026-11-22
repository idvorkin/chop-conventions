---
name: pr-hygiene
description: Surface open PRs that have genuinely unaddressed review feedback, filtering out CodeRabbit auto-summaries and CI-bot noise. Use before ending a session, when asked "which of my PRs need attention", or to gate a session close on unresolved review threads.
allowed-tools: Bash, Read
---

# PR Hygiene

This guards a quiet PR footgun: **open PRs piling up with review comments you
never resolved or replied to.** GitHub gives no nudge, and a naive "the last comment
isn't mine" filter over-flags ~3× — it can't tell a CodeRabbit auto-summary or a
CI-bot ping from a real ask.

`pr-hygiene` enumerates your open PRs and sorts them into three tiers so you act only on the ones that need it.

## When to use

- **Before ending a work session** — run it, resolve/reply to anything 🔴.
- "Which of my PRs need attention?" / "Do I have stale review comments?"
- As a close-out gate: it exits 1 when any 🔴 remains and 2 when anything could
  not be queried (it fails closed — "couldn't check" is never "clean").

## Install

It is a single self-bootstrapping `uv run --script` file — no packaging needed.
Symlink it onto `$PATH`:

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
PR_HYGIENE_AUTHORS=me,my-bot pr-hygiene   # several GitHub identities
pr-hygiene --no-fail              # always exit 0 (advisory; errors still printed)
```

Authors, by precedence: `--author` flags, then the `PR_HYGIENE_AUTHORS` env var
(comma-separated — set it if you work under more than one GitHub identity),
then the logged-in `gh` user (`gh api user`). Non-GitHub hosts (e.g. Bitbucket)
are invisible to `gh` and simply aren't listed.

Exit codes: `0` nothing needs action, `1` at least one 🔴, `2` something could
not be queried — the author lookup, a `gh search` (e.g. not logged in), or any
per-PR fetch (reported inline as tier `error`). Errors never crash the run, but
they do fail the gate; `--no-fail` is the explicit opt-out.

## How it classifies — the whole point

For each PR one GraphQL round-trip pulls review threads (`isResolved`),
`reviewDecision`, reviews, and issue comments. Tiers:

- **🔴 needs action** — ≥1 **unresolved** review thread that is a real ask
  (a human reviewer, **or** a bot _finding_ that isn't an auto-summary), OR
  `reviewDecision == CHANGES_REQUESTED`, OR a human reviewer has the last word
  and the author hasn't responded since (push, comment, or review). A human
  **APPROVED** review as the last word is not an ask.
- **🟡 no open asks** — there was review activity (a CodeRabbit auto-summary or
  CI comment, resolved threads, an approval, or an ask you already answered) but
  nothing is waiting on you. Mergeability and CI status are **not** checked, so
  this is not a "merge-ready" claim.
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
