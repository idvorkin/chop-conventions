#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Watch the Herdr agents a manager drives, and say when one needs them.

One line per event, on stdout, flushed as it happens — arm it under a Monitor:

    ./watch.py <name>        # one agent (the single-Muse workflow)
    ./watch.py a b c         # those agents
    ./watch.py               # every live agent of --kind (default: muse)

    BLOCKED <name>: …   waits on an approval or a question (last screen lines follow)
    FAILED  <name>: …   its model call failed; Herdr still reports "working"
    STALLED <name>: …   "working", but the screen has not moved for --stall-secs
    <name> idle|done    it settled
    <name> gone         Herdr answered `agent_not_found` — the agent is really gone
    WATCHER …           the watcher itself cannot see (herdr call failed, unknown
                        status, empty agent list). Never silent about being blind.

Nothing here types keys: the manager reads the prompt and answers with
`herdr agent send-keys <name> enter` (or what the prompt wants), or re-prompts a
FAILED agent. An auto-approver once typed a stray `y` into Muse's input.

The classification is pure (`parse_agent_get`, `parse_agent_list`, `step`) and
unit-tested in test_watch.py; only `Watcher` shells out to `herdr`.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace

# Herdr reports `blocked` while Muse is still thinking or running a command;
# only a real dialog counts as an approval prompt.
THINKING_RE = re.compile(
    r"Thinking \(|esc to interrupt|ctrl\+b to send|last event [0-9]"
)
# A failed model call that Herdr still rolls up as "working".
FAILURE_RE = re.compile(r"model failed|transport error|rate limit|Interrupted ·")
# Screen chrome that is not output.
NOISE_RE = re.compile(r"^\s*$|Voice input|^──")
# Extra chrome to drop when asking "is a failure the newest thing on screen?".
TAIL_NOISE_RE = re.compile(r"^\s*❯\s*$|muse-spark")

SETTLED_STATUSES = ("idle", "done")
KNOWN_STATUSES = ("blocked", "working", *SETTLED_STATUSES)

SCREEN_LINES = 8  # what an event quotes
WIDE_LINES = 14  # what the thinking filter looks at
TAIL_LINES = 3  # what the failure filter looks at


# --------------------------------------------------------------------------
# Pure parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """What one `herdr agent get` told us — status, absence, or blindness."""

    status: str | None = None
    gone: bool = False
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.status is not None or self.gone


def parse_agent_get(returncode: int, stdout: str, detail: str | None = None) -> Probe:
    """Classify one `herdr agent get` response.

    Herdr's API-backed commands print a single-line envelope and exit 1 on
    error: `{"result": {"agent": {"agent_status": …}}}` or
    `{"error": {"code": "agent_not_found", …}}`. Only that code means gone —
    an empty stdout, unparsable JSON, or a result without `agent_status` means
    the watcher is blind, NOT that the agent disappeared. The old shell version
    collapsed all of those into `// "gone"` (false "gone", or a silent spin).
    """
    text = (stdout or "").strip()
    if not text:
        why = detail or f"exit {returncode}"
        return Probe(error=f"no output from `herdr agent get` ({why})")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return Probe(
            error=f"unparsable `herdr agent get` output: {truncate(text, 120)}"
        )
    if not isinstance(payload, dict):
        return Probe(
            error=f"unexpected `herdr agent get` payload: {truncate(text, 120)}"
        )

    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code") or "unknown"
        if code == "agent_not_found":
            return Probe(gone=True)
        return Probe(
            error=f"`herdr agent get` error {code}: {error.get('message') or ''}".strip()
        )

    result = payload.get("result")
    status = (
        result.get("agent", {}).get("agent_status")
        if isinstance(result, dict)
        else None
    )
    if isinstance(status, str) and status:
        return Probe(status=status)
    return Probe(
        error="`herdr agent get` returned no .result.agent.agent_status (schema change?)"
    )


@dataclass(frozen=True)
class AgentList:
    """Names of `kind` in `herdr agent list`, plus what else was there."""

    names: tuple[str, ...] = ()
    total: int = 0
    error: str | None = None


def parse_agent_list(
    returncode: int, stdout: str, kind: str, detail: str | None = None
) -> AgentList:
    """Names of live agents of `kind`. `total` distinguishes "no agents at all"
    from "agents present, none matched the kind filter" — the filter keys off
    `.agent`, and a schema change there would otherwise go silent."""
    text = (stdout or "").strip()
    if not text:
        why = detail or f"exit {returncode}"
        return AgentList(error=f"no output from `herdr agent list` ({why})")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return AgentList(
            error=f"unparsable `herdr agent list` output: {truncate(text, 120)}"
        )

    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return AgentList(
            error=f"`herdr agent list` error {error.get('code') or 'unknown'}"
        )

    result = payload.get("result") if isinstance(payload, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return AgentList(
            error="`herdr agent list` returned no .result.agents (schema change?)"
        )

    # A pane that was never `agent start <name>`-ed has no name; its pane id is
    # an equally valid agent target, so watch it rather than skip it silently.
    names = tuple(
        target
        for a in agents
        if isinstance(a, dict) and a.get("agent") == kind
        for target in [_first_str(a.get("name"), a.get("pane_id"))]
        if target
    )
    return AgentList(names=names, total=len(agents))


def _first_str(*candidates: object) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def filter_lines(raw: str, *, extra_noise: re.Pattern[str] | None = None) -> list[str]:
    """Screen text minus blank lines and chrome."""
    kept = []
    for line in (raw or "").splitlines():
        if NOISE_RE.search(line):
            continue
        if extra_noise is not None and extra_noise.search(line):
            continue
        kept.append(line.rstrip())
    return kept


def tail_text(lines: list[str], count: int) -> str:
    return " ".join(lines[-count:]) if lines else ""


def truncate(text: str, limit: int) -> str:
    """Character-oriented truncation — bash's `${s:0:400}` sliced bytes and
    could cut a multi-byte character in half."""
    return text if len(text) <= limit else text[:limit]


def looks_like_thinking(wide: str) -> bool:
    return bool(THINKING_RE.search(wide))


def looks_like_failure(tail: str) -> bool:
    return bool(FAILURE_RE.search(tail))


# --------------------------------------------------------------------------
# Pure state machine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentState:
    status: str | None = None
    screen: str | None = None
    same_since: float | None = None
    last_emit: str | None = None  # dedup key for the last line emitted
    fail_streak: int = 0
    blind: bool = False  # we have already said we cannot see this agent


@dataclass(frozen=True)
class Event:
    kind: str
    agent: str
    text: str

    def line(self) -> str:
        if self.kind in ("SETTLED", "GONE"):
            return f"{self.agent} {self.text}"
        if self.kind == "WATCHER":
            return (
                f"WATCHER {self.agent}: {self.text}"
                if self.agent
                else f"WATCHER: {self.text}"
            )
        return f"{self.kind} {self.agent}: {self.text}"


@dataclass(frozen=True)
class Screens:
    """One `herdr agent read` per agent per tick, sliced three ways."""

    screen: str = ""
    wide: str = ""
    tail: str = ""

    @classmethod
    def from_raw(cls, raw: str) -> "Screens":
        lines = filter_lines(raw)
        return cls(
            screen=tail_text(lines, SCREEN_LINES),
            wide=tail_text(lines, WIDE_LINES),
            tail=tail_text(filter_lines(raw, extra_noise=TAIL_NOISE_RE), TAIL_LINES),
        )


def step(
    name: str,
    state: AgentState,
    probe: Probe,
    screens: Screens,
    now: float,
    *,
    stall_secs: float,
    blind_after: int,
) -> tuple[AgentState, list[Event]]:
    """Fold one poll into the agent's state. Pure: no I/O, no clock."""
    if not probe.known:
        streak = state.fail_streak + 1
        # Say it once, at the threshold, then stay quiet until it recovers —
        # a watcher that cannot see must not look the same as a quiet agent.
        if streak >= blind_after and not state.blind:
            text = f"cannot read this agent ({streak}× in a row): {probe.error}"
            return replace(state, fail_streak=streak, blind=True), [
                Event("WATCHER", name, text)
            ]
        return replace(state, fail_streak=streak), []

    events: list[Event] = []
    if state.blind:
        events.append(Event("WATCHER", name, "readable again"))
    state = replace(state, fail_streak=0, blind=False)

    if probe.gone:
        if state.last_emit == "gone":
            return state, events
        return replace(state, status="gone", last_emit="gone"), [
            *events,
            Event("GONE", name, "gone"),
        ]

    status = probe.status or ""
    # A status transition clears the dedup key: block → approve → block again
    # with a screen that renders identically must still be reported. The shell
    # version only reset on idle/done/gone and swallowed the second prompt.
    if status != state.status:
        state = replace(state, status=status, last_emit=None)

    if screens.screen != state.screen:
        state = replace(state, screen=screens.screen, same_since=now)
    elif state.same_since is None:
        state = replace(state, same_since=now)

    def emit(kind: str, key: str, text: str) -> tuple[AgentState, list[Event]]:
        if state.last_emit == key:
            return state, events
        return replace(state, last_emit=key), [*events, Event(kind, name, text)]

    if status == "blocked":
        if looks_like_thinking(screens.wide):
            return state, events
        return emit(
            "BLOCKED", f"blocked:{screens.screen}", truncate(screens.screen, 400)
        )
    if status == "working":
        if looks_like_failure(screens.tail):
            return emit(
                "FAILED",
                f"failed:{screens.screen}",
                f"(re-prompt it) {truncate(screens.screen, 300)}",
            )
        idle_for = now - (state.same_since if state.same_since is not None else now)
        if idle_for >= stall_secs:
            return emit(
                "STALLED",
                f"stalled:{screens.screen}",
                f"(no screen change {int(stall_secs)}s) {truncate(screens.screen, 300)}",
            )
        return state, events
    if status in SETTLED_STATUSES:
        return emit("SETTLED", f"settled:{status}", status)

    # `unknown` and anything a future Herdr invents: say so once rather than
    # classify it silently.
    return emit(
        "WATCHER",
        f"status:{status}",
        f"unrecognized agent_status {status!r} — not classifying",
    )


# --------------------------------------------------------------------------
# The only impure layer
# --------------------------------------------------------------------------


class Watcher:
    """Polls `herdr` and prints one line per event.

    `run` / `clock` / `sleep` are injected so tests never spawn a process,
    never sleep, and never signal anything.
    """

    def __init__(
        self,
        names: list[str],
        *,
        kind: str = "muse",
        interval: float = 20.0,
        stall_secs: float = 300.0,
        blind_after: int = 3,
        timeout: float = 30.0,
        run=None,
        clock=None,
        sleep=None,
        out=None,
    ) -> None:
        self.names = list(names)
        self.kind = kind
        self.interval = interval
        self.stall_secs = stall_secs
        self.blind_after = blind_after
        self.timeout = timeout
        self._run = run
        self._clock = clock
        self._sleep = sleep
        self.out = out if out is not None else sys.stdout
        self.states: dict[str, AgentState] = {}
        self.watcher_last: str | None = None

    # -- thin subprocess wrapper ------------------------------------------
    def _herdr(self, *args: str) -> tuple[int, str, str | None]:
        """(returncode, stdout, detail). `detail` is set when the call itself
        could not run (binary gone, timeout) so the WATCHER line can say why."""
        # Resolved at call time, not bound as a default arg: a
        # `run=subprocess.run` default would defeat test patching.
        runner = self._run if self._run is not None else subprocess.run
        try:
            proc = runner(
                ["herdr", *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, "", f"{type(exc).__name__}: {exc}"
        return proc.returncode, proc.stdout or "", None

    def now(self) -> float:
        return (self._clock if self._clock is not None else time.monotonic)()

    def pause(self) -> None:
        (self._sleep if self._sleep is not None else time.sleep)(self.interval)

    def say(self, event: Event) -> None:
        print(event.line(), file=self.out, flush=True)

    def say_watcher_once(self, text: str) -> None:
        if self.watcher_last == text:
            return
        self.watcher_last = text
        self.say(Event("WATCHER", "", text))

    # -- one pass ----------------------------------------------------------
    def discover(self) -> list[str]:
        if self.names:
            return list(self.names)
        returncode, stdout, detail = self._herdr("agent", "list")
        listing = parse_agent_list(returncode, stdout, self.kind, detail)
        if listing.error:
            self.say_watcher_once(listing.error)
            return []
        if not listing.names:
            self.say_watcher_once(
                f"no {self.kind} agents in `herdr agent list`"
                + (f" ({listing.total} agents of other kinds)" if listing.total else "")
            )
            return []
        self.watcher_last = None
        return list(listing.names)

    def poll_once(self) -> list[Event]:
        emitted: list[Event] = []
        now = self.now()
        for name in self.discover():
            returncode, stdout, detail = self._herdr("agent", "get", name)
            probe = parse_agent_get(returncode, stdout, detail)
            raw = ""
            if probe.status is not None:
                # One read per agent per tick; the three slices come off it.
                # The shell version read the screen twice on a working agent.
                _, raw, _ = self._herdr(
                    "agent", "read", name, "--source", "visible", "--lines", "40"
                )
            state, events = step(
                name,
                self.states.get(name, AgentState()),
                probe,
                Screens.from_raw(raw),
                now,
                stall_secs=self.stall_secs,
                blind_after=self.blind_after,
            )
            self.states[name] = state
            for event in events:
                emitted.append(event)
                self.say(event)
        return emitted

    def all_named_gone(self) -> bool:
        """True once every explicitly-named agent is gone. Discovery mode never
        self-exits — a new Muse can still appear."""
        if not self.names:
            return False
        return all(
            self.states.get(n, AgentState()).status == "gone" for n in self.names
        )

    def run_forever(self) -> int:
        while True:
            self.poll_once()
            if self.all_named_gone():
                return 0
            self.pause()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Watch Herdr agents and print one line whenever one needs the manager.",
        epilog="With no names, watches every live agent of --kind.",
    )
    parser.add_argument(
        "names",
        nargs="*",
        help="agent names (default: discover from `herdr agent list`)",
    )
    parser.add_argument(
        "--kind", default="muse", help="agent kind to discover (default: muse)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=20.0,
        help="seconds between polls (default: 20)",
    )
    parser.add_argument(
        "--stall-secs",
        type=float,
        default=300.0,
        help="seconds of unchanged screen while working before STALLED (default: 300)",
    )
    parser.add_argument(
        "--blind-after",
        type=int,
        default=3,
        help="consecutive failed `herdr agent get` calls before a WATCHER line (default: 3)",
    )
    parser.add_argument("--once", action="store_true", help="one poll pass, then exit")
    args = parser.parse_args(argv)

    if shutil.which("herdr") is None:
        print("WATCHER: `herdr` is not on PATH — nothing to watch", file=sys.stderr)
        return 2

    watcher = Watcher(
        args.names,
        kind=args.kind,
        interval=args.interval,
        stall_secs=args.stall_secs,
        blind_after=args.blind_after,
    )
    if args.once:
        watcher.poll_once()
        return 0
    try:
        return watcher.run_forever()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
