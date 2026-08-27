#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Machine pre-pass for adversarial-review.

Every finding a regex can catch must never cost an LLM pass. This runs before
pass 1 and again before the final pass, and is stdlib-only so `unittest` can
import it under system python without `uv`.

Subcommands
  leaks    secrets / private identifiers in the target
  numbers  numeric literals in the target that do NOT appear in a source file
  links    markdown links: dead local files, dead in-document anchors
  rubric   banned phrases pulled out of a rubric file (see parse_rubric)
  frozen   facts-frozen contract: numbers/tables/front-matter/code identical,
           word count strictly down (for style rewrites)
  all      leaks + links, plus numbers/rubric/frozen when their flags are given

Exit codes: 0 clean, 1 findings, 2 usage/IO error.
`--strict` promotes warnings to failures.
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ERROR = "error"
WARN = "warn"

FENCE = re.compile(r"^\s*(```|~~~)")

# --------------------------------------------------------------------------
# leaks
# --------------------------------------------------------------------------

# (name, severity, pattern). Ordered most-specific first so a token match wins
# over a generic assignment match on the same span.
LEAK_PATTERNS: list[tuple[str, str, str]] = [
    ("private-key-block", ERROR, r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("aws-access-key", ERROR, r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github-token", ERROR, r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("slack-token", ERROR, r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("anthropic-key", ERROR, r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    ("openai-key", ERROR, r"\bsk-[A-Za-z0-9]{32,}\b"),
    ("jwt", ERROR, r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    (
        "secret-assignment",
        ERROR,
        (
            r"(?i)\b(?:api[_-]?key|secret|token|passwd|password|bearer)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+]{16,}"
        ),
    ),
    # Tailscale CGNAT 100.64.0.0/10
    (
        "cgnat-ip",
        ERROR,
        r"\b100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}\b",
    ),
    (
        "rfc1918-ip",
        ERROR,
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}\b",
    ),
    ("tailnet-host", ERROR, r"\b[A-Za-z0-9][A-Za-z0-9-]*\.ts\.net\b"),
    (
        "session-uuid",
        WARN,
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    ),
    ("home-path", WARN, r"/(?:home|Users)/[A-Za-z0-9._-]+"),
    ("email", WARN, r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]

# Text that looks like a leak but is the redaction itself.
PLACEHOLDER = re.compile(r"^<[^>]+>$|^(?:REDACTED|EXAMPLE|xxx+|\.\.\.)$", re.IGNORECASE)


def scan_leaks(text: str, allow: list[str] | None = None) -> list[dict]:
    """Regex sweep for secrets and private identifiers.

    `allow` is a list of regexes; a match whose text matches any of them is
    dropped (for documented examples the artifact intentionally contains).
    """
    allow_res = [re.compile(a) for a in (allow or [])]
    findings: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        claimed: list[tuple[int, int]] = []
        for name, severity, pattern in LEAK_PATTERNS:
            for m in re.finditer(pattern, line):
                if any(m.start() < end and start < m.end() for start, end in claimed):
                    continue  # already reported by a more specific pattern
                hit = m.group(0)
                if PLACEHOLDER.match(hit) or any(a.search(hit) for a in allow_res):
                    continue
                claimed.append((m.start(), m.end()))
                findings.append(
                    {
                        "check": "leaks",
                        "rule": name,
                        "severity": severity,
                        "line": lineno,
                        "text": hit,
                        "detail": f"{name} in: {line.strip()[:120]}",
                    }
                )
    return findings


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

ORDERED_LIST = re.compile(r"^\s*\d+[.)]\s")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numbers(text: str, min_digits: int = 2) -> list[tuple[int, str]]:
    """Numeric literals with their line numbers, commas normalised away.

    Ordered-list markers are stripped first (`1.` is structure, not a fact).
    Numbers with fewer than `min_digits` significant digits are skipped —
    single digits are list counts and prose noise, not checkable figures.
    """
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = ORDERED_LIST.sub("", line)
        for m in NUMBER.finditer(stripped):
            raw = m.group(0).replace(",", "")
            if len(raw.replace(".", "").lstrip("0")) < min_digits:
                continue
            out.append((lineno, raw))
    return out


def unsourced_numbers(target: str, source: str, min_digits: int = 2) -> list[dict]:
    """Numbers asserted by the target that never appear in the source file."""
    source_nums = {n for _, n in extract_numbers(source, min_digits=1)}
    findings = []
    for lineno, num in extract_numbers(target, min_digits=min_digits):
        if num in source_nums:
            continue
        findings.append(
            {
                "check": "numbers",
                "rule": "unsourced-number",
                "severity": WARN,
                "line": lineno,
                "text": num,
                "detail": f"{num} does not appear in the source file",
            }
        )
    return findings


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------

MD_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*$")


def slugify(title: str) -> str:
    """GitHub-flavoured heading slug.

    Each whitespace character becomes its own hyphen — GitHub does NOT collapse
    runs, so `A : B` slugs to `a--b`. Collapsing them produced false
    dead-anchor reports on every heading containing a stripped punctuation mark.

    A heading may contain markdown links; GitHub slugs the *rendered* text, so
    `## Be [Curious](/grandmother)` is `#be-curious`, not `#be-curiousgrandmother`.
    """
    title = unicodedata.normalize("NFKD", title)
    title = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", title)  # [text](url) -> text
    title = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", title)  # [text][ref]  -> text
    title = re.sub(r"[`*_~]", "", title).strip().lower()
    title = re.sub(r"[^\w\s-]", "", title)
    return re.sub(r"\s", "-", title)


def heading_anchors(text: str) -> set[str]:
    """Every anchor the document exposes, with GitHub duplicate suffixes.

    A repeated heading does not collide: the second `## Conclusion` is
    `#conclusion-1`, the third `#conclusion-2`. Without the suffixes a doc with
    31 "Conclusion" headings reports 30 false dead anchors.
    """
    anchors: set[str] = set()
    counts: Counter[str] = Counter()
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not (m := HEADING.match(line)):
            continue
        base = slugify(m.group("title"))
        n = counts[base]
        counts[base] += 1
        anchors.add(base if n == 0 else f"{base}-{n}")
    return anchors


def check_links(text: str, base_dir: Path) -> list[dict]:
    """Dead relative-path links and dead in-document anchors. No network.

    Site-absolute URLs (`/permalink`) are skipped — on a Jekyll-style site they
    resolve against a site root this tool cannot know, and treating them as
    paths relative to the document produced a false positive on every internal
    link. Only genuinely relative paths are resolved against `base_dir`.
    """
    anchors = heading_anchors(text)
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in MD_LINK.finditer(line):
            url = m.group("url")
            if url.startswith("#"):
                if slugify(url[1:]) not in anchors:
                    findings.append(
                        {
                            "check": "links",
                            "rule": "dead-anchor",
                            "severity": ERROR,
                            "line": lineno,
                            "text": url,
                            "detail": f"no heading in this document slugs to {url}",
                        }
                    )
                continue
            if re.match(r"^[a-z][a-z0-9+.-]*:", url) or url.startswith("//"):
                continue  # http(s)/mailto/etc — out of scope, no network here
            if url.startswith("/"):
                continue  # site-absolute permalink; the site root is unknown here
            bare = url.split("#", 1)[0]
            if not Path(bare).suffix and not bare.startswith((".", "..")):
                continue  # extension-less route (`td/data-systems`), not a file
            path = (base_dir / bare).resolve()
            if not path.exists():
                findings.append(
                    {
                        "check": "links",
                        "rule": "dead-local-link",
                        "severity": ERROR,
                        "line": lineno,
                        "text": url,
                        "detail": f"{path} does not exist",
                    }
                )
    return findings


# --------------------------------------------------------------------------
# rubric
# --------------------------------------------------------------------------

QUOTED = re.compile(r"[\"“]([^\"”]+)[\"”]")


def parse_rubric(text: str, min_phrase_len: int = 4) -> list[str]:
    """Banned phrases from a rubric file.

    Convention (any rubric can opt in by following it): a line containing ❌
    opens a banned block; a line containing ✅ or a new markdown heading closes
    it. Inside the block, every double-quoted string on a **bullet** is a banned
    phrase. Bullets beginning `Instead` are the remedy, not the ban, and are
    skipped. Fenced code is skipped entirely — quoted strings in a "wrong
    structure" example illustrate layout, not phrasing. Phrases shorter than
    `min_phrase_len` are dropped — they are pronouns like "I"/"we" used
    illustratively, not literal strings to grep.
    """
    phrases: list[str] = []
    seen: set[str] = set()
    in_block = False
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if HEADING.match(line) or "✅" in line:
            in_block = "❌" in line
            continue
        if "❌" in line:
            in_block = True
        elif not in_block:
            continue
        if not re.match(r"^\s*[-*]\s", line):
            continue
        if re.match(r"^\s*[-*]\s*(\*\*)?Instead\b", line):
            continue
        for phrase in QUOTED.findall(line):
            phrase = phrase.strip()
            if len(phrase) < min_phrase_len or phrase.lower() in seen:
                continue
            seen.add(phrase.lower())
            phrases.append(phrase)
    return phrases


def scan_rubric(text: str, phrases: list[str]) -> list[dict]:
    """Case-insensitive, word-bounded hits for each banned phrase."""
    findings = []
    for phrase in phrases:
        pattern = re.escape(phrase)
        if phrase[:1].isalnum():
            pattern = r"\b" + pattern
        if phrase[-1:].isalnum():
            pattern = pattern + r"\b"
        rx = re.compile(pattern, re.IGNORECASE)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in rx.finditer(line):
                findings.append(
                    {
                        "check": "rubric",
                        "rule": "banned-phrase",
                        "severity": WARN,
                        "line": lineno,
                        "text": m.group(0),
                        "detail": f'rubric bans "{phrase}" — {line.strip()[:120]}',
                    }
                )
    return findings


# --------------------------------------------------------------------------
# frozen (facts-frozen rewrite contract)
# --------------------------------------------------------------------------


def front_matter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[: i + 1])
    return ""


def table_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.lstrip().startswith("|")]


def code_blocks(text: str) -> list[str]:
    blocks, current, inside = [], [], False
    for line in text.splitlines():
        if FENCE.match(line):
            if inside:
                blocks.append("\n".join(current))
                current, inside = [], False
            else:
                inside = True
            continue
        if inside:
            current.append(line)
    if inside:
        blocks.append("\n".join(current))
    return blocks


def word_count(text: str) -> int:
    return len(text.split())


def check_frozen(baseline: str, target: str) -> list[dict]:
    """The style-rewrite contract: prose may change, facts may not."""
    findings = []

    def flag(rule, detail, severity=ERROR):
        findings.append(
            {
                "check": "frozen",
                "rule": rule,
                "severity": severity,
                "line": 0,
                "text": "",
                "detail": detail,
            }
        )

    if front_matter(baseline) != front_matter(target):
        flag(
            "front-matter-changed", "front matter is not byte-identical to the baseline"
        )
    if table_lines(baseline) != table_lines(target):
        flag("table-changed", "table rows are not byte-identical to the baseline")
    if code_blocks(baseline) != code_blocks(target):
        flag(
            "code-changed", "fenced code blocks are not byte-identical to the baseline"
        )

    before = Counter(n for _, n in extract_numbers(baseline, min_digits=1))
    after = Counter(n for _, n in extract_numbers(target, min_digits=1))
    for num, count in (before - after).items():
        flag(
            "number-dropped", f"{num} appears {count}x fewer times than in the baseline"
        )
    for num, count in (after - before).items():
        flag("number-added", f"{num} appears {count}x more times than in the baseline")

    wb, wt = word_count(baseline), word_count(target)
    if wt >= wb:
        flag(
            "word-count-not-reduced",
            f"word count went {wb} -> {wt}; a de-slop rewrite must go DOWN",
        )
    else:
        findings.append(
            {
                "check": "frozen",
                "rule": "word-count",
                "severity": "info",
                "line": 0,
                "text": f"{wb} -> {wt}",
                "detail": f"word count {wb} -> {wt} ({100 * (wb - wt) // max(wb, 1)}% cut)",
            }
        )
    return findings


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def read(path_str: str) -> str:
    try:
        return Path(path_str).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"prepass: cannot read {path_str}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def emit(findings: list[dict], as_json: bool, strict: bool) -> int:
    real = [f for f in findings if f["severity"] != "info"]
    if as_json:
        print(json.dumps({"findings": findings, "count": len(real)}, indent=2))
    else:
        for f in findings:
            loc = f"L{f['line']}" if f["line"] else "-"
            print(
                f"[{f['severity'].upper():5}] {f['check']}/{f['rule']} {loc}: {f['detail']}"
            )
        blocking = [f for f in real if f["severity"] == ERROR or strict]
        print(
            f"\nprepass: {len(real)} finding(s), {len(blocking)} blocking"
            f"{' (strict)' if strict else ''}"
        )
    if any(f["severity"] == ERROR for f in real) or (strict and real):
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    # `--json` / `--strict` are accepted on BOTH sides of the subcommand.
    # argparse only honours parent-parser flags *before* the subcommand, and
    # `prepass all FILE --json` is the shape a caller reaches for first —
    # it used to die with "unrecognized arguments" and an empty stdout.
    # SUPPRESS defaults are required: without them the subparser would write
    # its own False back over a value set before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit findings as JSON",
    )
    common.add_argument(
        "--strict",
        action="store_true",
        default=argparse.SUPPRESS,
        help="warnings are failures too",
    )

    # NB: never call p.set_defaults() for these — `parents=` shares the same
    # action objects with every subparser, so set_defaults would rewrite their
    # default from SUPPRESS to False and the subparser would clobber a flag
    # given before the subcommand. Read them with getattr() instead.
    p = argparse.ArgumentParser(
        prog="prepass", description=__doc__.splitlines()[0], parents=[common]
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def target_arg(sp):
        sp.add_argument("target", help="file under review")
        return sp

    leaks = target_arg(
        sub.add_parser("leaks", parents=[common], help="secrets / private identifiers")
    )
    leaks.add_argument(
        "--allow",
        action="append",
        default=[],
        help="regex of allowed match (repeatable)",
    )

    nums = target_arg(
        sub.add_parser(
            "numbers", parents=[common], help="numbers absent from a source file"
        )
    )
    nums.add_argument("--source", required=True, help="file the numbers must come from")
    nums.add_argument("--min-digits", type=int, default=2)

    target_arg(
        sub.add_parser("links", parents=[common], help="dead local links and anchors")
    )

    rub = target_arg(
        sub.add_parser(
            "rubric", parents=[common], help="banned phrases from a rubric file"
        )
    )
    rub.add_argument("--rubric", required=True, help="rubric markdown file (❌ blocks)")
    rub.add_argument("--min-phrase-len", type=int, default=4)

    frz = target_arg(
        sub.add_parser("frozen", parents=[common], help="facts-frozen rewrite contract")
    )
    frz.add_argument(
        "--baseline", required=True, help="pre-rewrite version of the file"
    )

    all_ = target_arg(
        sub.add_parser(
            "all", parents=[common], help="leaks + links, plus any flag you pass"
        )
    )
    all_.add_argument("--allow", action="append", default=[])
    all_.add_argument("--source")
    all_.add_argument("--rubric")
    all_.add_argument("--baseline")
    all_.add_argument("--min-digits", type=int, default=2)
    all_.add_argument("--min-phrase-len", type=int, default=4)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = read(args.target)
    base_dir = Path(args.target).resolve().parent
    findings: list[dict] = []

    if args.cmd in ("leaks", "all"):
        findings += scan_leaks(target, allow=getattr(args, "allow", []))
    if args.cmd in ("links", "all"):
        findings += check_links(target, base_dir)
    if args.cmd == "numbers" or (args.cmd == "all" and args.source):
        findings += unsourced_numbers(
            target, read(args.source), min_digits=args.min_digits
        )
    if args.cmd == "rubric" or (args.cmd == "all" and args.rubric):
        phrases = parse_rubric(read(args.rubric), min_phrase_len=args.min_phrase_len)
        if not phrases:
            print(
                "prepass: rubric yielded 0 phrases — check its ❌ block format",
                file=sys.stderr,
            )
        findings += scan_rubric(target, phrases)
    if args.cmd == "frozen" or (args.cmd == "all" and args.baseline):
        findings += check_frozen(read(args.baseline), target)

    return emit(
        findings,
        as_json=getattr(args, "json", False),
        strict=getattr(args, "strict", False),
    )


if __name__ == "__main__":
    raise SystemExit(main())
