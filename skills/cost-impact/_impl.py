#!/usr/bin/env python3
"""Cost-impact analysis for Claude Code sessions.

Correct pricing per model (Opus/Sonnet/Haiku 4.5+/4.6), includes subagents,
groups sessions by repo, sorted by actual cost. Source of truth for prices:
https://platform.claude.com/docs/en/about-claude/pricing
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import defaultdict

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


def normalize_model(m):
    if not m or m == "<synthetic>":
        return None
    # strip date suffix: claude-haiku-4-5-20251001 -> claude-haiku-4-5
    m = re.sub(r"-\d{8}$", "", m)
    return m if m in PRICING else None


MAX_PLAN_MONTHLY = 200.00
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 7

today = date.today()
start_date = today - timedelta(days=DAYS_BACK - 1)

root = Path.home() / ".claude" / "projects"
main_files = sorted(root.glob("*/*.jsonl"))
sub_files = sorted(root.glob("*/*/subagents/agent-*.jsonl"))

pr_create_re = re.compile(r"^\s*gh pr create\b")
title_re = re.compile(r'--title\s+"([^"]+)"')
url_re = re.compile(r'https?://github\.com/([^/\s]+)/([^/\s)\'"]+)/pull/(\d+)')


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# Project & parent-session derivation
def parent_key(f):
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


# (project, parent_uuid, day) -> stats
bucket = defaultdict(empty_stats)


def ingest(f, is_sub):
    proj, puuid, _ = parent_key(f)
    pending = {}
    try:
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return
    for line in lines:
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

        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use" and block.get("name") == "Bash":
                    cmd = block.get("input", {}).get("command", "")
                    if pr_create_re.match(cmd.lstrip()):
                        t = title_re.search(cmd)
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
                        m = url_re.search(out)
                        pkey = (proj, puuid, tu_day)
                        if m and pkey in bucket:
                            k = (m.group(1), m.group(2), int(m.group(3)))
                            bucket[pkey]["prs_created"].append((k, tu_title))
                    for m in url_re.finditer(out):
                        k = (m.group(1), m.group(2), int(m.group(3)))
                        s["prs_referenced"].add(k)
                elif btype == "text":
                    for m in url_re.finditer(block.get("text", "")):
                        k = (m.group(1), m.group(2), int(m.group(3)))
                        s["prs_referenced"].add(k)


for f in main_files:
    ingest(f, is_sub=False)
for f in sub_files:
    ingest(f, is_sub=True)

# Fetch PR titles
all_prs = set()
for s in bucket.values():
    for k, _ in s["prs_created"]:
        all_prs.add(k)
    all_prs.update(s["prs_referenced"])
all_prs = {k for k in all_prs if not (k[0] == "a" and k[1] == "b")}

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
            titles[(owner, repo, num)] = (data.get("title", ""), data.get("state", ""))
        else:
            titles[(owner, repo, num)] = (None, None)
    except Exception:
        titles[(owner, repo, num)] = (None, None)


def money(tok, price):
    return tok / 1_000_000 * price


def cost_breakdown(s):
    """Return dict: total, by_component, by_model, naive (no-cache)."""
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
        # "naive" = what it would cost with no caching — all cached tokens rebilled as fresh input
        naive += money(t["inp"] + t["cread"] + t["c1h"] + t["c5m"], p["inp"]) + money(
            t["out"], p["out"]
        )
    return total, by_comp, dict(by_model), naive


# Aggregate everything
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
        )
    )

# Overall totals
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


def fmt_duration(minutes):
    h = int(minutes // 60)
    m = int(round(minutes - h * 60))
    if h == 0:
        return f"{m}m"
    return f"{h}h {m:02d}m"


# Per-day totals
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

# Per-repo grouping, sessions sorted by actual $ desc
by_repo = defaultdict(list)
for e in entries:
    by_repo[humanize_project(e["proj"])].append(e)
for repo in by_repo:
    by_repo[repo].sort(key=lambda x: -x["total"])
repo_totals = sorted(by_repo.items(), key=lambda kv: -sum(e["total"] for e in kv[1]))

# Build report
L = []
L.append(f"# Claude Cost Impact — {start_date} → {today}")
L.append("")
L.append(
    f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    f"{DAYS_BACK}-day window · Includes main sessions + subagents · "
    f"Pricing from [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing)*"
)
L.append("")

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
    f"| *Cache savings (reference − actual)* | *${tot_naive - tot_actual:,.2f} ({(tot_naive - tot_actual) / tot_naive * 100:.0f}%)* |"
)
L.append("")

L.append("### Cost by model")
L.append("")
L.append("| Model | Cost | Share |")
L.append("|---|---:|---:|")
for m, v in sorted(tot_by_model.items(), key=lambda x: -x[1]):
    L.append(f"| {m} | ${v:,.2f} | {v / tot_actual * 100:.1f}% |")
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
        f"| {repo} | {len(es)} | ${repo_total:,.2f} | {repo_total / tot_actual * 100:.1f}% |"
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
            gh_title, state = titles.get(k, (None, None))
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
window_baseline = daily_baseline * DAYS_BACK
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
    f"- **Max plan context (reference only)**: 7-day prorate of ${MAX_PLAN_MONTHLY:.0f}/mo = ${window_baseline:.2f}. Actual cost ${tot_actual:,.2f} implies an effective subsidy of ${subsidy:,.2f} — i.e. Anthropic is absorbing that much at list pricing. Edit `MAX_PLAN_MONTHLY` in the script if your plan is different."
)
L.append(
    "- **TTL-bug delta ([#45381](https://github.com/anthropics/claude-code/issues/45381))**: you aren't hit by it (0 `ephemeral_5m_input_tokens` observed). Not modeling hypothetical cost here — we have real data."
)
L.append(
    "- **Peak-hours quota burn**: per [u/ClaudeOfficial — Update on Session Limits (2026-03-26)](https://www.reddit.com/r/ClaudeAI/comments/1s4idaq/update_on_session_limits/), weekday 5–11am PT (1–7pm GMT) burns your 5-hour session quota faster on free/pro/max plans. Dollar cost per token is unchanged — this only affects how quickly you hit session ceilings. Shifting token-heavy work to off-peak stretches the weekly budget further. For context: on this 7-day window, ~10.6% of spend fell in the peak window."
)
L.append(
    "- **Fast mode**: not detected from the JSONL. If you used `/fast` for some turns, Anthropic bills those at 6× standard rates and my total under-reports."
)
L.append(
    "- **Sidechain PRs**: subagents can also run `gh pr create`; those are attributed to the parent session day since the extractor walks both main and sub files."
)

report = "\n".join(L)
out = Path("/tmp/cost-impact.md")
out.write_text(report)
print(f"Wrote {out} ({len(report):,} bytes, {len(entries)} session-days)")
print(
    f"Actual: ${tot_actual:,.2f} | no-cache ref: ${tot_naive:,.2f} | savings: {(tot_naive - tot_actual) / tot_naive * 100:.0f}%"
)
print(f"Repos: {len(by_repo)}, top 3: {', '.join(r for r, _ in repo_totals[:3])}")
