---
name: cost-impact
description: Compute a Claude Code cost-impact report across a time window, grouped by repo with per-session detail. Prices per-turn per-model at Opus 4.6 / Sonnet 4.6 / Haiku 4.5 list rates, includes subagent tokens, outputs markdown with clickable PR links and collapsible per-repo sections. Use when user asks "how much did I spend on Claude this week", "cost report", "/cost-impact", or wants a weekly retrospective on Claude usage.
allowed-tools: Bash, Read
---

# Cost Impact

Generate a markdown report of your Claude Code spend over the last N days,
grouped by repo, with clickable links to PRs shipped during each session.

**Announce at start:** "I'm using the cost-impact skill to compute your
Claude spend for the last N days."

## When to use

- User asks: "how much did I spend on Claude this week?", "Claude cost
  report", "weekly Claude retro", "/cost-impact"
- User wants to investigate a specific day's unusual spend
- End-of-week debrief on where the Claude budget went

## Inputs

One optional positional argument: **number of days back** (integer, default `7`).

```
/cost-impact        # last 7 days (default)
/cost-impact 1      # just today
/cost-impact 14     # last 2 weeks
```

If the user names a specific day ("yesterday", "last Friday"), compute the
integer offset from today and pass that — the underlying script is
window-based, not date-based.

## Ask destination FIRST

**Before running `_impl.py`, ask the user where the report should go:**

> "Where should this go — public gist, or local save to
> `~/tmp/cost-impact-YYYY-MM-DD.md`?"

Ask this up front so (a) the user doesn't have to context-switch after
waiting for the script, and (b) the privacy guard below can short-circuit
the run if a gist is selected and the content is sensitive. The destination
answer is the only thing that gates how the script's output is routed — it
does NOT change what `_impl.py` computes.

If the user already has a cost-impact gist they want updated in place, ask
which gist ID (see "Updating an existing gist" below). Treat an in-place
edit as a gist publish for the purposes of the privacy guard.

## How to run

```bash
python3 ~/gits/chop-conventions/skills/cost-impact/_impl.py <days>
```

The script:

1. Scans `~/.claude/projects/*/*.jsonl` (main sessions) and
   `~/.claude/projects/*/*/subagents/agent-*.jsonl` (subagent transcripts)
2. Filters to turns whose `timestamp` (converted to local TZ) falls in
   the requested window
3. Bills each turn at its model's published list price — pricing table
   in `_impl.py::PRICING` tracks Opus 4.6/4.5, Sonnet 4.6/4.5/4,
   Haiku 4.5/3.5, and older tiers
4. Rolls subagent costs into the parent session so per-session totals
   reflect all work done on behalf of that session
5. Groups results by project, then by parent-session UUID, then by day
6. Writes `/tmp/cost-impact.md` and prints a one-line summary to stdout

Expected stdout shape:

```
Wrote /tmp/cost-impact.md (16,936 bytes, 64 session-days)
Actual: $1,244.92 | no-cache ref: $6,933.49 | savings: 82%
Repos: 11, top 3: activation-energy-game, settings, blog4
```

If you see `Actual: $0.00` the window is probably wrong — check that
`date` shows today and that session JSONLs have fresh timestamps.

## Routing the output

Destination was chosen up front (see "Ask destination FIRST"). After
`_impl.py` writes `/tmp/cost-impact.md`:

**If the user chose gist: run the privacy guard first (see below). Only if
the guard passes, publish:**

```bash
gh gist create --public \
  -d "Claude cost impact YYYY-MM-DD (N-day window)" \
  /tmp/cost-impact.md
```

Relay the gist URL back.

**If the user chose local save** (always allowed — it's their own disk, no
privacy check needed):

```bash
mkdir -p ~/tmp
cp /tmp/cost-impact.md ~/tmp/cost-impact-$(date +%Y-%m-%d).md
```

Relay the absolute path back.

### Updating an existing gist

**If the user already has a cost-impact gist** and the new report
supersedes it, prefer `gh gist edit <id> /tmp/cost-impact.md` to update
in place so shared links don't break. Ask which gist to update if you
don't know the ID. Run the privacy guard before editing — an in-place
edit to a public gist is a publish.

## Privacy guard (gist destinations only)

Before running `gh gist create` or `gh gist edit` against a public gist,
**read `/tmp/cost-impact.md` and judge whether the content would leak
information the user wouldn't want public.** This is assistant judgment, not
a mechanical check. Look for:

- Repo names that identify private, client, or unreleased projects
  (anything not already visible on the user's public GitHub profile)
- PR URLs pointing at private repos
- Session UUIDs that could correlate with private incidents if cross-referenced
- Session titles or PR titles that reference secrets, credentials, internal
  codenames, or people by name
- Hostnames that reveal internal network naming conventions beyond the
  user's already-public setup

**If the report looks sensitive: STOP the gist upload.** Do NOT publish.
Tell the user *what specifically* looked sensitive (cite the repo name,
PR URL, or line), and offer the local-save path instead:

> "Holding off on the gist — I see `<specific thing>` in the report which
> looks like it'd expose a private project. Saving locally to
> `~/tmp/cost-impact-YYYY-MM-DD.md` instead, or want to scrub and retry?"

**Local save is always allowed regardless of sensitivity** — the user's own
disk is not a publication surface.

When in doubt, prefer refusing the gist. A false-positive refusal costs the
user one prompt ("go ahead, it's fine") — a false-negative publish can't be
unsent once the gist URL is indexed.

## What the report contains

1. **Summary table** — total $, total duration, total turns (main/sub/total),
   sessions in window, host (from `socket.gethostname()` — load-bearing
   provenance when the report is shared out of context), cost breakdown
   (input/output/cache writes/reads), without-cache reference, cache savings %
2. **Cost by model** — each model's share of the total
3. **Per day** — actual $, sessions, main+sub turns, no-cache reference, plus
   a separate daily details table splitting input, output, 1h cache
   writes, 5m cache writes, and cache reads
4. **Per repo summary table** — sessions, actual $, share %
5. **Sessions grouped by repo** — collapsible `<details>` sections, one per
   repo, each showing its sessions sorted by actual $ descending. Per-session
   row: day, duration, session UUID prefix, turns main/sub, costs split out,
   clickable PR links (from `gh pr create` commands found in the session)
6. **Footnotes** — methodology notes, known caveats, links to relevant
   Anthropic docs and GitHub issues

## Knobs in `_impl.py`

Edit these constants at the top of the file if your setup differs:

| Constant           | Purpose                                                                    | Default                                             |
| ------------------ | -------------------------------------------------------------------------- | --------------------------------------------------- |
| `PRICING`          | Per-model price table (input/output/cache-write-1h/5m/cache-read per MTok) | Opus 4.6 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5 |
| `MAX_PLAN_MONTHLY` | Used only for the subsidy footnote                                         | `200.00`                                            |

Pricing is sourced from
[platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing).
Update the table if Anthropic changes published rates.

## Hard rules

- **Do NOT** modify `_impl.py` to bake in a specific user's values
  (repo list, gist ID, plan cost, hostname). Keep it generic — the
  hostname comes from `socket.gethostname()` at runtime, not a constant.
- **Do NOT** post the report to a gist without running the privacy guard
  (see "Privacy guard" above). The report contains session names, PR
  URLs, and repo paths that may identify private projects.
- **Do NOT** run this on a machine that isn't the user's — it reads
  `~/.claude/projects/` which contains conversation history.
- **Ask before updating an existing gist in place** — the previous
  version of the report may have been shared with someone, and
  editing it in place will change what they see next time they load it.
  In-place edits are subject to the same privacy guard as new publishes.

## Known caveats (surfaced in the report's footnotes)

- **Fast mode invisible**: `usage` field in JSONL doesn't flag fast mode
  turns, so if the user hit `/fast` those are undercounted by 6×. No
  current way to detect.
- **Peak-hours quota burn** (weekday 5–11am PT) affects how quickly
  Max plan session quota gets consumed, not the $ per token. The
  report prices at list; peak vs off-peak is not reflected in the
  dollar column.
- **Opus 4.6 / Sonnet 4.6 flat pricing**: no 200k threshold — older
  4 / 4.1 tiers had 2× above 200k, but 4.5+ bill flat across the
  full 1M context window.
- **TTL bug ([anthropics/claude-code#45381](https://github.com/anthropics/claude-code/issues/45381))**:
  if `DISABLE_TELEMETRY` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
  is set, sessions silently fall from 1h to 5m cache tier, costing
  more. The report footnote measures `ephemeral_5m_input_tokens`
  directly and reports whether you were hit.
- **Unknown / unpriced models**: turns with a model ID not in
  `PRICING` are excluded from totals and surfaced as a stderr warning
  at run time + an `⚠ Unpriced models` line in the report's footnotes.
  Update the `PRICING` table in `_impl.py` when a new model ships.

## Related

- [Anthropic pricing docs](https://platform.claude.com/docs/en/about-claude/pricing)
- [Session limits update (u/ClaudeOfficial, 2026-03-26)](https://www.reddit.com/r/ClaudeAI/comments/1s4idaq/update_on_session_limits/)
- anthropics/claude-code#45381 — TTL bug
