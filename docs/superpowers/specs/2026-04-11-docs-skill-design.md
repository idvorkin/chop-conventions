# Design: `docs` skill (Context7 library docs lookup)

**Date:** 2026-04-11
**Scope:** Add a new machine-level Claude Code skill that wraps the Context7 (`ctx7`) CLI's library/docs lookup capability, so Claude fetches fresh, authoritative third-party library documentation instead of relying on stale training data.

## Motivation

Claude's training data for third-party libraries (React, FastAPI, Pydantic, LangChain, Polars, SDKs, etc.) drifts out of date. The `ctx7` CLI from context7.com is purpose-built to return curated, query-ranked snippets from library docs. We want a skill whose `description` field triggers Claude to reach for `ctx7` in two situations:

1. **Reactive** — user asks "how do I X with library Y".
2. **Proactive** — Claude is about to write code against a named third-party library and is uncertain whether the current API matches training memory.

The user explicitly does NOT want this skill to cover `ctx7 skills install` — only the docs lookup (`ctx7 library` and `ctx7 docs`).

## Non-Goals

- Wrapping `ctx7 skills` subcommands (install, search, suggest, list, remove, info, generate).
- Wrapping `ctx7 login` / `logout` / `whoami` (mentioned only as a troubleshooting footnote).
- Replacing `WebFetch` for arbitrary URLs — ctx7 is preferred only when the target is a named library.
- Caching ctx7 output to disk. Session-scoped reuse only (don't re-query the same library+topic twice in one conversation).

## Skill contents

**Path:** `skills/docs/SKILL.md` in chop-conventions, symlinked to `~/.claude/skills/docs`.

**Frontmatter:**

```yaml
---
name: docs
description: Use when answering questions about or writing code against a third-party library/framework. Fetches fresh, authoritative documentation via Context7 (`ctx7`) instead of relying on stale training data. Fires both reactively ("how do I X with library Y") and proactively (about to write library code and unsure of current API).
---
```

**Body sections (in order):**

1. **When to use / When NOT to use**
   - USE: named third-party library (e.g., `react`, `fastapi`, `pydantic`, `polars`, `langchain`, cloud SDKs), version-specific questions, uncertainty about whether an API still exists.
   - SKIP: language stdlib, general CS/programming concepts, code in the current repo (read it directly), or one-off trivia where you're already confident.

2. **Two-step workflow**
   - Step 1 — resolve name to Context7 library ID:
     ```bash
     npx ctx7 library <name> "<query>"
     ```
     Always pass a query — per `--help` it's positional-optional, but Context7 uses it to rank candidates by relevance and results get noticeably worse without it.
   - Step 2 — fetch docs for the resolved ID:
     ```bash
     npx ctx7 docs <libraryId> "<query>"
     ```
     Both arguments are required on `ctx7 docs` (asymmetric with Step 1).
   - Shortcut: skip Step 1 *only* when you already resolved the ID earlier in the same session. **Don't guess IDs from intuition.** The highest-ranked match is often not what you'd expect — `/reactjs/react.dev` not `/facebook/react`; `/pola-rs/polars` not `/polars/polars`. (Live sanity-check during implementation showed `/facebook/react` is actually the 5th-ranked result, contradicting my initial assumption — this drove the "don't guess" rule.)

3. **Efficiency rules**
   - Do not re-query the same library+topic twice in one session — reuse the first result from earlier in the conversation.
   - Prefer specific queries over broad ones (`"useEffect cleanup"` beats `"hooks"`).
   - `--json` is supported on both commands when structured output helps; plain text is fine for in-context reading.

4. **ctx7 vs WebFetch**
   - ctx7 returns curated, query-ranked library snippets — reach for it first when the target is a named library.
   - WebFetch is the fallback for arbitrary URLs (blog posts, GitHub issues, RFCs).

5. **Auth / setup**
   - The first `npx ctx7` invocation auto-installs the package; no manual setup needed.
   - Works anonymously. If you hit rate limits or auth errors, run `npx ctx7 login` and re-try.

6. **Common mistakes**
   - Skipping the `library` step and guessing the Context7 ID.
   - Using ctx7 for stdlib or general-language questions.
   - Re-running the same query multiple times in one session instead of reusing the first output.

## Installation

1. Create `skills/docs/SKILL.md` with the contents above.
2. Symlink machine-level:
   ```bash
   ln -s /home/developer/gits/chop-conventions/skills/docs ~/.claude/skills/docs
   ```
3. Add a row for `docs` to the skills table in the chop-conventions `README.md`.
4. Commit everything in a single change.

## Risks / Open questions

- **Name genericity.** `docs` is a broad name. Claude Code has `/doctor` as a built-in (which is why this repo uses `machine-doctor`), but `/docs` is not a current built-in. If a future CC built-in or plugin claims `docs`, rename to e.g. `library-docs` or `fresh-docs`. Accepting this risk for now because the user explicitly chose the short name.
- **Network + token cost.** Every ctx7 call is a network round-trip and returns 1–5k tokens of doc snippets. The efficiency rules in the skill body are meant to keep this in check; if they prove insufficient we may want a session-scoped cache later.

## Testing

Manual verification after install:

1. Restart session, confirm the `docs` skill appears in the available-skills list.
2. Ask a reactive question like "how do I use `polars.scan_parquet` with a glob?" — verify Claude invokes the skill and runs `npx ctx7 library polars` then `npx ctx7 docs /pola-rs/polars "scan_parquet glob"`.
3. Start a coding task that touches a named library (e.g., "write a small FastAPI endpoint with dependency injection") — verify Claude proactively fires the skill before coding.
4. Ask a stdlib question (e.g., "how do I sort a list in Python") — verify Claude does NOT fire the skill.
