import json
import re

import outline


def test_parse_headings_skips_fences_and_front_matter():
    md = "---\ntitle: T\n---\n\n# Top\n\n## A\ntext a\n\n```\n## not me\n```\n### B {#b}\n[link](x) body\n"
    hs = outline.parse_headings(md)
    assert [(h["level"], h["title"]) for h in hs] == [(1, "Top"), (2, "A"), (3, "B")]
    assert hs[1]["line"] == 7  # 1-based line in the original file
    assert hs[1]["snippet"] == "text a"
    assert hs[2]["snippet"] == "link body"


def test_clean_heading():
    assert (
        outline.clean_heading("Use [the docs](http://x) `fast` {#anchor}")
        == "Use the docs fast"
    )


def test_build_data_pages_ids_depths():
    d = outline.build_data(
        [("a.md", "# Title\n## One\n### One.a\n## Two\n"), ("b.md", "## Solo\n")]
    )
    assert [p["key"] for p in d["pages"]] == ["a", "b", "cut", "tray"]
    assert [(i["id"], i["depth"], i["page"]) for i in d["items"]] == [
        ("A01", 0, "a"),
        ("A02", 1, "a"),
        ("A03", 0, "a"),
        ("B01", 0, "b"),
    ]
    assert outline.validate(d) == []


def test_page_prefix():
    assert [outline.page_prefix(i) for i in (0, 25, 26, 27)] == ["A", "Z", "AA", "AB"]


def test_validate_catches_bad_refs():
    d = outline.build_data([("a.md", "## One\n")])
    d["items"][0]["page"] = "nope"
    d["items"].append(dict(d["items"][0], status="weird"))
    errs = outline.validate(d)
    assert any("unknown page" in e for e in errs)
    assert any("duplicate" in e for e in errs)
    assert any("unknown status" in e for e in errs)


def test_render_escapes_script_close_and_fills_slots():
    d = outline.build_data([("a.md", "## Ends </script> here\n")])
    d["intro_html"] = "<p>intro</p>"
    html = outline.render(d)
    assert "__OUTLINE_DATA__" not in html and "__INTRO_HTML__" not in html
    blob = re.search(
        r'<script id="outline-data" type="application/json">(.*?)</script>', html, re.S
    ).group(1)
    assert json.loads(blob)["items"][0]["title"] == "Ends </script> here"
    assert "<p>intro</p>" in html


def test_example_outline_is_valid():
    data = json.loads(
        (outline.TEMPLATE.parent / "example" / "outline.json").read_text()
    )
    assert outline.validate(data) == []
