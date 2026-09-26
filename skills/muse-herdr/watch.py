#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Watch the Herdr agents a manager drives; print one line whenever one needs the manager.

    watch.py <name> [name …]   those agents; exits once all of them are gone
    watch.py                   every named agent of --kind (default muse), including new ones

    BLOCKED <name>: …   waits on an approval or a question (the last screen lines follow)
    FAILED <name>: …    its model call failed; Herdr still says "working", so re-prompt it
    STALLED <name>: …   "working", but the screen has not moved for --stall-secs
    <name> idle|done    it settled
    <name> gone         Herdr says the agent does not exist
    WATCHER …           the watcher cannot see (a herdr call failed); silence never means blind

Run it under a Monitor. Nothing here types keys: the manager reads the prompt and answers with
`herdr agent send-keys <name> enter` (an auto-approver once typed a stray `y` into Muse's input).
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

# Herdr says blocked while Muse is still thinking or running a command; only a real dialog counts.
BUSY_RE = re.compile(r"Thinking \(|esc to interrupt|ctrl\+b to send|last event [0-9]")
FAILED_RE = re.compile(r"model failed|transport error|rate limit|Interrupted ·")
NOISE_RE = re.compile(r"^\s*$|Voice input|^──")
PROMPT_NOISE_RE = re.compile(
    r"^\s*❯\s*$|muse-spark"
)  # the input line and the status bar


def parse_status(out: str) -> str | None:
    """`herdr agent get` JSON -> agent_status, "gone" for agent_not_found, None when unreadable."""
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return None
    if doc.get("error", {}).get("code") == "agent_not_found":
        return "gone"
    return doc.get("result", {}).get("agent", {}).get("agent_status")


def parse_names(out: str, kind: str) -> list[str] | None:
    """`herdr agent list` JSON -> names of agents of that kind, None when unreadable."""
    try:
        agents = json.loads(out)["result"]["agents"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return [a["name"] for a in agents if a.get("agent") == kind and a.get("name")]


def screen_lines(screen: str) -> list[str]:
    return [line for line in screen.splitlines() if not NOISE_RE.search(line)]


@dataclass
class Seen:
    """What the watcher remembers about one agent between polls."""

    status: str | None = None
    reported: str = ""  # the last event printed; cleared when the status changes
    screen: str = ""
    screen_since: float = 0.0


def classify(
    name: str, status: str, lines: list[str], seen: Seen, now: float, stall_secs: float
) -> str | None:
    """Update `seen` for this poll and return the line to print, if any."""
    tail = " ".join(lines[-8:])
    if tail != seen.screen:
        seen.screen, seen.screen_since = tail, now
    if status != seen.status:
        seen.status, seen.reported = status, ""

    event = None
    if status == "blocked":
        # A long think scrolls its "Thinking (" marker up, so look wider than what gets printed.
        if not BUSY_RE.search(" ".join(lines[-14:])):
            event = ("blocked:" + tail, f"BLOCKED {name}: {tail[:400]}")
    elif status == "working":
        # A failure counts only when it is the newest thing on screen: a re-prompt makes the turn live again.
        newest = [line for line in lines if not PROMPT_NOISE_RE.search(line)][-3:]
        if FAILED_RE.search(" ".join(newest)):
            event = ("failed:" + tail, f"FAILED {name} (re-prompt it): {tail[:300]}")
        elif now - seen.screen_since >= stall_secs:
            event = (
                "stalled:" + tail,
                f"STALLED {name} (no screen change for {stall_secs:.0f}s): {tail[:300]}",
            )
    elif status in ("idle", "done", "gone"):
        event = (status, f"{name} {status}")
    elif status is not None:
        event = ("unknown:" + status, f"WATCHER {name}: unknown status {status!r}")

    if event is None or event[0] == seen.reported:
        return None
    seen.reported = event[0]
    return event[1]


def herdr(*args: str) -> str:
    # On a failure herdr exits 1 and prints its JSON error (agent_not_found) to stderr; parse_status needs it.
    try:
        done = subprocess.run(
            ["herdr", *args], capture_output=True, text=True, timeout=30
        )
        return done.stdout if done.returncode == 0 else done.stderr
    except subprocess.TimeoutExpired:
        return ""  # unparsable, so the caller reports the watcher as blind


def watch(names: list[str], kind: str, stall_secs: float, interval: float) -> None:
    seen: dict[str, Seen] = {}
    blind: set[str] = set()

    def say(line: str) -> None:
        print(line, flush=True)

    while True:
        agents = list(names) or parse_names(herdr("agent", "list"), kind)
        if agents is None:
            if "list" not in blind:
                say(
                    f"WATCHER cannot read `herdr agent list`; retrying every {interval:.0f}s"
                )
                blind.add("list")
            agents = []
        else:
            blind.discard("list")
        # An agent that dropped out of the list still gets one more look, so its exit is reported.
        agents += [n for n, s in seen.items() if s.status != "gone" and n not in agents]

        for name in agents:
            status = parse_status(herdr("agent", "get", name))
            if status is None:
                if name not in blind:
                    say(f"WATCHER {name}: cannot read `herdr agent get`; retrying")
                    blind.add(name)
                continue
            if name in blind:
                blind.discard(name)
                say(f"WATCHER {name}: readable again")
            screen = (
                ""
                if status == "gone"
                else herdr(
                    "agent", "read", name, "--source", "visible", "--lines", "40"
                )
            )
            line = classify(
                name,
                status,
                screen_lines(screen),
                seen.setdefault(name, Seen()),
                time.time(),
                stall_secs,
            )
            if line:
                say(line)

        if names and all(seen.get(n, Seen()).status == "gone" for n in names):
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "names",
        nargs="*",
        help="agents to watch (default: every named agent of --kind)",
    )
    parser.add_argument(
        "--kind", default="muse", help="agent kind to discover when no names are given"
    )
    parser.add_argument(
        "--stall-secs",
        type=float,
        default=300,
        help="report a working agent whose screen froze",
    )
    parser.add_argument(
        "--interval", type=float, default=15, help="seconds between polls"
    )
    args = parser.parse_args()
    if not shutil.which("herdr"):
        sys.exit("watch.py: herdr is not on PATH")
    try:
        watch(args.names, args.kind, args.stall_secs, args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
