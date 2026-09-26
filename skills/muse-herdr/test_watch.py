"""Unit tests for watch.py's parsing and classification; nothing here runs herdr."""

import json
import unittest

from watch import Seen, classify, parse_names, parse_status, screen_lines

DIALOG = "Allow Muse to run `git commit`?\n  1. Yes\n  2. No\n"
THINKING = "Thinking (12s · esc to interrupt)\n"


def run(status, screen, seen, now=0.0, stall_secs=300):
    """The line classify prints, or "" for none."""
    return classify("bell", status, screen_lines(screen), seen, now, stall_secs) or ""


class ParseTest(unittest.TestCase):
    def test_status(self):
        out = json.dumps({"result": {"agent": {"agent_status": "blocked"}}})
        self.assertEqual(parse_status(out), "blocked")

    def test_not_found_is_gone(self):
        out = json.dumps({"error": {"code": "agent_not_found", "message": "…"}})
        self.assertEqual(parse_status(out), "gone")

    def test_a_failed_call_is_unreadable_not_gone(self):
        for out in [
            "",
            "not json",
            json.dumps({"error": {"code": "server_down"}}),
            json.dumps({"result": {}}),
        ]:
            self.assertIsNone(parse_status(out), out)

    def test_names_of_one_kind_skip_nameless_panes(self):
        out = json.dumps(
            {
                "result": {
                    "agents": [
                        {"agent": "muse", "name": "bell-lab"},
                        {"agent": "muse"},
                        {"agent": "codex", "name": "codex-review"},
                    ]
                }
            }
        )
        self.assertEqual(parse_names(out, "muse"), ["bell-lab"])
        self.assertIsNone(parse_names("", "muse"))


class ClassifyTest(unittest.TestCase):
    def test_a_dialog_is_reported_once(self):
        seen = Seen()
        self.assertTrue(
            run("blocked", DIALOG, seen).startswith("BLOCKED bell: Allow Muse")
        )
        self.assertEqual(run("blocked", DIALOG, seen), "")

    def test_the_same_dialog_after_working_is_reported_again(self):
        seen = Seen()
        run("blocked", DIALOG, seen)
        run("working", "compiling\n", seen)
        self.assertTrue(run("blocked", DIALOG, seen))

    def test_blocked_while_thinking_is_not_a_prompt(self):
        self.assertEqual(run("blocked", THINKING, Seen()), "")

    def test_a_long_think_scrolled_past_the_printed_tail_still_counts(self):
        screen = THINKING + "".join(f"reasoning line {i}\n" for i in range(10))
        self.assertEqual(run("blocked", screen, Seen()), "")

    def test_blocked_on_a_running_command_is_not_a_prompt(self):
        screen = "xcodebuild test\n(ctrl+b to send to background)\n"
        self.assertEqual(run("blocked", screen, Seen()), "")

    def test_a_failed_model_call_is_reported(self):
        line = run(
            "working", "step 2\n◆ model failed: 500\n❯\nmuse-spark-3 · repo\n", Seen()
        )
        self.assertTrue(line.startswith("FAILED bell"))

    def test_an_old_failure_above_live_work_is_not(self):
        screen = "◆ model failed: 500\nretrying\nreading watch.py\nediting watch.py\n"
        self.assertEqual(run("working", screen, Seen()), "")

    def test_a_frozen_working_screen_stalls(self):
        seen = Seen()
        self.assertEqual(run("working", "editing\n", seen, now=0), "")
        self.assertEqual(run("working", "editing\n", seen, now=299), "")
        self.assertTrue(
            run("working", "editing\n", seen, now=300).startswith("STALLED bell")
        )
        self.assertEqual(run("working", "editing\n", seen, now=400), "")

    def test_a_moving_screen_does_not_stall(self):
        seen = Seen()
        run("working", "a\n", seen, now=0)
        self.assertEqual(run("working", "b\n", seen, now=400), "")

    def test_settled_and_gone(self):
        seen = Seen()
        self.assertEqual(run("idle", "", seen), "bell idle")
        self.assertEqual(run("idle", "", seen), "")
        self.assertEqual(run("gone", "", seen), "bell gone")

    def test_an_unknown_status_is_said_out_loud(self):
        self.assertIn("unknown status", run("rebooting", "", Seen()))

    def test_braces_on_screen_are_printed_as_is(self):
        self.assertIn('{"a": 1}', run("blocked", 'Write {"a": 1}?\n', Seen()))

    def test_screen_chrome_is_dropped(self):
        self.assertEqual(screen_lines("a\n\n── rule\nVoice input off\nb"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
