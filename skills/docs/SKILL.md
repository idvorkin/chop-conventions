---
name: docs
description: Use when answering questions about or writing code against a named third-party library/framework. Fetches fresh, authoritative documentation via Context7 (`ctx7`) instead of relying on stale training data. Fires both reactively ("how do I X with library Y") and proactively (about to write library code and unsure of current API). Skip for language stdlib and general CS concepts.
---

# Docs (Context7 Library Lookup)

Fetch fresh, authoritative third-party library documentation via the `ctx7` CLI instead of guessing from (possibly stale) training memory.

## When to use

**USE when:**
- The user asks "how do I X with library Y" for a named third-party library or framework.
- You are about to write code against a named third-party library and are not 100% sure the API still matches your training memory.
- A question is version-specific ("in the latest FastAPI…", "post-v18 React…").

**SKIP when:**
- The question is about a language stdlib (Python stdlib, JS built-ins, Go stdlib, etc.) — you already know these and ctx7 is wasted tokens.
- It's a general CS/programming concept, not a library.
- The code in question lives in the current repo — read the source directly instead.
- You've already fetched the same library+topic earlier in this session — reuse that answer.

## Two-step workflow

Context7 splits lookup into a name→ID resolution step and a docs-fetch step.

### Step 1: Resolve library name to a Context7 ID

```bash
npx ctx7 library <name> "<query>"
```

Always pass a query — per `ctx7 library --help` it's positional-optional, but Context7 uses it to rank candidate libraries by relevance and results get noticeably worse without it. The command returns several candidates; pick the first result unless it's obviously wrong for your query (e.g., a fork, translated mirror, or unrelated namespace match).

Example:
```bash
npx ctx7 library react "useEffect cleanup function"
# → /reactjs/react.dev  (first result)
# → /websites/react_dev  (second result)
# → /facebook/react      (fifth result — canonical repo, but not always top-ranked)
```

### Step 2: Fetch docs for the resolved ID

```bash
npx ctx7 docs <libraryId> "<query>"
```

Unlike `ctx7 library`, **both arguments are required** on `ctx7 docs` — omitting the query produces `error: missing required argument 'query'` and exits 1.

Example:
```bash
npx ctx7 docs /reactjs/react.dev "useEffect cleanup function"
```

Returns curated doc snippets ranked for the query, with source URLs.

### When to skip Step 1

Only skip the `library` step when you already resolved the ID **earlier in the current session** and you're reusing it. **Do not guess IDs from intuition** — the highest-ranked match is often not what you'd expect (`/reactjs/react.dev`, not `/facebook/react`; `/pola-rs/polars`, not `/polars/polars`).

## Efficiency rules

- **Don't re-query the same library+topic twice in one session.** Reuse the first result from earlier in the conversation.
- **Prefer specific queries over broad ones.** `"useEffect cleanup"` beats `"hooks"`; `"scan_parquet glob pattern"` beats `"polars io"`.
- **`--json` is available** on both commands if you need structured output, but plain text is fine for reading into your own context.

## ctx7 vs WebFetch

- Reach for `ctx7` first whenever the target is a **named library** — it returns curated, query-ranked snippets rather than raw HTML.
- Fall back to `WebFetch` for arbitrary URLs (blog posts, GitHub issues, RFCs, changelogs not yet indexed by Context7).

## Auth / setup

- The first `npx ctx7` invocation auto-installs the package; no manual setup needed.
- Works anonymously. If you hit rate limits or auth errors, run `npx ctx7 login` and retry.

## Common mistakes

- **Guessing the library ID instead of running `ctx7 library` first.** IDs are not predictable (`/reactjs/react.dev`, `/pola-rs/polars`). Resolve first unless you already saw the ID earlier in this session.
- **Using ctx7 for stdlib questions.** Wasted tokens — you already know `list.sort()`.
- **Re-running the same query multiple times in one session** instead of reusing the first output.
- **Ignoring errors.** `ctx7` exits 1 and prints `✖ …` or `error: …` on bad IDs, missing queries, or unknown names. If the output looks like an error, it IS — don't proceed as if you got docs. Re-run `ctx7 library` with a different name or fall back to `WebFetch`.
