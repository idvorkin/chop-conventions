"""Tests for walk.py's pure layer. Fixtures are made up; no real story text."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import walk  # noqa: E402

COHN = """# Journey

Intro text.

---

### User Story 001:

- **Summary:** Tap a card to open it
- **Status:** implemented in [abc1234](https://example.com/c/abc1234); verified by `test_cards.py::test_tap_opens` and `::test_tap_twice` and `test_swipe_away`, and the `route_smoke.cjs` `card-open` landmark

#### Use Case:

- **As** a reader
- **I want to** open a card
- **so that** I can read it

#### Acceptance Criteria:

- **Scenario:** One tap
- **Given:** a list of cards
- **When:** I tap the first
- **Then:** it opens

- **Scenario:** Two taps
- **Given:** an open card
- **When:** I tap it again
- **Then:** it closes

- **Issues:** none

---

### User Story 002:

- **Summary:** Swipe to dismiss
- **Issues:** #9
- **Status:** implemented in [def5678](https://example.com/c/def5678); not verified

#### Acceptance Criteria:

- **Scenario:** Swipe
- **Given:** an open card
- **When:** I swipe left
- **Then:** it is gone

---

### User Story 003:

- **Summary:** Old flow
- **Status:** superseded by 004 in [aaa1111](https://example.com); before that implemented in [bbb2222](https://example.com)

---

### User Story 004:

- **Summary:** Planned thing
- **Status:** planned (proj-12)

---

### User Story 005:

- **Summary:** Native side
- **Status:** native app only: [0c95985a](https://example.com/c/0c95985a), verified by host tests (`CardRulesTests`) and on the simulator (`just test-sim`)
"""

HYPHEN = """# Seeing things

## 001 — Pick the source

**Summary**: Every source is a button.
**Status**: Done (06c69ad; host tests `SourceTests`;
switching by key: run, Sam).

**Acceptance Criteria**

- Scenario: Switch by key
  - Given three sources
  - When I press 2
  - Then the second opens
  - And the readings reset

## 002 — Later

**Summary**: Not yet.
**Status**: Planned
"""


def stories():
    return walk.parse_file(COHN, "01-cards.md")


def test_cohn_dialect_parses_every_story_in_any_field_order():
    s = stories()
    assert [x.id for x in s] == ["001", "002", "003", "004", "005"]
    assert s[1].status.startswith("implemented in")  # Issues came before Status
    assert len(s[0].scenarios) == 2
    sc = s[0].scenarios[0]
    assert (sc.name, sc.given, sc.when, sc.then) == (
        "One tap",
        ["a list of cards"],
        ["I tap the first"],
        ["it opens"],
    )


def test_hyphen_dialect_wraps_status_and_keeps_and_steps():
    s = walk.parse_file(HYPHEN, "01-seeing.md")
    assert [x.id for x in s] == ["001", "002"]
    assert "switching by key" in s[0].status
    assert s[0].scenarios[0].then == ["the second opens", "the readings reset"]
    assert s[0].status_class == "implemented" and s[1].status_class == "planned"
    assert s[0].refs == ["xctest:SourceTests"]
    assert "human" in s[0].rungs


def test_status_classes():
    assert [x.status_class for x in stories()] == [
        "implemented",
        "implemented",
        "retired",
        "planned",
        "implemented",
    ]
    assert walk.classify_status("") == "unclassified"
    assert walk.classify_status("the rule decided last week") == "unclassified"


def test_refs_resolve_shorthand_and_landmarks():
    assert stories()[0].refs == [
        "pytest:test_cards.py::test_tap_opens",
        "pytest:test_cards.py::test_tap_twice",
        "pytest:*::test_swipe_away",
        "landmark:route_smoke.cjs#card-open",
    ]
    assert walk.extract_refs("checked by `assets/annotate.js` and `ink_audit.cjs`") == [
        "node:ink_audit.cjs"
    ]
    assert stories()[4].refs == ["xctest:CardRulesTests", "just:test-sim"]
    # a word after a script that is not called a landmark is not one
    assert walk.extract_refs(
        "verified by `test_board.mjs` `big` and `test_board.mjs`"
    ) == ["node:test_board.mjs"]
    assert walk.extract_refs("the `route_smoke.cjs` `a-b` and `c-d` landmarks") == [
        "landmark:route_smoke.cjs#a-b",
        "landmark:route_smoke.cjs#c-d",
    ]
    # a repo's own hyphenated script checks, keyed by basename with or without their directory
    assert walk.extract_refs(
        "by `scripts/native/test-ladder-map.sh`, `test-ladder-map.sh` and `test-board-sessions.py`; not `build-wasm.sh`"
    ) == ["script:test-ladder-map.sh", "script:test-board-sessions.py"]


def test_file_level_ref_is_the_worst_of_its_cases_and_parametrized_cases_match():
    checks = {
        "pytest:test_a.py::test_one": {"outcome": "pass"},
        "pytest:test_a.py::test_two[x]": {"outcome": "fail", "detail": "boom"},
    }
    assert walk.match_outcome("pytest:test_a.py", checks)[0] == "fail"
    assert walk.match_outcome("pytest:test_a.py::test_two", checks)[0] == "fail"
    assert walk.match_outcome("pytest:test_a.py::test_one", checks)[0] == "pass"
    assert walk.match_outcome("pytest:test_gone.py::test_x", checks)[0] == "missing"
    assert (
        walk.match_outcome("pytest:*::test_two", checks)[0] == "fail"
    )  # any file, parametrized
    assert walk.match_outcome("pytest:*::test_three", checks)[0] == "missing"
    checks["pytest:test_a.py::test_two[x]"] = {"outcome": "flaky"}
    assert walk.match_outcome("pytest:test_a.py", checks)[0] == "flaky"


def run_for(outcomes: dict, kinds=("pytest", "landmark")):
    return {
        "repo": "demo",
        "kinds": list(kinds),
        "checks": {k: {"outcome": v} for k, v in outcomes.items()},
    }


def test_verdicts_sum_to_the_story_count():
    run = run_for(
        {
            "pytest:test_cards.py::test_tap_opens": "pass",
            "pytest:test_cards.py::test_tap_twice": "pass",
            "pytest:test_other.py::test_swipe_away": "pass",
            "landmark:route_smoke.cjs#card-open": "pass",
        }
    )
    w = walk.judge(stories(), walk.Manifest(), run, None)
    v = {s["id"]: s["verdict"] for s in w["stories"]}
    assert v == {
        "001": "green",
        "002": "untestable",
        "003": "retired",
        "004": "planned",
        "005": "skipped",
    }
    assert w["summary"]["total"] == 5


def test_a_missing_named_test_is_red_and_xfail_is_known_red():
    run = run_for(
        {
            "pytest:test_cards.py::test_tap_opens": "known-red",
            "landmark:route_smoke.cjs#card-open": "pass",
        }
    )
    v = walk.judge_story(stories()[0], walk.Manifest(), run)
    assert v["verdict"] == "red" and not v.get("known")  # test_tap_twice is missing
    run["checks"]["pytest:test_cards.py::test_tap_twice"] = {"outcome": "pass"}
    run["checks"]["pytest:test_other.py::test_swipe_away"] = {"outcome": "pass"}
    v = walk.judge_story(stories()[0], walk.Manifest(), run)
    assert v["verdict"] == "red" and v["known"]


def test_manifest_overrides_checks_marks_untestable_and_ignores_cross_repo_refs(
    tmp_path,
):
    m = tmp_path / "walk.toml"
    m.write_text(
        'ignore_refs = ["^xctest:"]\n'
        '[stories.002]\nchecks = ["pytest:test_swipe.py"]\n'
        '[stories.001]\nuntestable = "needs a finger on glass"\n'
    )
    man = walk.load_manifest(m)
    run = run_for(
        {"pytest:test_swipe.py::test_left": "pass", "just:test-sim": "pass"},
        kinds=("pytest",),
    )
    v = {s.id: walk.judge_story(s, man, run) for s in stories()}
    assert (
        v["001"]["verdict"] == "untestable"
        and v["001"]["reason"] == "needs a finger on glass"
    )
    assert v["002"]["verdict"] == "green"
    assert [c["ref"] for c in v["005"]["checks"]] == [
        "just:test-sim"
    ]  # xctest ref ignored


def test_diff_finds_new_reds_fixes_and_known_reds_are_not_new():
    prev = [
        {"id": "1", "verdict": "red"},
        {"id": "2", "verdict": "green"},
        {"id": "9", "verdict": "green"},
    ]
    cur = [
        {"id": "1", "verdict": "green"},
        {"id": "2", "verdict": "red"},
        {"id": "3", "verdict": "red", "known": True},
    ]
    d = walk.diff_walks(cur, prev)
    assert (
        d["new_red"] == ["2"]
        and d["fixed"] == ["1"]
        and d["added"] == ["3"]
        and d["removed"] == ["9"]
    )
    assert walk.diff_walks(cur, None)["new_red"] == ["2"]


def test_file_reds_dry_run_prints_and_apply_dedupes_on_external_ref():
    run = run_for(
        {
            "pytest:test_cards.py::test_tap_opens": "fail",
            "pytest:test_other.py::test_swipe_away": "pass",
            "landmark:route_smoke.cjs#card-open": "pass",
        }
    )
    w = walk.judge(stories(), walk.Manifest(), run, None)
    assert walk.file_reds(w, apply=False, parent=None)[0].startswith(
        "first walk: 2 red checks"
    )
    w = walk.judge(
        stories(), walk.Manifest(), run, []
    )  # a previous walk with nothing red
    dry = walk.file_reds(w, apply=False, parent=None)
    assert len(dry) == 2 and all(line.startswith("bd create") for line in dry)
    calls = []

    def fake(argv, **_):
        calls.append(argv)
        if argv[1] == "list":
            ref = argv[argv.index("--external-ref") + 1]
            rows = (
                [{"id": "x-1", "status": "open"}]
                if ref.endswith("test_tap_opens")
                else []
            )
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
        return subprocess.CompletedProcess(argv, 0, "x-2\n", "")

    out = walk.file_reds(w, apply=True, parent="x-0", run=fake)
    assert out[0].startswith("open already: x-1") and out[1].startswith("filed x-2")
    assert sum(1 for c in calls if c[1] == "create") == 1


def test_report_renders_counts_and_sections():
    run = run_for(
        {
            "pytest:test_cards.py::test_tap_opens": "fail",
            "pytest:test_other.py::test_swipe_away": "pass",
            "landmark:route_smoke.cjs#card-open": "pass",
        }
    )
    md = walk.render_report(walk.judge(stories(), walk.Manifest(), run, None))
    assert "0 green · 1 red (0 known) · 1 skipped · 1 untestable" in md
    assert "## New reds" in md and "First walk" in md


def test_a_check_that_passed_its_retry_is_flaky_and_counts_as_passing():
    run = run_for(
        {
            "pytest:test_cards.py::test_tap_opens": "flaky",
            "pytest:test_cards.py::test_tap_twice": "pass",
            "pytest:test_other.py::test_swipe_away": "pass",
            "landmark:route_smoke.cjs#card-open": "pass",
        }
    )
    w = walk.judge(stories(), walk.Manifest(), run, None)
    v = w["stories"][0]
    assert v["verdict"] == "green" and v["flaky"]
    assert "## Flaky" in walk.render_report(w)
