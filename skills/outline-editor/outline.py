#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""Build an outline-editor page from markdown headings or a JSON outline.

    outline.py from-md post.md [other.md ...] --html outline.html [--json outline.json]
    outline.py render outline.json --html outline.html

`from-md` turns every heading of each file into a section card (one page per
file, plus an empty "Cut" page and a "Not placed" tray). Edit the JSON to add
notes, statuses, new topics or intro HTML, then `render` it again.
"""

import json
import re
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")

DEFAULT_STATUSES = [
    {"key": "keep", "label": "keep", "note": "stays as it is", "color": "#2e7d4f"},
    {
        "key": "rewrite",
        "label": "rewrite",
        "note": "keep, but rewrite",
        "color": "#b4572e",
    },
    {"key": "new", "label": "new", "note": "not written yet", "color": "#2f5d8a"},
    {"key": "cut", "label": "cut", "note": "drop", "color": "#8a8a85"},
]

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FRONT_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.M)


def clean_heading(text: str) -> str:
    """Strip markdown decoration from a heading: links, code ticks, {#anchors}, emphasis."""
    text = re.sub(r"\{#[^}]*\}\s*$", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_]", "", text)
    return text.strip()


def split_front_matter(md: str) -> tuple[str, str]:
    """Return (front_matter, body). Front matter is the leading --- block, if any."""
    if md.startswith("---\n"):
        end = md.find("\n---", 4)
        if end != -1:
            return md[4:end], md[end + 4 :].lstrip("\n")
    return "", md


def parse_headings(md: str) -> list[dict]:
    """Headings outside code fences, with level, 1-based line number and a text snippet."""
    front, body = split_front_matter(md)
    offset = md.count("\n", 0, len(md) - len(body))
    lines = body.split("\n")
    out: list[dict] = []
    in_fence = False
    for n, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            out.append(
                {
                    "level": len(m.group(1)),
                    "title": clean_heading(m.group(2)),
                    "line": n + 1 + offset,
                    "body": [],
                }
            )
        elif out and line.strip():
            out[-1]["body"].append(line.strip())
    for h in out:
        h["snippet"] = snippet(" ".join(h.pop("body")))
    return out


def snippet(text: str, limit: int = 220) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\{%.*?%\}|<[^>]+>|[`*_>#|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "page"


def page_prefix(index: int) -> str:
    """A, B, ... Z, AA, AB ... — the id prefix for the n-th file."""
    s = ""
    index += 1
    while index:
        index, r = divmod(index - 1, 26)
        s = chr(65 + r) + s
    return s


def build_data(
    files: list[tuple[str, str]], title: str | None = None, skip_h1: bool = True
) -> dict:
    """files = [(path, markdown)]. One page per file; each heading becomes a section."""
    pages, origins, items = [], [], []
    used: set[str] = set()
    for i, (path, md) in enumerate(files):
        front, _ = split_front_matter(md)
        m = _FRONT_TITLE.search(front)
        page_title = m.group(1).strip("\"'") if m else Path(path).stem
        key = slug(Path(path).stem)
        while key in used or key in ("cut", "tray"):
            key += "-2"
        used.add(key)
        heads = parse_headings(md)
        if (
            skip_h1
            and sum(h["level"] == 1 for h in heads) == 1
            and heads
            and heads[0]["level"] == 1
        ):
            heads = heads[1:]
        base = min((h["level"] for h in heads), default=2)
        pages.append({"key": key, "title": page_title, "sub": path})
        origins.append({"key": key, "title": f"{page_title}, as written"})
        prefix = page_prefix(i)
        for n, h in enumerate(heads, 1):
            items.append(
                {
                    "id": f"{prefix}{n:02d}",
                    "title": h["title"],
                    "page": key,
                    "origin": key,
                    "status": "keep",
                    "depth": h["level"] - base,
                    "why": h["snippet"],
                    "src": f"{path}:{h['line']}",
                }
            )
    pages.append({"key": "cut", "title": "Cut", "sub": "dropped", "status": "cut"})
    pages.append(
        {
            "key": "tray",
            "title": "Not placed",
            "sub": "new topics waiting for a home",
            "tray": True,
        }
    )
    name = title or ("Restructure: " + " + ".join(p["title"] for p in pages[:-2]))
    return {
        "title": name,
        "subtitle": "Drag sections into the order and pages you want, then send the outline.",
        "storage_key": "outline-editor:" + slug(name),
        "statuses": DEFAULT_STATUSES,
        "default_status": "keep",
        "pages": pages,
        "origins": origins,
        "items": items,
    }


def validate(data: dict) -> list[str]:
    """Problems that would break the page. Empty list means OK."""
    errs = []
    for k in ("title", "statuses", "pages", "items"):
        if k not in data:
            errs.append(f"missing key: {k}")
    if errs:
        return errs
    pages = {p["key"] for p in data["pages"]}
    statuses = {s["key"] for s in data["statuses"]}
    seen = set()
    for it in data["items"]:
        iid = it.get("id", "")
        if not iid or iid in seen or iid.startswith("~h"):
            errs.append(f"bad or duplicate id: {iid!r}")
        seen.add(iid)
        if it.get("page") not in pages:
            errs.append(f"{iid}: unknown page {it.get('page')!r}")
        if it.get("status", data.get("default_status")) not in statuses:
            errs.append(f"{iid}: unknown status {it.get('status')!r}")
    for p in data["pages"]:
        if p.get("status") and p["status"] not in statuses:
            errs.append(f"page {p['key']}: unknown status {p['status']!r}")
    return errs


def render(data: dict, template: str | None = None) -> str:
    """Inject the outline JSON (and optional intro/extra HTML) into the template."""
    if template is None:
        template = TEMPLATE.read_text()
    blob = json.dumps(data, ensure_ascii=False, indent=1)
    # keep the JSON from closing its <script> element early
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")
    title = data["title"].replace("&", "&amp;").replace("<", "&lt;")
    subs = {
        "__TITLE__": title,
        "__INTRO_HTML__": data.get("intro_html", ""),
        "__EXTRA_HTML__": data.get("extra_html", ""),
        "__OUTLINE_DATA__": blob,
    }
    return re.sub("|".join(subs), lambda m: subs[m.group(0)], template)


def _build_app():
    import typer

    app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

    def _write(data: dict, html: Path, json_out: Path | None) -> None:
        errs = validate(data)
        if errs:
            for e in errs:
                typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1)
        if json_out:
            json_out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            typer.echo(f"wrote {json_out}")
        html.write_text(render(data))
        typer.echo(
            f"wrote {html} ({len(data['items'])} sections, {len(data['pages'])} pages)"
        )

    @app.command("from-md")
    def from_md(
        files: list[Path] = typer.Argument(
            ..., exists=True, dir_okay=False, help="Markdown files, one page each"
        ),
        html: Path = typer.Option(..., "--html", help="Page to write"),
        json_out: Path | None = typer.Option(
            None, "--json", help="Also write the outline JSON, for editing"
        ),
        title: str | None = typer.Option(None, help="Page title"),
        keep_h1: bool = typer.Option(
            False, help="Keep a lone top-level # heading as a section"
        ),
    ):
        """Turn the headings of markdown files into an outline page."""
        data = build_data(
            [(str(f), f.read_text()) for f in files], title=title, skip_h1=not keep_h1
        )
        _write(data, html, json_out)

    @app.command("render")
    def render_cmd(
        data_file: Path = typer.Argument(
            ..., exists=True, dir_okay=False, help="Outline JSON"
        ),
        html: Path = typer.Option(..., "--html", help="Page to write"),
    ):
        """Render an (edited) outline JSON into a page."""
        _write(json.loads(data_file.read_text()), html, None)

    return app


if __name__ == "__main__":
    _build_app()()
