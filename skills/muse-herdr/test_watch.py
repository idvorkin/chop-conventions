#!/usr/bin/env python3
"""Unit tests for watch.py — parsing and the classification state machine.

Run with: python3 -m unittest test_watch

No process is ever started, signalled or killed here: the `herdr` layer is a
stub callable injected into `Watcher`, and the pure functions take strings.
"""

import io
import json
import sys
import unittest
from pathlib import Path

# `unittest discover` already puts the start dir on sys.path; the insert keeps
# pytest and pyright happy without adding a conftest.py.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import watch  # noqa: E402 — sibling import after the sys.path shim above
from watch import (  # noqa: E402 — sibling import after the sys.path shim above
    AgentState,
    Screens,
    Watcher,
    filter_lines,
    looks_like_failure,
    looks_like_thinking,
    parse_agent_get,
    parse_agent_list,
    step,
    truncate,
)


def get_ok(status: str) -> str:
    return json.dumps(
        {
            "id": "cli:agent:get",
            "result": {"type": "agent_info", "agent": {"agent_status": status}},
        }
    )


def get_error(code: str, message: str = "nope") -> str:
    return json.dumps(
        {"id": "cli:agent:get", "error": {"code": code, "message": message}}
    )


def advance(
    state: AgentState,
    status: str,
    screen_text: str = "",
    now: float = 0.0,
    *,
    stall_secs: float = 300.0,
    blind_after: int = 3,
):
    return step(
        "m1",
        state,
        watch.Probe(status=status),
        Screens.from_raw(screen_text),
        now,
        stall_secs=stall_secs,
        blind_after=blind_after,
    )


class ParseAgentGetTests(unittest.TestCase):
    def test_status_from_envelope(self):
        probe = parse_agent_get(0, get_ok("blocked"))
        self.assertEqual(probe.status, "blocked")
        self.assertFalse(probe.gone)
        self.assertTrue(probe.known)

    def test_agent_not_found_is_the_only_gone(self):
        probe = parse_agent_get(1, get_error("agent_not_found"))
        self.assertTrue(probe.gone)
        self.assertIsNone(probe.status)

    def test_other_error_code_is_not_gone(self):
        probe = parse_agent_get(1, get_error("agent_not_ready"))
        self.assertFalse(probe.gone)
        self.assertFalse(probe.known)
        self.assertIn("agent_not_ready", probe.error)

    def test_empty_stdout_is_blindness_not_gone(self):
        """The shell version's `// "gone"` turned this into a false 'gone'."""
        probe = parse_agent_get(1, "")
        self.assertFalse(probe.gone)
        self.assertFalse(probe.known)
        self.assertIn("no output", probe.error)

    def test_empty_stdout_reports_the_subprocess_detail(self):
        probe = parse_agent_get(127, "", "FileNotFoundError: herdr")
        self.assertIn("FileNotFoundError", probe.error)

    def test_unparsable_output_is_blindness(self):
        probe = parse_agent_get(0, "herdr: command not found")
        self.assertFalse(probe.known)
        self.assertIn("unparsable", probe.error)

    def test_missing_status_field_is_blindness_not_gone(self):
        """A schema change must not be reported as a vanished agent."""
        probe = parse_agent_get(
            0, json.dumps({"result": {"type": "agent_info", "agent": {}}})
        )
        self.assertFalse(probe.gone)
        self.assertFalse(probe.known)
        self.assertIn("agent_status", probe.error)

    def test_non_object_payload_is_blindness(self):
        probe = parse_agent_get(0, "[1, 2, 3]")
        self.assertFalse(probe.known)


class ParseAgentListTests(unittest.TestCase):
    def _listing(self, agents):
        return json.dumps({"result": {"type": "agent_list", "agents": agents}})

    def test_filters_by_kind(self):
        listing = parse_agent_list(
            0,
            self._listing(
                [
                    {"name": "bell-lab", "agent": "muse", "pane_id": "w1:p1"},
                    {"name": "claude-1", "agent": "claude", "pane_id": "w2:p1"},
                    # never `agent start <name>`-ed: the pane id is the target
                    {"name": None, "agent": "muse", "pane_id": "w3:p2"},
                ]
            ),
            "muse",
        )
        self.assertEqual(listing.names, ("bell-lab", "w3:p2"))
        self.assertEqual(listing.total, 3)
        self.assertIsNone(listing.error)

    def test_agent_without_a_name_or_pane_id_is_skipped(self):
        listing = parse_agent_list(0, self._listing([{"agent": "muse"}]), "muse")
        self.assertEqual(listing.names, ())
        self.assertEqual(listing.total, 1)

    def test_kind_filter_matching_nothing_still_reports_the_total(self):
        listing = parse_agent_list(
            0, self._listing([{"name": "c1", "agent": "claude"}]), "muse"
        )
        self.assertEqual(listing.names, ())
        self.assertEqual(listing.total, 1)

    def test_empty_output_is_an_error(self):
        self.assertIn("no output", parse_agent_list(1, "", "muse").error)

    def test_schema_change_is_an_error_not_an_empty_list(self):
        self.assertIn(
            "result.agents",
            parse_agent_list(0, json.dumps({"result": {}}), "muse").error,
        )


class ScreenTests(unittest.TestCase):
    def test_filter_drops_blanks_and_chrome(self):
        raw = "hello\n\n  \nVoice input ready\n──────\nworld"
        self.assertEqual(filter_lines(raw), ["hello", "world"])

    def test_truncate_is_character_oriented(self):
        """bash's ${s:0:n} sliced bytes and could halve a multi-byte glyph."""
        text = "🦝" * 10
        self.assertEqual(truncate(text, 4), "🦝🦝🦝🦝")
        self.assertEqual(len(truncate(text, 4)), 4)
        self.assertEqual(truncate("short", 400), "short")

    def test_screens_slices_one_read_three_ways(self):
        raw = "\n".join([f"l{i}" for i in range(20)] + ["muse-spark-1.3", "❯"])
        screens = Screens.from_raw(raw)
        self.assertEqual(len(screens.screen.split()), 8)
        self.assertTrue(screens.screen.startswith("l14 "))
        self.assertEqual(len(screens.wide.split()), 14)
        self.assertTrue(screens.wide.startswith("l8 "))
        # the failure filter drops the prompt line and the model banner
        self.assertEqual(screens.tail, "l17 l18 l19")

    def test_thinking_and_failure_detectors(self):
        self.assertTrue(looks_like_thinking("Thinking (12s · esc to interrupt)"))
        self.assertTrue(looks_like_thinking("ctrl+b to send to background"))
        self.assertFalse(looks_like_thinking("Do you want to allow this? (y/n)"))
        self.assertTrue(looks_like_failure("model failed: 500"))
        self.assertTrue(looks_like_failure("transport error"))
        self.assertFalse(looks_like_failure("all tests passed"))


class StepTests(unittest.TestCase):
    def test_blocked_emits_once_then_dedups(self):
        state, events = advance(
            AgentState(), "blocked", "Allow write to src/main.rs? (y/n)"
        )
        self.assertEqual([e.kind for e in events], ["BLOCKED"])
        self.assertIn("Allow write", events[0].text)
        state, events = advance(
            state, "blocked", "Allow write to src/main.rs? (y/n)", now=10
        )
        self.assertEqual(events, [])

    def test_second_identical_prompt_after_approval_is_reported(self):
        """The shell watcher only reset its dedup key on idle/done/gone, so a
        repeated identical approval banner was silently swallowed."""
        state, _ = advance(AgentState(), "blocked", "Reviewing approval request")
        state, working = advance(state, "working", "running tests", now=10)
        self.assertEqual(working, [])
        state, events = advance(state, "blocked", "Reviewing approval request", now=20)
        self.assertEqual([e.kind for e in events], ["BLOCKED"])

    def test_blocked_while_thinking_is_not_an_approval(self):
        state, events = advance(
            AgentState(), "blocked", "Thinking (94s · esc to interrupt)"
        )
        self.assertEqual(events, [])
        self.assertIsNone(state.last_emit)

    def test_failed_model_call_while_working(self):
        _, events = advance(AgentState(), "working", "stream error: model failed")
        self.assertEqual([e.kind for e in events], ["FAILED"])
        self.assertIn("re-prompt", events[0].text)

    def test_stall_needs_an_unchanged_screen_for_the_window(self):
        state, _ = advance(AgentState(), "working", "compiling", now=0)
        state, quiet = advance(state, "working", "compiling", now=100, stall_secs=300)
        self.assertEqual(quiet, [])
        state, events = advance(state, "working", "compiling", now=400, stall_secs=300)
        self.assertEqual([e.kind for e in events], ["STALLED"])

    def test_screen_movement_resets_the_stall_clock(self):
        state, _ = advance(AgentState(), "working", "compiling", now=0)
        state, _ = advance(state, "working", "linking", now=290)
        _, events = advance(state, "working", "linking", now=500, stall_secs=300)
        self.assertEqual(events, [])

    def test_settled_emits_once_per_status(self):
        state, events = advance(AgentState(), "idle", "done here", now=0)
        self.assertEqual(events[0].line(), "m1 idle")
        state, again = advance(state, "idle", "done here", now=10)
        self.assertEqual(again, [])

    def test_gone_emits_once(self):
        state, events = step(
            "m1",
            AgentState(),
            watch.Probe(gone=True),
            Screens(),
            0.0,
            stall_secs=300,
            blind_after=3,
        )
        self.assertEqual(events[0].line(), "m1 gone")
        self.assertEqual(state.status, "gone")
        _, again = step(
            "m1",
            state,
            watch.Probe(gone=True),
            Screens(),
            10.0,
            stall_secs=300,
            blind_after=3,
        )
        self.assertEqual(again, [])

    def test_unrecognized_status_is_announced_once(self):
        state, events = advance(AgentState(), "unknown", "")
        self.assertEqual(events[0].kind, "WATCHER")
        self.assertIn("unrecognized", events[0].text)
        _, again = advance(state, "unknown", "", now=10)
        self.assertEqual(again, [])

    def test_blindness_is_announced_at_the_threshold_then_recovery(self):
        blind = watch.Probe(error="no output from `herdr agent get` (exit 1)")
        state = AgentState()
        for _ in range(2):
            state, events = step(
                "m1", state, blind, Screens(), 0.0, stall_secs=300, blind_after=3
            )
            self.assertEqual(events, [])
        state, events = step(
            "m1", state, blind, Screens(), 0.0, stall_secs=300, blind_after=3
        )
        self.assertEqual([e.kind for e in events], ["WATCHER"])
        self.assertIn("cannot read", events[0].text)
        # Stays quiet while still blind…
        state, events = step(
            "m1", state, blind, Screens(), 0.0, stall_secs=300, blind_after=3
        )
        self.assertEqual(events, [])
        # …and says so when the agent is readable again, alongside its state.
        state, events = advance(state, "idle", "all done", now=20)
        self.assertEqual([e.kind for e in events], ["WATCHER", "SETTLED"])
        self.assertFalse(state.blind)

    def test_blindness_does_not_fake_a_gone(self):
        _, events = step(
            "m1",
            AgentState(),
            watch.Probe(error="unparsable"),
            Screens(),
            0.0,
            stall_secs=300,
            blind_after=1,
        )
        self.assertNotIn("GONE", [e.kind for e in events])


class FakeHerdr:
    """Canned `herdr` responses keyed by subcommand. Never spawns anything."""

    def __init__(self, get_by_name, listing=None, screen=""):
        self.get_by_name = get_by_name
        self.listing = listing
        self.screen = screen
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        sub = argv[1:3]
        if sub == ["agent", "list"]:
            return self._done(self.listing or "")
        if sub == ["agent", "get"]:
            return self._done(self.get_by_name[argv[3]])
        if sub == ["agent", "read"]:
            return self._done(self.screen)
        raise AssertionError(f"unexpected herdr call: {argv}")

    @staticmethod
    def _done(stdout):
        class Proc:
            returncode = 0

            def __init__(self, out):
                self.stdout = out
                self.stderr = ""

        return Proc(stdout)


class WatcherTests(unittest.TestCase):
    def _watcher(self, names, herdr, **kwargs):
        return Watcher(
            names,
            run=herdr,
            clock=lambda: 0.0,
            sleep=lambda _s: None,
            out=io.StringIO(),
            **kwargs,
        )

    def test_poll_reads_the_screen_once_per_agent(self):
        herdr = FakeHerdr({"m1": get_ok("working")}, screen="compiling")
        watcher = self._watcher(["m1"], herdr)
        watcher.poll_once()
        reads = [c for c in herdr.calls if c[1:3] == ["agent", "read"]]
        self.assertEqual(len(reads), 1)

    def test_gone_agent_is_not_read(self):
        herdr = FakeHerdr({"m1": get_error("agent_not_found")})
        watcher = self._watcher(["m1"], herdr)
        events = watcher.poll_once()
        self.assertEqual([e.line() for e in events], ["m1 gone"])
        self.assertEqual([c for c in herdr.calls if c[1:3] == ["agent", "read"]], [])

    def test_run_forever_exits_when_every_named_agent_is_gone(self):
        herdr = FakeHerdr({"m1": get_error("agent_not_found")})
        watcher = self._watcher(["m1"], herdr)
        self.assertEqual(watcher.run_forever(), 0)

    def test_discovery_mode_warns_once_when_no_agent_matches(self):
        listing = json.dumps(
            {"result": {"agents": [{"name": "c1", "agent": "claude"}]}}
        )
        herdr = FakeHerdr({}, listing=listing)
        watcher = self._watcher([], herdr)
        watcher.poll_once()
        watcher.poll_once()
        printed = watcher.out.getvalue().splitlines()
        self.assertEqual(len(printed), 1)
        self.assertIn("no muse agents", printed[0])
        self.assertIn("1 agents of other kinds", printed[0])

    def test_discovery_mode_never_self_exits(self):
        herdr = FakeHerdr({}, listing=json.dumps({"result": {"agents": []}}))
        watcher = self._watcher([], herdr)
        watcher.poll_once()
        self.assertFalse(watcher.all_named_gone())

    def test_subprocess_failure_is_reported_not_swallowed(self):
        def explode(argv, **kwargs):
            raise FileNotFoundError("herdr")

        watcher = self._watcher(["m1"], explode, blind_after=1)
        events = watcher.poll_once()
        self.assertEqual([e.kind for e in events], ["WATCHER"])
        self.assertIn("FileNotFoundError", events[0].text)

    def test_blocked_line_is_printed(self):
        herdr = FakeHerdr(
            {"m1": get_ok("blocked")}, screen="Allow `rm -rf build`? (y/n)"
        )
        watcher = self._watcher(["m1"], herdr)
        watcher.poll_once()
        self.assertIn("BLOCKED m1: Allow `rm -rf build`? (y/n)", watcher.out.getvalue())


if __name__ == "__main__":
    unittest.main()
