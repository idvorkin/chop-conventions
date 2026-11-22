import copy
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


def _blob(html):
    return re.search(
        r'<script id="outline-data" type="application/json">(.*?)</script>', html, re.S
    ).group(1)


def test_render_html_comment_open_stays_valid_json():
    title = "A <!-- comment and <script> and </script> & more"
    d = outline.build_data([("a.md", "## One\n")])
    d["items"][0]["title"] = title
    blob = _blob(outline.render(d))
    assert "<" not in blob  # nothing the HTML parser can act on
    assert "\\!" not in blob  # not a JSON escape: JSON.parse would throw
    assert json.loads(blob)["items"][0]["title"] == title


def test_validate_reports_missing_top_level_keys():
    errs = outline.validate({"title": "T"})
    assert errs == ["missing key: statuses", "missing key: pages", "missing key: items"]


def test_validate_reports_entries_without_key_instead_of_raising():
    # deepcopy: build_data shares DEFAULT_STATUSES with the module
    d = copy.deepcopy(outline.build_data([("a.md", "## One\n")]))
    del d["pages"][0]["key"]
    del d["statuses"][1]["key"]
    d["items"].append("not an object")
    errs = outline.validate(d)
    assert "pages[0]: missing key: key" in errs
    assert "statuses[1]: missing key: key" in errs
    assert "items[1]: must be an object" in errs


def test_validate_reports_non_list_sections():
    d = outline.build_data([("a.md", "## One\n")])
    d["pages"] = {"a": 1}
    assert outline.validate(d) == ["pages: must be a list"]


def test_cli_creates_parent_dirs_and_exits_1_on_invalid(tmp_path):
    from typer.testing import CliRunner

    app = outline._build_app()
    md = tmp_path / "a.md"
    md.write_text("## One\n")
    html = tmp_path / "new" / "deep" / "out.html"
    js = tmp_path / "other" / "out.json"
    ok = CliRunner().invoke(
        app, ["from-md", str(md), "--html", str(html), "--json", str(js)]
    )
    assert ok.exit_code == 0, ok.output
    assert html.exists() and js.exists()

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"title": "T", "statuses": [{}], "pages": [], "items": []})
    )
    res = CliRunner().invoke(
        app, ["render", str(bad), "--html", str(tmp_path / "x.html")]
    )
    assert res.exit_code == 1
    assert "missing key: key" in res.output
    assert not (tmp_path / "x.html").exists()


def test_example_outline_is_valid():
    data = json.loads(
        (outline.TEMPLATE.parent / "example" / "outline.json").read_text()
    )
    assert outline.validate(data) == []
