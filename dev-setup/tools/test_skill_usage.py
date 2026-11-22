import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from skill_usage import scan, summarize, uses_in_record, known_skills  # noqa: E402

KNOWN = ["ammon", "up-to-date", "docs"]


def tool_line(name, uuid="u1", session="s1", ts="2026-11-20T01:00:00Z"):
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": ts,
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": ""}],
            },
            "toolUseResult": {"success": True, "commandName": name},
        }
    )


def typed_line(name, uuid="t1", session="s1", ts="2026-11-20T02:00:00Z"):
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": ts,
            "message": {
                "role": "user",
                "content": f"<command-name>/{name}</command-name>",
            },
        }
    )


def test_tool_result_counts():
    uses = uses_in_record(json.loads(tool_line("ammon")), set(KNOWN))
    assert [(u.skill, u.source) for u in uses] == [("ammon", "tool")]


def test_typed_slash_command_counts():
    uses = uses_in_record(json.loads(typed_line("up-to-date")), set(KNOWN))
    assert [(u.skill, u.source) for u in uses] == [("up-to-date", "typed")]


def test_prefixed_and_unknown_names_skipped():
    rec = json.loads(tool_line("anthropic-skills:docs"))
    assert uses_in_record(rec, set(KNOWN)) == []
    assert uses_in_record(json.loads(typed_line("clear")), set(KNOWN)) == []


def test_assistant_text_mentioning_command_not_counted():
    rec = {
        "type": "assistant",
        "uuid": "a",
        "message": {
            "content": [{"type": "text", "text": "<command-name>/ammon</command-name>"}]
        },
    }
    assert uses_in_record(rec, set(KNOWN)) == []


def test_duplicate_uuid_across_files_counted_once():
    report = scan(
        [[tool_line("ammon", uuid="x")], [tool_line("ammon", uuid="x")]], KNOWN
    )
    assert len(report.uses) == 1
    assert report.files == 2


def test_window_spans_all_lines_not_just_hits():
    plain = json.dumps(
        {"type": "assistant", "uuid": "p", "timestamp": "2026-10-01T00:00:00Z"}
    )
    report = scan([[plain, tool_line("ammon")]], KNOWN)
    assert report.first_ts == "2026-10-01T00:00:00Z"
    assert report.last_ts == "2026-11-20T01:00:00Z"


def test_bad_lines_ignored():
    report = scan([["not json", "[1,2]", tool_line("docs")]], KNOWN)
    assert [u.skill for u in report.uses] == ["docs"]


def test_summarize_orders_and_counts_sessions():
    lines = [
        tool_line("ammon", uuid="1", session="a"),
        tool_line("ammon", uuid="2", session="b", ts="2026-11-21T00:00:00Z"),
        typed_line("ammon", uuid="3", session="b"),
        tool_line("docs", uuid="4"),
    ]
    rows = summarize(scan([lines], KNOWN))
    assert [r["skill"] for r in rows] == ["ammon", "docs", "up-to-date"]
    assert rows[0] == {
        "skill": "ammon",
        "uses": 3,
        "sessions": 2,
        "typed": 1,
        "last_used": "2026-11-21T00:00:00Z",
    }
    assert rows[2]["uses"] == 0 and rows[2]["last_used"] is None


def test_known_skills_requires_skill_md(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "SKILL.md").write_text("x")
    (tmp_path / "junk").mkdir()
    assert known_skills(tmp_path) == ["real"]
