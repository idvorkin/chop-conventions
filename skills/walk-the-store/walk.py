#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Walk the store: turn a repo's docs/stories into checks, verdicts and a report.

The repo runs its own checks (`just walk`); this script does the parts that are
the same in every repo:

    walk.py stories <stories-dir>             every story as JSON: id, status class, named checks, scenarios
    walk.py refs <stories-dir>                the check refs the walk should run, one per line, by kind
    walk.py judge --stories D --checks C.json --out OUT [--manifest M.toml] [--previous LAST/walk.json]
                  [--session-tokens JSON]     join the runner's outcomes into one verdict per story,
                                              write OUT/walk.json and OUT/report.md, exit 1 on a new red,
                                              2 when the runner killed a stage at its cap
    walk.py file-reds OUT/walk.json [--apply] [--parent ID]
                                              one bead per new red, deduped by external ref; prints
                                              the bd commands unless --apply

Stdlib only. Pure functions are tested by test_walk.py; the only subprocess is
`bd`, behind run_bd().
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- parsing

# Two dialects seen in the wild:
#   ### User Story 001:            then   - **Status:** ...
#   ## 001 — Choose the mic        then   **Status**: ...
HEADING_RE = re.compile(
    r"^(?:###\s+User Story\s+(?P<a>\d+)\s*:?\s*(?P<at>.*)"
    r"|##\s+(?P<b>\d+)\s+[—–-]\s+(?P<bt>.+))$"
)
FIELD_RE = re.compile(
    r"^\s*(?:-\s+)?\*\*(?P<name>[A-Za-z ]+?)(?::\*\*|\*\*:)\s*(?P<value>.*)$"
)
SCENARIO_RE = re.compile(
    r"^\s*-\s+(?:\*\*)?(?P<key>Scenario|Given|When|Then|And)(?::\*\*|\*\*:|:)?\s*(?P<value>.*)$"
)


@dataclass
class Scenario:
    name: str
    given: list[str] = field(default_factory=list)
    when: list[str] = field(default_factory=list)
    then: list[str] = field(default_factory=list)


@dataclass
class Story:
    id: str
    title: str
    file: str
    summary: str = ""
    status: str = ""
    status_class: str = "unclassified"
    refs: list[str] = field(default_factory=list)
    rungs: list[str] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)


def parse_file(text: str, file: str) -> list[Story]:
    """Split one journey file into stories. Field order is not assumed."""
    stories: list[Story] = []
    cur: Story | None = None
    last_field = ""
    scen: Scenario | None = None
    last_step = ""
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            sid = m.group("a") or m.group("b")
            title = (m.group("at") or m.group("bt") or "").strip()
            cur = Story(id=sid, title=title, file=file)
            stories.append(cur)
            last_field, scen, last_step = "", None, ""
            continue
        if cur is None:
            continue
        if line.startswith("#") and not line.startswith("####"):
            cur, last_field = None, ""  # a non-story heading ends the story
            continue
        s = SCENARIO_RE.match(line)
        if s:
            key, value = s.group("key"), s.group("value").strip()
            if key == "Scenario":
                scen = Scenario(name=value)
                cur.scenarios.append(scen)
                last_step = ""
            elif scen is not None:
                step = last_step if key == "And" else key.lower()
                if step:
                    getattr(scen, step).append(value)
                    last_step = step
            last_field = ""
            continue
        f = FIELD_RE.match(line)
        if f:
            name, value = f.group("name").strip().lower(), f.group("value").strip()
            last_field = name
            if name == "summary":
                cur.summary = value
            elif name == "status":
                cur.status = value
            continue
        # audio-debugger wraps Status over several lines
        if (
            last_field == "status"
            and line.strip()
            and not line.lstrip().startswith(("-", "*", "#"))
        ):
            cur.status += " " + line.strip()
        elif not line.strip():
            last_field = ""
    for st in stories:
        if not st.title:
            st.title = st.summary
        st.status_class = classify_status(st.status)
        st.refs = extract_refs(st.status)
        st.rungs = rung_claims(st.status)
    return stories


def parse_dir(stories_dir: Path) -> list[Story]:
    out: list[Story] = []
    for path in sorted(stories_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        out.extend(parse_file(path.read_text(encoding="utf-8"), path.name))
    return out


# ---------------------------------------------------------------- status

RETIRED_RE = re.compile(r"^(superseded|replaced|removed|retired|dropped)\b", re.I)
PLANNED_RE = re.compile(
    r"^(planned|not implemented|not built|parked|in progress|todo|proposed)\b", re.I
)
IMPLEMENTED_RE = re.compile(
    r"\b(implemented|done|already true|shipped|built)\b|^(started|partly)\b", re.I
)


def classify_status(status: str) -> str:
    """implemented / planned / retired / unclassified, from the Status line's lead clause.

    Only the lead clause decides: "implemented in X; the old flow superseded" is
    implemented, "superseded by 140; before that implemented in X" is retired.
    """
    s = re.sub(r"[\[\]*`]", "", status).strip()
    if not s:
        return "unclassified"
    # A scope qualifier ("native app only:", "app side") is not the status itself.
    s = re.sub(r"^(?:[\w -]{1,25} only:|app side)\s*", "", s, flags=re.I)
    lead = re.split(r"[;.(]", s, maxsplit=1)[0].strip()
    if re.match(r"^[0-9a-f]{7,40}\b", lead):
        return "implemented"  # leads with the commit that built it
    if RETIRED_RE.match(lead):
        return "retired"
    if PLANNED_RE.match(lead):
        return "planned"
    if IMPLEMENTED_RE.search(lead):
        return "implemented"
    return "unclassified"


# A ref is "<kind>:<target>". The kinds are generic; a manifest maps each kind to a surface.
TICK_RE = re.compile(r"`([^`]+)`")
PY_RE = re.compile(
    r"^(?P<file>[\w./-]*test_[\w.-]*\.py)(?:::(?P<test>[\w\[\]-]+(?:::[\w\[\]-]+)?))?$"
)
SHORT_PY_TEST_RE = re.compile(
    r"^::(?P<test>test_\w+(?:\[[^\]]*\])?)$"
)  # `::test_x`: the file just named
BARE_PY_TEST_RE = re.compile(
    r"^(?P<test>test_\w+(?:\[[^\]]*\])?)$"
)  # `test_x`: any file
# A script is a check only when its name says so; `annotate.js` is source, not a test.
NODE_RE = re.compile(
    r"^(?P<file>[\w./-]*(?:test|audit|smoke|spec)[\w.-]*\.(?:cjs|mjs|js))$"
)
# A repo's own script check, named like `scripts/native/test-ladder-map.sh` or `test-board-sessions.py` (hyphen, not
# pytest's underscore). Keyed by basename: stories write it with and without its directory.
SCRIPT_RE = re.compile(r"^(?P<file>[\w./-]*?(?P<base>test-[\w.-]*\.(?:sh|py)))$")
XCTEST_RE = re.compile(r"^(?P<cls>[A-Z]\w*Tests)(?:[./](?P<test>test\w+))?$")
JUST_RE = re.compile(r"^just\s+(?P<recipe>[\w:-]+)")
LANDMARK_RE = re.compile(r"^[a-z][a-z0-9-]+$")


def extract_refs(status: str) -> list[str]:
    """Named checks in a Status line, in order, deduped.

    Handles the shorthand the stories use: a bare `::test_x` or `test_x` after a
    `test_file.py` belongs to that file, a bare `test_x` matches that test in any file, and `route_smoke.cjs` `label` landmark
    names a landmark of that script (the word "landmark" must follow the label).
    """
    refs: list[str] = []
    last_py = ""
    last_node = ""
    for tm in TICK_RE.finditer(status):
        tok = tm.group(1).strip()
        after = status[tm.end() : tm.end() + 40]
        if m := PY_RE.match(tok):
            last_py, last_node = m.group("file"), ""
            ref = f"pytest:{m.group('file')}" + (
                f"::{m.group('test')}" if m.group("test") else ""
            )
        elif (m := SHORT_PY_TEST_RE.match(tok)) and last_py:
            ref = f"pytest:{last_py}::{m.group('test')}"
        elif m := BARE_PY_TEST_RE.match(tok):
            # prose names a test without its file: it often lives in another file than the last one named
            ref = f"pytest:*::{m.group('test')}"
        elif m := SCRIPT_RE.match(tok):
            last_py = last_node = ""
            ref = f"script:{m.group('base')}"
        elif m := NODE_RE.match(tok):
            last_node, last_py = m.group("file"), ""
            ref = f"node:{m.group('file')}"
        elif last_node and LANDMARK_RE.match(tok) and "landmark" in after.split(";")[0]:
            ref = f"landmark:{last_node}#{tok}"
        elif m := XCTEST_RE.match(tok):
            last_py = last_node = ""
            ref = f"xctest:{m.group('cls')}" + (
                f".{m.group('test')}" if m.group("test") else ""
            )
        elif m := JUST_RE.match(tok):
            last_py = last_node = ""
            ref = f"just:{m.group('recipe')}"
        else:
            continue
        if ref not in refs:
            refs.append(ref)
    # A landmark makes the bare script ref redundant: the landmark is the more specific check.
    with_marks = {
        r.split("#", 1)[0].replace("landmark:", "node:")
        for r in refs
        if r.startswith("landmark:")
    }
    return [r for r in refs if r not in with_marks]


RUNG_PATTERNS = {
    "host": r"\bhost\b|host tests?|fast rung|selftest",
    "browser": r"browser rung|browser audit|playwright",
    "simulator": r"\bsimulator\b|\bsim\b",
    "device": r"\bphone\b|\bdevice\b|\bwatch\b|\biPad\b",
    "human": r"by hand|\w+'s check|\(run[:,]? \w+\)|run[:,] \w+\)|by eye|not verified",
}


def rung_claims(status: str) -> list[str]:
    return [r for r, pat in RUNG_PATTERNS.items() if re.search(pat, status, re.I)]


# ---------------------------------------------------------------- manifest

DEFAULT_SURFACES = {
    "pytest": "host",
    "xctest": "host",
    "node": "web",
    "landmark": "web",
    "just": "host",
    "script": "host",
}


@dataclass
class Manifest:
    surfaces: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SURFACES))
    ignore_refs: list[str] = field(
        default_factory=list
    )  # regexes: cross-repo names, prose
    stories: dict[str, dict] = field(
        default_factory=dict
    )  # id -> {checks, untestable, surface, skip}


def load_manifest(path: Path | None) -> Manifest:
    m = Manifest()
    if path is None or not path.exists():
        return m
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    m.surfaces.update(data.get("surfaces", {}))
    m.ignore_refs = list(data.get("ignore_refs", []))
    m.stories = {
        str(k).zfill(3) if k.isdigit() else k: v
        for k, v in data.get("stories", {}).items()
    }
    return m


def story_checks(story: Story, manifest: Manifest) -> list[str]:
    over = manifest.stories.get(story.id, {})
    refs = list(over["checks"]) if "checks" in over else list(story.refs)
    return [r for r in refs if not any(re.search(p, r) for p in manifest.ignore_refs)]


def surface_of(ref: str, manifest: Manifest) -> str:
    return manifest.surfaces.get(ref.split(":", 1)[0], "host")


# ---------------------------------------------------------------- judging

VERDICTS = ("green", "red", "killed", "skipped", "untestable")
OUT_OF_SCOPE = ("planned", "retired", "unclassified")


def match_outcome(ref: str, checks: dict[str, dict]) -> tuple[str, str]:
    """The outcome of one ref from the runner's results.

    A file-level ref (`pytest:test_x.py`) is the worst of every result under it;
    a test ref also matches its parametrized cases (`::test_y[...]`).
    Returns (outcome, detail); outcome is pass | flaky | fail | known-red | skip | missing.
    """
    if ref in checks:
        r = checks[ref]
        return r.get("outcome", "missing"), r.get("detail", "")
    if ref.startswith("pytest:*::"):
        name = ref.removeprefix("pytest:*::")
        hits = {
            k: v
            for k, v in checks.items()
            if k.startswith("pytest:")
            and (k.endswith("::" + name) or ("::" + name + "[") in k)
        }
        if not hits:
            return "missing", f"no test named {name} in any file"
        return match_outcome(
            "pytest:hit", {f"pytest:hit::{i}": v for i, v in enumerate(hits.values())}
        )
    prefix_hits = [
        (k, v)
        for k, v in checks.items()
        if k.startswith(ref + "::")
        or k.startswith(ref + "[")
        or k.startswith(ref + ".")
    ]
    if not prefix_hits:
        return "missing", "named in the Status but the runner reported no such check"
    order = ["fail", "missing", "known-red", "killed", "skip", "flaky", "pass"]
    worst = min(
        prefix_hits, key=lambda kv: order.index(kv[1].get("outcome", "missing"))
    )
    n_bad = sum(1 for _, v in prefix_hits if v.get("outcome") not in ("pass", "flaky"))
    detail = worst[1].get("detail", "")
    if n_bad:
        detail = (
            f"{n_bad}/{len(prefix_hits)} not passing, e.g. {worst[0]}: {detail}".strip(
                ": "
            )
        )
    return worst[1].get("outcome", "missing"), detail


def killed_stages(run: dict) -> list[dict]:
    return [st for st in run.get("stages", []) if st.get("killed")]


def killed_by(ref: str, run: dict) -> dict | None:
    """The killed stage that should have run this ref: by its `covers` ref prefixes, else by its kinds."""
    for st in killed_stages(run):
        covers = st.get("covers") or [k + ":" for k in st.get("kinds", [])]
        if any(ref.startswith(c) for c in covers):
            return st
    return None


def judge_story(story: Story, manifest: Manifest, run: dict) -> dict:
    """One story's verdict. run = {"kinds": [...ran...], "checks": {ref: {outcome, detail, screenshot?}}}."""
    over = manifest.stories.get(story.id, {})
    base = {
        "id": story.id,
        "title": story.title,
        "file": story.file,
        "status_class": story.status_class,
        "checks": [],
    }
    if story.status_class != "implemented":
        return {**base, "verdict": story.status_class, "reason": story.status[:160]}
    if over.get("untestable"):
        return {**base, "verdict": "untestable", "reason": over["untestable"]}
    refs = story_checks(story, manifest)
    if not refs:
        claims = ", ".join(story.rungs) or "no rung named"
        return {
            **base,
            "verdict": "untestable",
            "reason": f"names no check the walk can run (claims: {claims})",
        }
    ran_kinds = set(run.get("kinds", [])) | {
        k for st in killed_stages(run) for k in st.get("kinds", [])
    }
    results = []
    for ref in refs:
        kind = ref.split(":", 1)[0]
        if kind not in ran_kinds:
            results.append(
                {
                    "ref": ref,
                    "outcome": "skip",
                    "detail": f"{surface_of(ref, manifest)} rung not run this walk",
                }
            )
            continue
        outcome, detail = match_outcome(ref, run.get("checks", {}))
        stage = killed_by(ref, run)
        # A stage killed at its cap never reported what it had not reached: that is not a missing check.
        # A file- or class-level ref whose finished cases passed is unproven too: its other cases never ran.
        if stage and (
            outcome == "missing"
            or (
                outcome in ("pass", "flaky", "skip")
                and ref not in run.get("checks", {})
                and not ref.startswith("pytest:*::")
            )
        ):
            was = (
                "" if outcome == "missing" else f"; the cases that finished: {outcome}"
            )
            outcome = "killed"
            detail = f"stage {stage.get('stage', '?')} killed at its {stage.get('cap_s', '?')}s cap{was}"
        row = {"ref": ref, "outcome": outcome, "detail": detail}
        shot = run.get("checks", {}).get(ref, {}).get("screenshot")
        if shot:
            row["screenshot"] = shot
        results.append(row)
    outcomes = {r["outcome"] for r in results}
    if "flaky" in outcomes:
        base["flaky"] = (
            True  # failed in the walk, passed its serial retry: counted as passing, listed
        )
        outcomes = (outcomes - {"flaky"}) | {"pass"}
    if outcomes & {"fail", "missing"}:
        verdict = "red"
    elif "killed" in outcomes:
        verdict = "killed"  # its proof never ran: the kill is the finding, not a red
    elif "known-red" in outcomes:
        verdict = "red"
        base["known"] = True
    elif outcomes == {"pass"}:
        verdict = "green"
    elif "pass" in outcomes:
        verdict = "green"  # partly walked: what ran passed; the skipped refs are listed
        base["partial"] = True
    else:
        verdict = "skipped"
    return {**base, "verdict": verdict, "checks": results}


def summarize(verdicts: list[dict]) -> dict:
    counts = {k: 0 for k in VERDICTS + OUT_OF_SCOPE}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    counts["known_red"] = sum(
        1 for v in verdicts if v["verdict"] == "red" and v.get("known")
    )
    counts["total"] = len(verdicts)
    assert sum(counts[k] for k in VERDICTS + OUT_OF_SCOPE) == len(verdicts), counts
    return counts


def diff_walks(cur: list[dict], prev: list[dict] | None) -> dict:
    """What changed since the last walk. No previous walk: every red is new."""
    p = {v["id"]: v for v in (prev or [])}
    c = {v["id"]: v for v in cur}
    new_red = [
        i
        for i, v in c.items()
        if v["verdict"] == "red"
        and not v.get("known")
        and (p.get(i, {}).get("verdict") != "red")
    ]
    still_red = [
        i
        for i, v in c.items()
        if v["verdict"] == "red" and p.get(i, {}).get("verdict") == "red"
    ]
    fixed = [
        i
        for i, v in c.items()
        if v["verdict"] == "green" and p.get(i, {}).get("verdict") == "red"
    ]
    changed = [
        {"id": i, "was": p[i]["verdict"], "now": v["verdict"]}
        for i, v in c.items()
        if i in p and p[i]["verdict"] != v["verdict"]
    ]
    return {
        "first_walk": prev is None,
        "new_red": new_red,
        "still_red": still_red,
        "fixed": fixed,
        "changed": changed,
        "added": [i for i in c if i not in p] if prev is not None else [],
        "removed": [i for i in p if i not in c],
    }


TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")


def tally_tokens(usage: list[dict]) -> dict:
    """Model spend by (purpose, model). One row per call, or a pre-summed row with `calls`."""
    groups: dict[tuple[str, str], dict] = {}
    for u in usage:
        key = (u.get("purpose", "?"), u.get("model", "?"))
        g = groups.setdefault(
            key,
            {"purpose": key[0], "model": key[1], "calls": 0}
            | {f: 0 for f in TOKEN_FIELDS},
        )
        g["calls"] += int(u.get("calls", 1))
        for f in TOKEN_FIELDS:
            g[f] += int(u.get(f, 0) or 0)
    by_purpose = list(groups.values())
    total = {"calls": sum(g["calls"] for g in by_purpose)} | {
        f: sum(g[f] for g in by_purpose) for f in TOKEN_FIELDS
    }
    return {"total": total, "by_purpose": by_purpose}


def load_session_tokens(arg: str | None) -> list[dict]:
    """--session-tokens: inline JSON or a path to it; one row or a list. Purpose defaults to `session`."""
    if not arg:
        return []
    inline = arg.lstrip().startswith(
        ("{", "[")
    )  # never stat a long JSON string as a path
    data = json.loads(arg if inline else Path(arg).read_text())
    rows = data if isinstance(data, list) else [data]
    return [{"purpose": "session"} | r for r in rows]


def dedupe_key(repo: str, story_id: str, ref: str) -> str:
    return f"walk:{repo}:{story_id}:{ref}"


def render_report(walk: dict) -> str:
    s, d = walk["summary"], walk["diff"]
    lines = [
        f"# Store walk: {walk['repo']}",
        "",
        f"Tree `{walk.get('sha', '?')}`{' (dirty)' if walk.get('dirty') else ''} · walked {walk.get('started', '?')} → {walk.get('finished', '?')} · rungs run: {', '.join(walk.get('kinds', [])) or 'none'}",
        "",
        f"**{s['green']} green · {s['red']} red ({s['known_red']} known) · {s['skipped']} skipped · {s['untestable']} untestable"
        + (f" · {s['killed']} killed" if s.get("killed") else "")
        + "** "
        f"of {s['total'] - s['planned'] - s['retired'] - s['unclassified']} implemented stories; "
        f"{s['planned']} planned, {s['retired']} retired, {s['unclassified']} unclassified not walked.",
        "",
    ]
    if d["first_walk"]:
        lines += ["First walk: no previous walk to diff against.", ""]
    else:
        lines += [
            f"Since the last walk: {len(d['new_red'])} new red, {len(d['fixed'])} fixed, {len(d['still_red'])} still red, "
            f"{len(d['added'])} stories added, {len(d['removed'])} removed.",
            "",
        ]
    killed = [st for st in walk.get("stages", []) if st.get("killed")]
    if killed:
        lines += [
            f"## Killed stages ({len(killed)})",
            "",
            "The runner killed these at their cap, so the walk exits 2. Checks they had finished keep their "
            "outcome; the rest are `killed`, not red, and file no beads.",
            "",
        ]
        for st in killed:
            lines.append(
                f"- **{st.get('stage', '?')}**: killed at {st.get('at', '?')} after {st.get('elapsed_s', '?')}s "
                f"(cap {st.get('cap_s', '?')}s)"
            )
            lines += [f"  - running: `{r}`" for r in st.get("running", [])] or [
                "  - running: not recorded"
            ]
        lines.append("")
    by_id = {v["id"]: v for v in walk["stories"]}

    def row(v: dict) -> str:
        bad = [
            c for c in v["checks"] if c["outcome"] != "pass"
        ]  # flaky refs show here too
        why = "; ".join(
            f"`{c['ref']}` {c['outcome']}"
            + (f" ({c['detail'][:120]})" if c.get("detail") else "")
            for c in bad[:3]
        )
        shots = " ".join(
            f"[shot]({c['screenshot']})" for c in v["checks"] if c.get("screenshot")
        )
        return f"- **{v['id']}** {v['title'][:90]} ({v['file']}): {why or v.get('reason', '')} {shots}".rstrip()

    for heading, ids in (
        ("New reds", d["new_red"]),
        ("Still red", d["still_red"]),
        (
            "Known red",
            [
                v["id"]
                for v in walk["stories"]
                if v["verdict"] == "red"
                and v.get("known")
                and v["id"] not in d["still_red"]
            ],
        ),
        ("Fixed since last walk", d["fixed"]),
    ):
        if ids:
            lines += [f"## {heading}", ""] + [row(by_id[i]) for i in ids] + [""]
    for verdict in ("killed", "skipped", "untestable", "unclassified"):
        vs = [v for v in walk["stories"] if v["verdict"] == verdict]
        if vs:
            lines += (
                [f"## {verdict.capitalize()} ({len(vs)})", ""]
                + [row(v) for v in vs]
                + [""]
            )
    flaky = [v for v in walk["stories"] if v.get("flaky")]
    if flaky:
        lines += (
            [f"## Flaky: red in the walk, green on a serial retry ({len(flaky)})", ""]
            + [row(v) for v in flaky]
            + [""]
        )
    greens = [v for v in walk["stories"] if v["verdict"] == "green"]
    if greens:
        lines += [f"## Green ({len(greens)})", ""]
        lines += [
            f"- {v['id']} {v['title'][:90]}"
            + (" (partly walked)" if v.get("partial") else "")
            for v in greens
        ]
        lines.append("")
    lines += render_tokens(walk.get("tokens"))
    return "\n".join(lines)


def render_tokens(tokens: dict | None) -> list[str]:
    """Always present, so a walk that spent nothing says so. No $: the skill keeps no price table."""
    lines = ["## Tokens", ""]
    if not tokens or not tokens["by_purpose"]:
        return lines + [
            "No model calls recorded (an agent session's own tokens count only when passed with --session-tokens).",
            "",
        ]

    def fmt(t: dict) -> str:
        return (
            f"{t['calls']} calls · {t['input']:,} in · {t['output']:,} out · "
            f"{t['cache_read']:,} cache read · {t['cache_write']:,} cache write"
        )

    lines.append(f"Total: {fmt(tokens['total'])}")
    lines.append("")
    lines += [
        f"- {g['purpose']} ({g['model']}): {fmt(g)}" for g in tokens["by_purpose"]
    ]
    return lines + [""]


def judge(
    stories: list[Story],
    manifest: Manifest,
    run: dict,
    prev: list[dict] | None,
    session_tokens: list[dict] | None = None,
) -> dict:
    verdicts = [judge_story(st, manifest, run) for st in stories]
    walk = {
        "repo": run.get("repo", "?"),
        "sha": run.get("sha"),
        "dirty": run.get("dirty", False),
        "started": run.get("started"),
        "finished": run.get("finished"),
        "kinds": run.get("kinds", []),
        "stages": run.get("stages", []),
        "tokens": tally_tokens(run.get("usage", []) + (session_tokens or [])),
        "summary": summarize(verdicts),
        "diff": diff_walks(verdicts, prev),
        "stories": verdicts,
    }
    return walk


# ---------------------------------------------------------------- beads


def run_bd(args: list[str], run=None) -> subprocess.CompletedProcess:
    if run is None:
        run = subprocess.run
    return run(["bd", *args], capture_output=True, text=True, check=False)


def red_beads(walk: dict) -> list[dict]:
    """One bead per (red story, failing check), known reds excluded. New and still-red alike:
    the external-ref dedupe in file_reds keeps a red that already has an open bead from being filed twice."""
    out = []
    for v in walk["stories"]:
        if v["verdict"] != "red" or v.get("known"):
            continue
        sid = v["id"]
        for c in v["checks"]:
            if c["outcome"] in ("fail", "missing"):
                out.append(
                    {
                        "key": dedupe_key(walk["repo"], sid, c["ref"]),
                        "title": f"walk: story {sid} red, {c['ref']} {c['outcome']}",
                        "description": f"Store walk at {walk.get('sha')} found story {sid} ({v['title']}, {v['file']}) red.\n"
                        f"Check: {c['ref']} → {c['outcome']}\n{c.get('detail', '')}",
                    }
                )
    return out


def file_reds(
    walk: dict, apply: bool, parent: str | None, run=None, baseline: bool = False
) -> list[str]:
    """Beads for new reds. A first walk has no baseline, so its reds are not "new" to anyone:
    it files nothing unless baseline=True, and says how many it held back."""
    if walk["diff"]["first_walk"] and not baseline:
        n = len(red_beads(walk))
        return [
            f"first walk: {n} red checks are the baseline, not filed; review report.md, then rerun with --baseline to file them"
        ]
    lines = []
    for b in red_beads(walk):
        create = [
            "create",
            b["title"],
            "--description",
            b["description"],
            "-t",
            "bug",
            "-p",
            "2",
            "--external-ref",
            b["key"],
            "--labels",
            "walk-the-store",
            "--silent",
        ]
        if parent:
            create += ["--parent", parent]
        if not apply:
            lines.append(
                "bd "
                + " ".join(
                    json.dumps(a) if " " in a or "\n" in a else a for a in create
                )
            )
            continue
        found = run_bd(["list", "--external-ref", b["key"], "--json", "-n", "0"], run)
        try:
            existing = [
                i
                for i in json.loads(found.stdout or "[]")
                if i.get("status") != "closed"
            ]
        except json.JSONDecodeError:
            existing = []
        if existing:
            lines.append(f"open already: {existing[0].get('id')} {b['key']}")
            continue
        made = run_bd(create, run)
        lines.append(
            f"filed {made.stdout.strip() or '?'} {b['key']}"
            if made.returncode == 0
            else f"FAILED {b['key']}: {made.stderr.strip()[:200]}"
        )
    return lines


# ---------------------------------------------------------------- cli


def _story_json(st: Story) -> dict:
    return asdict(st)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stories")
    s.add_argument("dir", type=Path)
    s.add_argument(
        "--implemented", action="store_true", help="only implemented stories"
    )
    r = sub.add_parser("refs")
    r.add_argument("dir", type=Path)
    r.add_argument("--manifest", type=Path)
    r.add_argument("--kind", help="only refs of this kind")
    j = sub.add_parser("judge")
    j.add_argument("--stories", type=Path, required=True)
    j.add_argument("--checks", type=Path, required=True)
    j.add_argument("--out", type=Path, required=True)
    j.add_argument("--manifest", type=Path)
    j.add_argument("--previous", type=Path)
    j.add_argument(
        "--session-tokens",
        help="the driving agent session's own spend: JSON row(s) {purpose, model, calls, input, output, "
        "cache_read, cache_write}, inline or a file",
    )
    f = sub.add_parser("file-reds")
    f.add_argument("walk", type=Path)
    f.add_argument("--apply", action="store_true")
    f.add_argument("--parent")
    f.add_argument(
        "--baseline", action="store_true", help="file a first walk's reds too"
    )
    a = p.parse_args(argv)

    if a.cmd == "stories":
        sts = parse_dir(a.dir)
        if a.implemented:
            sts = [x for x in sts if x.status_class == "implemented"]
        print(json.dumps([_story_json(x) for x in sts], indent=1))
        return 0
    if a.cmd == "refs":
        m = load_manifest(a.manifest)
        seen: list[str] = []
        for st in parse_dir(a.dir):
            if st.status_class != "implemented" or m.stories.get(st.id, {}).get(
                "untestable"
            ):
                continue
            for ref in story_checks(st, m):
                if ref not in seen and (not a.kind or ref.startswith(a.kind + ":")):
                    seen.append(ref)
        print("\n".join(seen))
        return 0
    if a.cmd == "judge":
        run = json.loads(a.checks.read_text())
        prev = None
        if a.previous and a.previous.exists():
            prev = json.loads(a.previous.read_text())["stories"]
        walk = judge(
            parse_dir(a.stories),
            load_manifest(a.manifest),
            run,
            prev,
            load_session_tokens(a.session_tokens),
        )
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "walk.json").write_text(json.dumps(walk, indent=1))
        (a.out / "report.md").write_text(render_report(walk))
        s = walk["summary"]
        print(
            f"{s['green']} green · {s['red']} red ({s['known_red']} known) · {s['skipped']} skipped · "
            f"{s['untestable']} untestable · {s['killed']} killed · {len(walk['diff']['new_red'])} new red "
            f"→ {a.out / 'report.md'}"
        )
        if killed_stages(run):
            return 2  # a stage hit its cap: the walk did not finish, whatever else it found
        return 1 if walk["diff"]["new_red"] else 0
    if a.cmd == "file-reds":
        walk = json.loads(a.walk.read_text())
        for line in file_reds(walk, a.apply, a.parent, baseline=a.baseline):
            print(line)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
