#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Count how often this repo's skills are used, from Claude Code session transcripts.

    ./dev-setup/tools/skill_usage.py            # table, most-used first
    ./dev-setup/tools/skill_usage.py --json     # machine-readable
    ./dev-setup/tools/skill_usage.py --projects DIR --skills DIR

A use is either
  * a Skill tool call: Claude Code records the result line with
    `toolUseResult.commandName` (this covers calls made directly and from
    inside REPL scripts), or
  * a slash command the user typed: a user message holding
    `<command-name>/name</command-name>`.

Only names of directories under skills/ count; prefixed names such as
`anthropic-skills:docs` belong to other packages and are skipped. Lines are
deduped on their `uuid`, since resumed and forked sessions copy history
into new files.

Transcripts are pruned by Claude Code (cleanupPeriodDays, 30 days by
default), so the counts cover only the window the report prints, and only
this machine.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
COMMAND_RE = re.compile(r"<command-name>/?([\w:.-]+)</command-name>")


@dataclass(frozen=True)
class Use:
    skill: str
    session: str
    timestamp: str
    source: str  # "tool" or "typed"


@dataclass
class Report:
    known: list[str]
    uses: list[Use] = field(default_factory=list)
    first_ts: str | None = None
    last_ts: str | None = None
    files: int = 0


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return []


def uses_in_record(record: dict, known: set[str]) -> list[Use]:
    """The skill uses one transcript record shows, limited to `known` names."""
    session = record.get("sessionId") or record.get("session_id") or ""
    ts = record.get("timestamp") or ""
    found: list[Use] = []
    result = record.get("toolUseResult")
    if isinstance(result, dict) and isinstance(result.get("commandName"), str):
        name = result["commandName"]
        if name in known:
            found.append(Use(name, session, ts, "tool"))
    if record.get("type") == "user":
        message = record.get("message") or {}
        for text in _texts(message.get("content")):
            for name in COMMAND_RE.findall(text):
                if name in known:
                    found.append(Use(name, session, ts, "typed"))
    return found


def scan(lines_by_file, known: list[str]) -> Report:
    """Fold transcript lines into a report. `lines_by_file` yields iterables of raw JSONL lines."""
    report = Report(known=sorted(known))
    known_set = set(known)
    seen: set[str] = set()
    for lines in lines_by_file:
        report.files += 1
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            ts = record.get("timestamp")
            if isinstance(ts, str) and ts:
                report.first_ts = min(report.first_ts or ts, ts)
                report.last_ts = max(report.last_ts or ts, ts)
            uid = record.get("uuid")
            if uid:
                if uid in seen:
                    continue
                seen.add(uid)
            report.uses.extend(uses_in_record(record, known_set))
    return report


def summarize(report: Report) -> list[dict]:
    """One row per known skill: uses, distinct sessions, last use. Most-used first, unused last."""
    rows = []
    for name in report.known:
        mine = [u for u in report.uses if u.skill == name]
        rows.append(
            {
                "skill": name,
                "uses": len(mine),
                "sessions": len({u.session for u in mine}),
                "typed": sum(u.source == "typed" for u in mine),
                "last_used": max((u.timestamp for u in mine), default=None),
            }
        )
    rows.sort(key=lambda r: (-r["uses"], r["skill"]))
    return rows


def render(report: Report, rows: list[dict]) -> str:
    window = f"{(report.first_ts or '?')[:10]} .. {(report.last_ts or '?')[:10]}"
    out = [f"Skill usage from {report.files} transcript files, {window} (this machine only)", ""]
    out.append(f"{'uses':>5} {'sessions':>8} {'typed':>5}  {'last used':<10}  skill")
    for r in rows:
        last = (r["last_used"] or "-")[:10]
        out.append(f"{r['uses']:>5} {r['sessions']:>8} {r['typed']:>5}  {last:<10}  {r['skill']}")
    unused = [r["skill"] for r in rows if r["uses"] == 0]
    out += ["", f"{len(unused)} of {len(rows)} skills unused in this window"]
    return "\n".join(out)


def known_skills(skills_dir: Path) -> list[str]:
    return sorted(p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file())


def _read_files(projects_dir: Path):
    for path in sorted(projects_dir.rglob("*.jsonl")):
        try:
            with path.open(errors="replace") as fh:
                yield list(fh)
        except OSError as e:
            print(f"skip {path}: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Count how often this repo's skills are used.")
    ap.add_argument("--projects", type=Path, default=PROJECTS_DIR, help="transcript root (default ~/.claude/projects)")
    ap.add_argument("--skills", type=Path, default=SKILLS_DIR, help="skills directory whose names count")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)
    if not args.projects.is_dir():
        print(f"no transcripts at {args.projects}", file=sys.stderr)
        return 1
    report = scan(_read_files(args.projects), known_skills(args.skills))
    rows = summarize(report)
    if args.json:
        print(json.dumps({"window": [report.first_ts, report.last_ts], "files": report.files, "skills": rows}, indent=2))
    else:
        print(render(report, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
