#!/usr/bin/env python3
"""Cost-impact analysis for Claude Code sessions.

Correct pricing per model (Opus/Sonnet/Haiku 4.5+/4.6), includes subagents,
groups sessions by repo, sorted by actual cost. Source of truth for prices:
https://platform.claude.com/docs/en/about-claude/pricing
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# Pricing per million tokens (flat — Opus 4.6 & Sonnet 4.6 have no >200k premium)
PRICING = {
    "claude-opus-4-6": dict(inp=5.00, out=25.00, c1h=10.00, c5m=6.25, cread=0.50),
    "claude-opus-4-5": dict(inp=5.00, out=25.00, c1h=10.00, c5m=6.25, cread=0.50),
    "claude-opus-4-1": dict(inp=15.00, out=75.00, c1h=30.00, c5m=18.75, cread=1.50),
    "claude-opus-4": dict(inp=15.00, out=75.00, c1h=30.00, c5m=18.75, cread=1.50),
    "claude-sonnet-4-6": dict(inp=3.00, out=15.00, c1h=6.00, c5m=3.75, cread=0.30),
    "claude-sonnet-4-5": dict(inp=3.00, out=15.00, c1h=6.00, c5m=3.75, cread=0.30),
    "claude-sonnet-4": dict(inp=3.00, out=15.00, c1h=6.00, c5m=3.75, cread=0.30),
    "claude-haiku-4-5": dict(inp=1.00, out=5.00, c1h=2.00, c5m=1.25, cread=0.10),
    "claude-haiku-3-5": dict(inp=0.80, out=4.00, c1h=1.60, c5m=1.00, cread=0.08),
}

MAX_PLAN_MONTHLY = 200.00

# Match `gh pr create` anywhere in a command, including compound commands
# like `cd ~/gits/foo && gh pr create ...` or `git push && gh pr create ...`.
# Word boundary guards against false matches inside identifiers.
PR_CREATE_RE = re.compile(r"\bgh pr create\b")
TITLE_RE = re.compile(r'--title\s+"([^"]+)"')
URL_RE = re.compile(r'https?://github\.com/([^/\s]+)/([^/\s)\'"]+)/pull/(\d+)')


# ---------- Pure functions (importable, unit-testable) ----------


def normalize_model(m):
    """Strip the date suffix and return the canonical model key, or None.

    >>> normalize_model("claude-haiku-4-5-20251001")
    'claude-haiku-4-5'
    >>> normalize_model("<synthetic>") is None
    True
    >>> normalize_model("claude-unknown-9") is None
    True
    """
    if not m or m == "<synthetic>":
        return None
    m = re.sub(r"-\d{8}$", "", m)
    return m if m in PRICING else None


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def parent_key(f, root):
    """Map a jsonl file to (project_dirname, parent_session_uuid, is_subagent)."""
    parts = f.relative_to(root).parts
    # main: <project>/<uuid>.jsonl
    # sub:  <project>/<uuid>/subagents/agent-*.jsonl
    proj = parts[0]
    if len(parts) == 2:
        uuid = parts[1].removesuffix(".jsonl")
        return (proj, uuid, False)
    elif len(parts) >= 4 and parts[2] == "subagents":
        return (proj, parts[1], True)
    return (proj, "unknown", False)


def humanize_project(proj):
    p = proj.lstrip("-")
    p = p.replace("home-developer-gits-", "").replace("home-developer-", "")
    return p


def empty_stats():
    return dict(
        models=defaultdict(lambda: dict(inp=0, out=0, c1h=0, c5m=0, cread=0, turns=0)),
        first=None,
        last=None,
        prs_created=[],
        prs_referenced=set(),
        subagent_turns=0,
        main_turns=0,
    )


def money(tok, price):
    return tok / 1_000_000 * price


def cost_breakdown(s):
    """Return (total, by_comp, by_model, naive) for a session-day stats bucket.

    `naive` = what it would cost without caching — every token (input +
    cache-read + cache-create) billed at the model's fresh input rate, plus
    output. Used to compute cache savings.
    """
    total = 0.0
    naive = 0.0
    by_comp = dict(inp=0.0, out=0.0, c1h=0.0, c5m=0.0, cread=0.0)
    by_model = defaultdict(float)
    for model, t in s["models"].items():
        p = PRICING[model]
        comps = dict(
            inp=money(t["inp"], p["inp"]),
            out=money(t["out"], p["out"]),
            c1h=money(t["c1h"], p["c1h"]),
            c5m=money(t["c5m"], p["c5m"]),
            cread=money(t["cread"], p["cread"]),
        )
        mc = sum(comps.values())
        total += mc
        by_model[model] += mc
        for k, v in comps.items():
            by_comp[k] += v
        naive += money(t["inp"] + t["cread"] + t["c1h"] + t["c5m"], p["inp"]) + money(
            t["out"], p["out"]
        )
    return total, by_comp, dict(by_model), naive


def fmt_duration(minutes):
    h = int(minutes // 60)
    m = int(round(minutes - h * 60))
    if h == 0:
        return f"{m}m"
    return f"{h}h {m:02d}m"


def pct_or_na(num, denom):
    """Return 'NN%' or 'n/a' if denom is zero. Avoids ZeroDivisionError on empty windows."""
    if not denom:
        return "n/a"
    return f"{num / denom * 100:.0f}%"


# ---------- Impure helpers ----------


def ingest(f, is_sub, root, start_date, today, bucket, unknown_models):
    """Stream a JSONL session/subagent file into `bucket` and `unknown_models`.

    Streams line-by-line instead of loading the whole file into memory (session
    logs can exceed tens of MB when a long-running session gets unrolled).
    """
    proj, puuid, _ = parent_key(f, root)
    pending = {}
    try:
        fh = f.open("r", encoding="utf-8", errors="ignore")
    except Exception:
        return
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = parse_ts(d.get("timestamp"))
            if not ts:
                continue
            day_local = ts.astimezone().date()
            if day_local < start_date or day_local > today:
                continue

            key = (proj, puuid, day_local)
            s = bucket[key]

            msg = d.get("message", {}) if isinstance(d.get("message"), dict) else {}
            usage = msg.get("usage") or {}
            model_raw = msg.get("model")
            model = normalize_model(model_raw)

            if usage and model:
                mstats = s["models"][model]
                mstats["inp"] += usage.get("input_tokens", 0) or 0
                mstats["out"] += usage.get("output_tokens", 0) or 0
                mstats["cread"] += usage.get("cache_read_input_tokens", 0) or 0
                cc = usage.get("cache_creation", {}) or {}
                mstats["c1h"] += cc.get("ephemeral_1h_input_tokens", 0) or 0
                mstats["c5m"] += cc.get("ephemeral_5m_input_tokens", 0) or 0
                mstats["turns"] += 1
                if is_sub:
                    s["subagent_turns"] += 1
                else:
                    s["main_turns"] += 1
                if s["first"] is None or ts < s["first"]:
                    s["first"] = ts
                if s["last"] is None or ts > s["last"]:
                    s["last"] = ts
            elif usage and model_raw and not model and model_raw != "<synthetic>":
                # Priced turn with a model we don't recognize — surface it so
                # users know the report under-reports when a new model ID ships
                # before PRICING is updated. `<synthetic>` is Claude Code's
                # placeholder for internal turns that aren't billable, skip it.
                unknown_models[model_raw] = unknown_models.get(model_raw, 0) + 1

            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_use" and block.get("name") == "Bash":
                        cmd = block.get("input", {}).get("command", "")
                        if PR_CREATE_RE.search(cmd):
                            t = TITLE_RE.search(cmd)
                            pending[block.get("id")] = (
                                day_local,
                                t.group(1) if t else None,
                            )
                    elif btype == "tool_result":
                        tid = block.get("tool_use_id")
                        out = block.get("content", "")
                        if isinstance(out, list):
                            out = " ".join(
                                b.get("text", "") for b in out if isinstance(b, dict)
                            )
                        out = out or ""
                        if tid in pending:
                            tu_day, tu_title = pending.pop(tid)
                            m = URL_RE.search(out)
                            pkey = (proj, puuid, tu_day)
                            if m and pkey in bucket:
                                k = (m.group(1), m.group(2), int(m.group(3)))
                                bucket[pkey]["prs_created"].append((k, tu_title))
                        for m in URL_RE.finditer(out):
                            k = (m.group(1), m.group(2), int(m.group(3)))
                            s["prs_referenced"].add(k)
                    elif btype == "text":
                        for m in URL_RE.finditer(block.get("text", "")):
                            k = (m.group(1), m.group(2), int(m.group(3)))
                            s["prs_referenced"].add(k)


def fetch_pr_titles(all_prs):
    titles = {}
    for owner, repo, num in sorted(all_prs):
        try:
            r = subprocess.run(
                [
                    "gh",
                    "pr",
                    "view",
                    str(num),
                    "--repo",
                    f"{owner}/{repo}",
                    "--json",
                    "title,state",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                titles[(owner, repo, num)] = (
                    data.get("title", ""),
                    data.get("state", ""),
                )
            else:
                titles[(owner, repo, num)] = (None, None)
        except Exception:
            titles[(owner, repo, num)] = (None, None)
    return titles


# ---------- Report builder ----------


def build_report(entries, bucket_meta, days_back, start_date, today, titles):
    """Render the markdown report from aggregated entries.

    `bucket_meta` carries precomputed totals so we don't recompute twice:
      tot_actual, tot_naive, tot_comps, tot_by_model, tot_dur_min,
      tot_main_turns, tot_sub_turns, per_day, by_repo, repo_totals, unknown_models
    """
    L = []
    L.append(f"# Claude Cost Impact — {start_date} → {today}")
    L.append("")
    L.append(
        f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"{days_back}-day window · Includes main sessions + subagents · "
        f"Pricing from [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing)*"
    )
    L.append("")

    tot_actual = bucket_meta["tot_actual"]
    tot_naive = bucket_meta["tot_naive"]
    tot_comps = bucket_meta["tot_comps"]
    tot_by_model = bucket_meta["tot_by_model"]
    tot_dur_min = bucket_meta["tot_dur_min"]
    tot_main_turns = bucket_meta["tot_main_turns"]
    tot_sub_turns = bucket_meta["tot_sub_turns"]
    per_day = bucket_meta["per_day"]
    repo_totals = bucket_meta["repo_totals"]
    unknown_models = bucket_meta["unknown_models"]

    if not entries:
        L.append("## Summary")
        L.append("")
        L.append("*No billable turns found in the selected window.*")
        L.append("")
        L.append(
            f"- Window: {start_date} → {today} ({days_back} day{'s' if days_back != 1 else ''})"
        )
        if unknown_models:
            L.append(
                f"- ⚠ Saw {sum(unknown_models.values())} turn(s) with unpriced models: "
                + ", ".join(f"`{k}`" for k in sorted(unknown_models))
            )
        return "\n".join(L)

    L.append("## Summary")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---:|")
    L.append(f"| **Total actual cost** | **${tot_actual:,.2f}** |")
    L.append(f"| **Total duration** | **{fmt_duration(tot_dur_min)}** |")
    L.append(
        f"| **Total turns** (main / sub / total) | **{tot_main_turns:,} / {tot_sub_turns:,} / {tot_main_turns + tot_sub_turns:,}** |"
    )
    L.append(f"| Sessions in window | {len(entries)} |")
    L.append(f"| — Input (uncached) | ${tot_comps['inp']:,.2f} |")
    L.append(f"| — Output | ${tot_comps['out']:,.2f} |")
    L.append(f"| — Cache writes (1h TTL) | ${tot_comps['c1h']:,.2f} |")
    L.append(f"| — Cache writes (5m TTL) | ${tot_comps['c5m']:,.2f} |")
    L.append(f"| — Cache reads | ${tot_comps['cread']:,.2f} |")
    L.append(f"| *Without-cache reference* | *${tot_naive:,.2f}* |")
    L.append(
        f"| *Cache savings (reference − actual)* | *${tot_naive - tot_actual:,.2f} ({pct_or_na(tot_naive - tot_actual, tot_naive)})* |"
    )
    L.append("")

    L.append("### Cost by model")
    L.append("")
    L.append("| Model | Cost | Share |")
    L.append("|---|---:|---:|")
    for m, v in sorted(tot_by_model.items(), key=lambda x: -x[1]):
        L.append(f"| {m} | ${v:,.2f} | {pct_or_na(v, tot_actual)} |")
    L.append("")

    L.append("## Per day")
    L.append("")
    L.append(
        "| Day | Sessions | Main turns | Sub turns | Actual $ | 1h cache $ | Cache read $ | Output $ | No-cache ref $ |"
    )
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for day in sorted(per_day):
        d = per_day[day]
        L.append(
            f"| {day} | {d['sess']} | {d['main']} | {d['sub']} | "
            f"${d['actual']:.2f} | ${d['c1h']:.2f} | ${d['cread']:.2f} | ${d['out']:.2f} | ${d['naive']:.2f} |"
        )
    L.append("")

    L.append("## Per repo")
    L.append("")
    L.append("| Repo | Sessions | Actual $ | Share |")
    L.append("|---|---:|---:|---:|")
    for repo, es in repo_totals:
        repo_total = sum(e["total"] for e in es)
        L.append(
            f"| {repo} | {len(es)} | ${repo_total:,.2f} | {pct_or_na(repo_total, tot_actual)} |"
        )
    L.append("")

    L.append("## Sessions grouped by repo (sorted by actual $ within each repo)")
    L.append("")
    L.append("*Click a repo to expand its session detail.*")
    L.append("")
    for repo, es in repo_totals:
        repo_total = sum(e["total"] for e in es)
        repo_dur = sum(e["dur"] for e in es)
        repo_main = sum(e["main_turns"] for e in es)
        repo_sub = sum(e["sub_turns"] for e in es)
        L.append("<details>")
        L.append(
            f"<summary><b>{repo}</b> — ${repo_total:,.2f} · "
            f"{len(es)} session{'s' if len(es) != 1 else ''} · "
            f"{fmt_duration(repo_dur)} · "
            f"{repo_main:,} main + {repo_sub:,} sub turns</summary>"
        )
        L.append("")
        L.append(
            "| Day | Dur | Session | Turns (main/sub) | Actual $ | 1h $ | Read $ | Out $ | PRs shipped |"
        )
        L.append("|---|---:|---|---:|---:|---:|---:|---:|---|")
        for e in es:
            sess = e["puuid"][:8]
            pr_links = []
            for k, local_title in e["prs_created"]:
                if k[0] == "a" and k[1] == "b":
                    continue
                gh_title, _state = titles.get(k, (None, None))
                shown = local_title or gh_title or ""
                url = f"https://github.com/{k[0]}/{k[1]}/pull/{k[2]}"
                pr_links.append(f"[{k[0]}/{k[1]}#{k[2]}]({url}) — {shown}")
            pr_str = "<br>".join(pr_links) if pr_links else "—"
            L.append(
                f"| {e['day']} | {e['dur']:.0f}m | {sess} | "
                f"{e['main_turns']}/{e['sub_turns']} | "
                f"${e['total']:.2f} | ${e['comps']['c1h']:.2f} | "
                f"${e['comps']['cread']:.2f} | ${e['comps']['out']:.2f} | {pr_str} |"
            )
        L.append("")
        L.append("</details>")
        L.append("")

    # Footnotes
    daily_baseline = MAX_PLAN_MONTHLY / 30
    window_baseline = daily_baseline * days_back
    subsidy = tot_actual - window_baseline

    L.append("## Footnotes")
    L.append("")
    L.append(
        "- **Subagent coverage**: script scans `~/.claude/projects/*/*.jsonl` (main sessions) and `~/.claude/projects/*/*/subagents/agent-*.jsonl` (subagents). Subagent tokens are rolled into the parent session's totals so the per-session cost reflects all work done on behalf of that session."
    )
    L.append(
        "- **Per-model pricing**: each turn is billed at its model's listed rate (Opus 4.6/4.5 $5/$25, Sonnet 4.6/4.5 $3/$15, Haiku 4.5 $1/$5, older Opus tiers at their higher historic rates). Opus 4.6 and Sonnet 4.6 are flat-priced across the full 1M context window — no 200k threshold."
    )
    L.append(
        "- **Without-cache reference** = `input + cache_read + cache_create` all billed as fresh input at that model's base input rate, plus output. Shows what you'd pay if caching were disabled entirely. Not a prediction — a reference point."
    )
    L.append(
        "- **Cache savings** = reference − actual. Represents the value your session got from prompt caching."
    )
    L.append(
        f"- **Max plan context (reference only)**: {days_back}-day prorate of ${MAX_PLAN_MONTHLY:.0f}/mo = ${window_baseline:.2f}. Actual cost ${tot_actual:,.2f} implies an effective subsidy of ${subsidy:,.2f} — i.e. Anthropic is absorbing that much at list pricing. Edit `MAX_PLAN_MONTHLY` in the script if your plan is different."
    )
    # TTL-bug footnote: compute the actual c5m token count instead of
    # hardcoding a claim we didn't measure (Copilot review #5).
    total_c5m_tokens = 0
    for e in entries:
        for _model, t in e.get("raw_models", {}).items():
            total_c5m_tokens += t.get("c5m", 0)
    if total_c5m_tokens == 0:
        ttl_note = (
            "- **TTL-bug delta ([#45381](https://github.com/anthropics/claude-code/issues/45381))**: "
            "not hit by the bug — `0` `ephemeral_5m_input_tokens` observed in this window."
        )
    else:
        ttl_note = (
            f"- **TTL-bug delta ([#45381](https://github.com/anthropics/claude-code/issues/45381))**: "
            f"observed `{total_c5m_tokens:,}` `ephemeral_5m_input_tokens` tokens in this window "
            f"(`${tot_comps['c5m']:,.2f}` at 5m cache-write rates). "
            f"If `DISABLE_TELEMETRY` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is set, "
            f"these are sessions that silently fell from the 1h to 5m cache tier."
        )
    L.append(ttl_note)
    L.append(
        "- **Peak-hours quota burn**: per [u/ClaudeOfficial — Update on Session Limits (2026-03-26)](https://www.reddit.com/r/ClaudeAI/comments/1s4idaq/update_on_session_limits/), weekday 5–11am PT (1–7pm GMT) burns your 5-hour session quota faster on free/pro/max plans. Dollar cost per token is unchanged — this only affects how quickly you hit session ceilings. Shifting token-heavy work to off-peak stretches the weekly budget further."
    )
    L.append(
        "- **Fast mode**: not detected from the JSONL. If you used `/fast` for some turns, Anthropic bills those at 6× standard rates and my total under-reports."
    )
    L.append(
        "- **Sidechain PRs**: subagents can also run `gh pr create`; those are attributed to the parent session day since the extractor walks both main and sub files."
    )
    if unknown_models:
        L.append(
            f"- ⚠ **Unpriced models**: saw {sum(unknown_models.values())} turn(s) with "
            f"unrecognised model IDs: {', '.join(f'`{k}` ({v})' for k, v in sorted(unknown_models.items()))}. "
            f"These turns were excluded from the cost totals — update `PRICING` in `_impl.py` "
            f"when a new model ships."
        )

    return "\n".join(L)


def aggregate(bucket):
    """Collapse the session-day bucket into entries + rolled-up totals."""
    entries = []
    for key, s in bucket.items():
        total, comps, by_model, naive = cost_breakdown(s)
        if total == 0:
            continue
        dur_min = 0
        if s["first"] and s["last"]:
            dur_min = (s["last"] - s["first"]).total_seconds() / 60
        entries.append(
            dict(
                key=key,
                proj=key[0],
                puuid=key[1],
                day=key[2],
                total=total,
                naive=naive,
                comps=comps,
                by_model=by_model,
                dur=dur_min,
                main_turns=s["main_turns"],
                sub_turns=s["subagent_turns"],
                prs_created=s["prs_created"],
                prs_referenced=s["prs_referenced"],
                # Keep raw per-model token counts for downstream footnotes
                # (TTL-bug c5m measurement).
                raw_models={m: dict(t) for m, t in s["models"].items()},
            )
        )

    tot_actual = sum(e["total"] for e in entries)
    tot_naive = sum(e["naive"] for e in entries)
    tot_comps = dict(inp=0, out=0, c1h=0, c5m=0, cread=0)
    tot_by_model = defaultdict(float)
    tot_dur_min = 0.0
    tot_main_turns = 0
    tot_sub_turns = 0
    for e in entries:
        for k in tot_comps:
            tot_comps[k] += e["comps"][k]
        for m, v in e["by_model"].items():
            tot_by_model[m] += v
        tot_dur_min += e["dur"]
        tot_main_turns += e["main_turns"]
        tot_sub_turns += e["sub_turns"]

    per_day = defaultdict(
        lambda: dict(
            actual=0, naive=0, c1h=0, cread=0, out=0, turns=0, sub=0, main=0, sess=0
        )
    )
    for e in entries:
        d = per_day[e["day"]]
        d["actual"] += e["total"]
        d["naive"] += e["naive"]
        d["c1h"] += e["comps"]["c1h"]
        d["cread"] += e["comps"]["cread"]
        d["out"] += e["comps"]["out"]
        d["main"] += e["main_turns"]
        d["sub"] += e["sub_turns"]
        d["sess"] += 1

    by_repo = defaultdict(list)
    for e in entries:
        by_repo[humanize_project(e["proj"])].append(e)
    for repo in by_repo:
        by_repo[repo].sort(key=lambda x: -x["total"])
    repo_totals = sorted(
        by_repo.items(), key=lambda kv: -sum(e["total"] for e in kv[1])
    )

    return dict(
        entries=entries,
        tot_actual=tot_actual,
        tot_naive=tot_naive,
        tot_comps=tot_comps,
        tot_by_model=dict(tot_by_model),
        tot_dur_min=tot_dur_min,
        tot_main_turns=tot_main_turns,
        tot_sub_turns=tot_sub_turns,
        per_day=per_day,
        by_repo=by_repo,
        repo_totals=repo_totals,
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Compute Claude Code cost-impact report for a recent time window."
    )
    p.add_argument(
        "days",
        nargs="?",
        type=positive_int,
        default=7,
        help="Number of days back to scan (integer >= 1, default 7)",
    )
    return p.parse_args(argv)


def positive_int(s):
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected integer, got {s!r}")
    if v < 1:
        raise argparse.ArgumentTypeError(f"days must be >= 1, got {v}")
    return v


def main(argv=None):
    args = parse_args(argv)
    days_back = args.days

    today = date.today()
    start_date = today - timedelta(days=days_back - 1)

    root = Path.home() / ".claude" / "projects"
    main_files = sorted(root.glob("*/*.jsonl"))
    sub_files = sorted(root.glob("*/*/subagents/agent-*.jsonl"))

    bucket = defaultdict(empty_stats)
    unknown_models = {}

    for f in main_files:
        ingest(f, False, root, start_date, today, bucket, unknown_models)
    for f in sub_files:
        ingest(f, True, root, start_date, today, bucket, unknown_models)

    if unknown_models:
        for m, n in sorted(unknown_models.items()):
            print(
                f"warning: unpriced model {m!r} ({n} turn(s)) — cost report excludes these",
                file=sys.stderr,
            )

    agg = aggregate(bucket)

    all_prs = set()
    for s in bucket.values():
        for k, _ in s["prs_created"]:
            all_prs.add(k)
        all_prs.update(s["prs_referenced"])
    all_prs = {k for k in all_prs if not (k[0] == "a" and k[1] == "b")}
    titles = fetch_pr_titles(all_prs)

    bucket_meta = dict(agg, unknown_models=unknown_models)
    report = build_report(
        agg["entries"], bucket_meta, days_back, start_date, today, titles
    )

    out = Path("/tmp/cost-impact.md")
    out.write_text(report)
    n_entries = len(agg["entries"])
    tot_actual = agg["tot_actual"]
    tot_naive = agg["tot_naive"]
    print(f"Wrote {out} ({len(report):,} bytes, {n_entries} session-days)")
    print(
        f"Actual: ${tot_actual:,.2f} | "
        f"no-cache ref: ${tot_naive:,.2f} | "
        f"savings: {pct_or_na(tot_naive - tot_actual, tot_naive)}"
    )
    repo_totals = agg["repo_totals"]
    print(
        f"Repos: {len(agg['by_repo'])}, top 3: {', '.join(r for r, _ in repo_totals[:3])}"
    )


if __name__ == "__main__":
    main()
