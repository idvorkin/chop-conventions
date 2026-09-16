#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pyyaml>=6",
# ]
# ///
"""Check the external tools in dev-setup/external-tools.yaml.

    ./dev-setup/tools/tool_doctor.py                # check every entry
    ./dev-setup/tools/tool_doctor.py --manifest P   # ...a manifest elsewhere

One line per entry: ✅ installed, ⚠️ below min_version, ❌ missing (with the
install command). Exits 1 if any `adopted` entry is missing, 0 otherwise — a
missing `trial` or `proposed` tool is information, not a failure.

`yaml` is imported lazily so tests can import the pure functions under system
Python without the PEP 723 deps. See CLAUDE.md "Scripting Language Defaults".
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "external-tools.yaml"
CHECK_TIMEOUT_S = 20
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")

OK, WARN, MISSING = "ok", "warn", "missing"
SYMBOL = {OK: "✅", WARN: "⚠️", MISSING: "❌"}


@dataclass(frozen=True)
class CheckResult:
    """What running an entry's `check` command told us."""

    passed: bool
    output: str = ""


@dataclass(frozen=True)
class Verdict:
    state: str
    version: str | None
    detail: str


def parse_version(text: str) -> str | None:
    """First dotted number in command output, or None."""
    match = VERSION_RE.search(text or "")
    return match.group(0) if match else None


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_below(found: str | None, minimum: str | None) -> bool:
    """True only when both versions are known and `found` is older."""
    if not found or not minimum:
        return False
    a, b = version_key(found), version_key(minimum)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a < b


def classify(entry: dict, result: CheckResult) -> Verdict:
    if not result.passed:
        return Verdict(MISSING, None, f"install: {entry['install']}")
    version = parse_version(result.output)
    minimum = entry.get("min_version")
    if is_below(version, minimum):
        return Verdict(WARN, version, f"below min_version {minimum}")
    return Verdict(OK, version, "")


def format_line(entry: dict, verdict: Verdict) -> str:
    cells = [
        SYMBOL[verdict.state],
        f"{entry['name']:<14}",
        f"{verdict.version or '':<8}",
        f"{entry['kind']:<11}",
        f"{entry['status']:<9}",
    ]
    line = " ".join(cells)
    if verdict.detail:
        line += f"  {verdict.detail}"
    return line.rstrip()


def exit_code(verdicts: list[tuple[dict, Verdict]]) -> int:
    """Nonzero when an adopted tool is missing; warnings alone are not fatal."""
    broken = any(
        entry.get("status") == "adopted" and verdict.state == MISSING
        for entry, verdict in verdicts
    )
    return 1 if broken else 0


def summary(verdicts: list[tuple[dict, Verdict]]) -> str:
    counts = {OK: 0, WARN: 0, MISSING: 0}
    for _, verdict in verdicts:
        counts[verdict.state] += 1
    return f"{counts[OK]} installed, {counts[WARN]} outdated, {counts[MISSING]} missing"


def run_check(command: str, run=None) -> CheckResult:
    """Subprocess boundary. Shell is used so `$HOME` and pipes in the manifest work."""
    if run is None:
        run = subprocess.run
    try:
        proc = run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return CheckResult(False, str(exc))
    return CheckResult(proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}")


def load_manifest(path: Path) -> list[dict]:
    import yaml  # lazy: see module docstring

    data = yaml.safe_load(path.read_text()) or {}
    return data.get("tools", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)

    verdicts = [
        (entry, classify(entry, run_check(entry["check"])))
        for entry in load_manifest(args.manifest)
    ]
    for entry, verdict in verdicts:
        print(format_line(entry, verdict))
    print(f"\n{summary(verdicts)} — see {args.manifest.name}")
    return exit_code(verdicts)


if __name__ == "__main__":
    sys.exit(main())
