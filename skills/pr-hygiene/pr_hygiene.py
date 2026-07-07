#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
"""pr-hygiene — which of my open PRs have genuinely unaddressed review feedback?

Why this exists
---------------
`git-safe-push`'s sibling footgun: leaving PRs open with review comments that
were never resolved or replied to. They pile up silently — GitHub gives no
nudge, and a naive "the last comment isn't mine" filter over-flags ~3x because
it can't tell a CodeRabbit auto-summary or a CI bot ping from a real ask.

What it does
------------
1. Enumerates open PRs authored by the user across BOTH GitHub identities
   (`idvorkin` and `idvorkin-ai-tools` by default) via `gh search prs`, deduped
   by repo#number. (Bitbucket repos like igor2 are invisible to `gh` — noted,
   not an error.)
2. For each PR, one GraphQL round-trip pulls review threads (isResolved /
   isOutdated / comments), reviewDecision, reviews, and issue comments.
3. Classifies each PR into a tier — this filtering is the whole point:
     RED    needs action  — >=1 UNRESOLVED review thread that is a real ask
                            (human reviewer, or a bot *finding* that isn't an
                            auto-summary), OR reviewDecision == CHANGES_REQUESTED,
                            OR a human reviewer's comment is the last word and
                            post-dates our last push.
     YELLOW awaiting merge — flagged only because a CodeRabbit auto-summary or a
                            github-actions/CI comment is the last event, with NO
                            open threads. Not stale, just merge-ready.
     GREEN  clean         — no review activity at all.
   CodeRabbit walkthrough/summary comments and CI-bot (github-actions) comments
   are treated as NOISE, never as asks.
4. Prints a ranked markdown table (RED first). `--json` for machines. Exit code
   is nonzero when any RED PR exists, so it can gate a session-close check.

Usage
-----
    pr-hygiene                          # markdown table across both identities
    pr-hygiene --json                   # machine-readable JSON
    pr-hygiene --author octocat         # override author (repeatable)
    pr-hygiene --repo idvorkin/chop-conventions   # filter to one repo
    pr-hygiene --no-fail                # always exit 0 (don't gate on RED)

Dependencies: gh (GitHub CLI, authenticated), python>=3.13. Typer for the CLI.

The pure classification/rendering functions below are stdlib-only and importable
without uv/typer, so they can be unit-tested directly (see tests/). Typer is
lazy-imported inside `_build_app()`.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

DEFAULT_AUTHORS = ["idvorkin", "idvorkin-ai-tools"]
DEFAULT_MAX_WORKERS = 8

# Bot logins that post *auto-summaries* / walkthroughs / CI status — NOISE, never
# an ask. CodeRabbit's real findings arrive as review *threads*, handled
# separately; only its issue-level comment is noise.
NOISE_BOT_LOGINS = {
    "coderabbitai",
    "coderabbitai[bot]",
    "github-actions",
    "github-actions[bot]",
    "codecov",
    "codecov[bot]",
    "vercel",
    "vercel[bot]",
    "netlify",
    "netlify[bot]",
    "sonarcloud",
    "sonarcloud[bot]",
    "socket-security",
    "socket-security[bot]",
}

# Substrings that mark a CodeRabbit auto-summary / walkthrough body.
AUTO_SUMMARY_MARKERS = (
    "summarize by coderabbit.ai",
    "walkthrough_start",
    "summary by coderabbit",
    "<!-- walkthrough",
    "actionable comments posted",
)


# --------------------------------------------------------------------------- #
# tiny stdlib helpers                                                          #
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    """Progress/diagnostics to stderr."""
    sys.stderr.write(msg if msg.endswith("\n") else msg + "\n")
    sys.stderr.flush()


def _parse_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_ago(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).days


def _login(node: dict) -> str | None:
    author = (node or {}).get("author") or {}
    return author.get("login")


def _typename(node: dict) -> str | None:
    author = (node or {}).get("author") or {}
    return author.get("__typename")


# --------------------------------------------------------------------------- #
# classification predicates (pure)                                            #
# --------------------------------------------------------------------------- #
def is_bot(login: str | None, typename: str | None) -> bool:
    """A GitHub actor is a bot if GraphQL types it as Bot or its login is
    suffixed `[bot]`. Human accounts (even automation accounts like
    idvorkin-ai-tools, which is a real User) are NOT bots."""
    if typename == "Bot":
        return True
    if login and login.endswith("[bot]"):
        return True
    return False


def is_auto_summary_body(body: str | None) -> bool:
    """True when a comment body is a CodeRabbit walkthrough/summary. Context-free
    so it can screen *thread* comments too: a bot's inline finding has a real
    suggestion body and never matches, while its issue-level summary does."""
    lowered = (body or "").lower()
    return any(m in lowered for m in AUTO_SUMMARY_MARKERS)


def is_noise_comment(login: str | None, typename: str | None, body: str | None) -> bool:
    """True when an *issue-level* comment/review is a CodeRabbit auto-summary or a
    CI/status bot ping — noise that a naive "last comment isn't mine" filter
    mistakes for an ask.

    This is deliberately blunt about known summary/CI bot logins, so it must NOT
    be used to screen review *threads* — CodeRabbit's real findings arrive as
    threads under the same login. Thread screening uses `is_auto_summary_body`,
    which keys on the body, not the login."""
    if login and login.lower() in NOISE_BOT_LOGINS:
        return True
    if is_bot(login, typename) and is_auto_summary_body(body):
        return True
    return False


def _last_push_dt(pr: dict) -> datetime | None:
    """Timestamp of our last pushed commit. `pushedDate` is sometimes null
    (GitHub stopped populating it for some pushes), so fall back to
    committedDate."""
    nodes = ((pr.get("commits") or {}).get("nodes")) or []
    if not nodes:
        return None
    commit = (nodes[0] or {}).get("commit") or {}
    return _parse_dt(commit.get("pushedDate")) or _parse_dt(commit.get("committedDate"))


def classify(pr: dict, author_login: str | None) -> dict:
    """Classify one PR's GraphQL payload into a tier.

    `author_login` is the PR author; a comment counts as a *reviewer* comment
    only when its author differs from the PR author (so self-notes on your own
    PR aren't mistaken for external review, while a note left by your *other*
    identity — e.g. idvorkin reviewing an idvorkin-ai-tools PR — correctly
    counts as a real ask).

    Returns a dict: {tier, unresolved, human_ask, changes_requested, verdict,
    last_actor, last_days}."""
    threads = ((pr.get("reviewThreads") or {}).get("nodes")) or []
    reviews = ((pr.get("reviews") or {}).get("nodes")) or []
    icomments = ((pr.get("comments") or {}).get("nodes")) or []
    review_decision = pr.get("reviewDecision")
    last_push = _last_push_dt(pr)

    unresolved = [t for t in threads if not t.get("isResolved")]

    # A real-ask thread: unresolved, with >=1 comment from a reviewer (not the
    # PR author) that isn't pure auto-summary noise.
    real_ask_threads: list[dict] = []
    human_ask = False
    for t in unresolved:
        comments = ((t.get("comments") or {}).get("nodes")) or []
        reviewer_comments = [
            c for c in comments if _login(c) and _login(c) != author_login
        ]
        # In a thread, a bot comment is a real finding (not an auto-summary) —
        # screen on the body, not the bot login, or genuine CodeRabbit/Copilot
        # inline findings get dropped.
        non_noise = [
            c for c in reviewer_comments if not is_auto_summary_body(c.get("body"))
        ]
        if not non_noise:
            continue
        real_ask_threads.append(t)
        if any(not is_bot(_login(c), _typename(c)) for c in non_noise):
            human_ask = True

    changes_requested = review_decision == "CHANGES_REQUESTED"

    # Human reviewer's comment/review is the last word AND the author hasn't
    # responded since. "Responded" = pushed a new commit, replied with a comment,
    # or submitted a review — any of those clears the flag (the reviewer no
    # longer has the last word). Comparing only against the last *push* wrongly
    # keeps PRs where the author already replied in a comment.
    human_events: list[tuple[datetime, str]] = []
    for r in reviews:
        lg, tn = _login(r), _typename(r)
        if lg and lg != author_login and not is_bot(lg, tn):
            dt = _parse_dt(r.get("submittedAt"))
            if dt:
                human_events.append((dt, lg))
    for c in icomments:
        lg, tn = _login(c), _typename(c)
        if (
            lg
            and lg != author_login
            and not is_bot(lg, tn)
            and not is_noise_comment(lg, tn, c.get("body"))
        ):
            dt = _parse_dt(c.get("createdAt"))
            if dt:
                human_events.append((dt, lg))

    author_events: list[datetime] = []
    if last_push:
        author_events.append(last_push)
    for r in reviews:
        if _login(r) == author_login:
            dt = _parse_dt(r.get("submittedAt"))
            if dt:
                author_events.append(dt)
    for c in icomments:
        if _login(c) == author_login:
            dt = _parse_dt(c.get("createdAt"))
            if dt:
                author_events.append(dt)
    author_latest = max(author_events) if author_events else None

    human_last_word = False
    if human_events:
        latest_human = max(human_events, key=lambda e: e[0])[0]
        if author_latest is None or latest_human > author_latest:
            human_last_word = True

    needs_action = bool(real_ask_threads) or changes_requested or human_last_word

    # Last review event overall (any non-author actor) — for the "who + days ago"
    # column. Falls back to PR updatedAt when nobody has reviewed.
    events: list[tuple[datetime, str]] = []
    for t in threads:
        for c in ((t.get("comments") or {}).get("nodes")) or []:
            lg = _login(c)
            dt = _parse_dt(c.get("createdAt"))
            if lg and lg != author_login and dt:
                events.append((dt, lg))
    for r in reviews:
        lg = _login(r)
        dt = _parse_dt(r.get("submittedAt"))
        if lg and lg != author_login and dt:
            events.append((dt, lg))
    for c in icomments:
        lg = _login(c)
        dt = _parse_dt(c.get("createdAt"))
        if lg and lg != author_login and dt:
            events.append((dt, lg))

    had_review_activity = bool(events)
    if events:
        last_dt, last_actor = max(events, key=lambda e: e[0])
    else:
        last_dt, last_actor = _parse_dt(pr.get("updatedAt")), "—"
    last_days = _days_ago(last_dt)

    if needs_action:
        tier = "red"
        bits = []
        if real_ask_threads:
            bits.append(
                f"{len(real_ask_threads)} unresolved thread(s)"
                + (" (human ask)" if human_ask else " (bot findings)")
            )
        if changes_requested:
            bits.append("CHANGES_REQUESTED")
        if human_last_word and not real_ask_threads:
            bits.append("human reviewer last word, post-push")
        verdict = "; ".join(bits)
    elif had_review_activity:
        tier = "yellow"
        verdict = "auto-summary / CI only, no open threads — merge-ready"
    else:
        tier = "green"
        verdict = "no review activity"

    return {
        "tier": tier,
        "unresolved": len(unresolved),
        "human_ask": human_ask,
        "changes_requested": changes_requested,
        "verdict": verdict,
        "last_actor": last_actor,
        "last_days": last_days,
    }


# --------------------------------------------------------------------------- #
# gh shell-outs (thin, injectable for tests)                                  #
# --------------------------------------------------------------------------- #
_GRAPHQL = """
query($owner:String!,$repo:String!,$number:Int!){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$number){
      author{login __typename}
      reviewDecision
      updatedAt
      commits(last:1){nodes{commit{committedDate pushedDate}}}
      reviewThreads(first:100){
        nodes{
          isResolved
          isOutdated
          comments(first:20){ nodes{ author{login __typename} body createdAt } }
        }
      }
      reviews(last:30){ nodes{ author{login __typename} state submittedAt } }
      comments(last:30){ nodes{ author{login __typename} body createdAt } }
    }
  }
}
"""


def search_open_prs(author: str, *, run: Callable | None = None) -> list[dict]:
    """Open PRs authored by `author`, via `gh search prs`. Returns a list of
    {repo, number, title, url, updatedAt}. Raises RuntimeError on gh failure."""
    if run is None:
        run = subprocess.run
    cmd = [
        "gh",
        "search",
        "prs",
        f"--author={author}",
        "--state=open",
        "--limit",
        "200",
        "--json",
        "repository,number,title,url,updatedAt",
    ]
    result = run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip() or f"gh exited {result.returncode}"
        )
    rows = json.loads(result.stdout or "[]")
    out = []
    for r in rows:
        repo = (r.get("repository") or {}).get("nameWithOwner")
        if not repo:
            continue
        out.append(
            {
                "repo": repo,
                "number": r.get("number"),
                "title": r.get("title"),
                "url": r.get("url"),
                "updatedAt": r.get("updatedAt"),
            }
        )
    return out


def graphql_pr(repo: str, number: int, *, run: Callable | None = None) -> dict:
    """Fetch one PR's review state via GraphQL. Returns the pullRequest dict.
    Raises RuntimeError on gh/GraphQL failure."""
    if run is None:
        run = subprocess.run
    owner, name = repo.split("/", 1)
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={_GRAPHQL}",
        "-f",
        f"owner={owner}",
        "-f",
        f"repo={name}",
        "-F",
        f"number={number}",
    ]
    result = run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip() or f"gh exited {result.returncode}"
        )
    payload = json.loads(result.stdout or "{}")
    if payload.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in payload["errors"]))
    pr = ((payload.get("data") or {}).get("repository") or {}).get("pullRequest")
    if pr is None:
        raise RuntimeError("PR not found (null pullRequest)")
    return pr


def analyze_pr(meta: dict, *, run: Callable | None = None) -> dict:
    """Combine GraphQL fetch + classify for one PR. Never raises — failures are
    captured as {..., error, tier: 'error'} so the batch never partial-fails."""
    base = {
        "repo": meta["repo"],
        "number": meta["number"],
        "title": meta.get("title"),
        "url": meta.get("url"),
    }
    try:
        pr = graphql_pr(meta["repo"], meta["number"], run=run)
    except Exception as exc:  # noqa: BLE001 — tool boundary
        return {**base, "tier": "error", "error": f"{type(exc).__name__}: {exc}"}
    author_login = (pr.get("author") or {}).get("login")
    verdict = classify(pr, author_login)
    return {**base, **verdict}


# --------------------------------------------------------------------------- #
# orchestration + rendering (pure)                                            #
# --------------------------------------------------------------------------- #
def gather_prs(
    authors: list[str],
    repo_filter: str | None,
    *,
    run: Callable | None = None,
) -> tuple[list[dict], list[str]]:
    """Search all authors, dedupe by repo#number, apply optional repo filter.
    Returns (prs, errors) — errors is a list of human-readable notes for authors
    whose search failed."""
    seen: dict[str, dict] = {}
    errors: list[str] = []
    for author in authors:
        try:
            for pr in search_open_prs(author, run=run):
                key = f"{pr['repo']}#{pr['number']}"
                seen.setdefault(key, pr)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"search --author={author}: {type(exc).__name__}: {exc}")
    prs = list(seen.values())
    if repo_filter:
        prs = [p for p in prs if p["repo"] == repo_filter]
    prs.sort(key=lambda p: (p["repo"], p["number"]))
    return prs, errors


def _parallel(items: list, worker: Callable, max_workers: int) -> list:
    if not items:
        return []
    workers = max(1, min(max_workers, len(items)))
    results: list = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(worker, it): i for i, it in enumerate(items)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[i] = {"tier": "error", "error": f"{type(exc).__name__}: {exc}"}
    return results


TIER_RANK = {"red": 0, "yellow": 1, "green": 2, "error": 3}
TIER_EMOJI = {"red": "🔴", "yellow": "🟡", "green": "🟢", "error": "⚠️"}


def sort_rows(rows: list[dict]) -> list[dict]:
    """RED first, then by #unresolved desc, then most-recent activity."""
    return sorted(
        rows,
        key=lambda r: (
            TIER_RANK.get(r.get("tier"), 9),
            -(r.get("unresolved") or 0),
            r.get("last_days") if r.get("last_days") is not None else 1_000_000,
        ),
    )


def render_markdown(rows: list[dict], errors: list[str]) -> str:
    rows = sort_rows(rows)
    counts = {"red": 0, "yellow": 0, "green": 0, "error": 0}
    for r in rows:
        counts[r.get("tier", "error")] = counts.get(r.get("tier", "error"), 0) + 1

    out: list[str] = []
    out.append("# PR hygiene")
    out.append("")
    out.append(
        f"{TIER_EMOJI['red']} {counts['red']} needs action · "
        f"{TIER_EMOJI['yellow']} {counts['yellow']} awaiting merge · "
        f"{TIER_EMOJI['green']} {counts['green']} clean"
        + (
            f" · {TIER_EMOJI['error']} {counts['error']} unqueryable"
            if counts["error"]
            else ""
        )
    )
    out.append("")
    out.append("| | PR | Title | Unres | Last activity | Verdict |")
    out.append("|---|---|---|---|---|---|")
    for r in rows:
        tier = r.get("tier", "error")
        emoji = TIER_EMOJI.get(tier, "⚠️")
        pr_cell = f"[{r['repo']}#{r['number']}]({r.get('url')})"
        title = (r.get("title") or "").replace("|", "\\|")
        if len(title) > 60:
            title = title[:57] + "…"
        if tier == "error":
            out.append(
                f"| {emoji} | {pr_cell} | {title} |  |  | {r.get('error', 'error')} |"
            )
            continue
        unres = r.get("unresolved") or 0
        days = r.get("last_days")
        actor = r.get("last_actor") or "—"
        last = f"{actor}, {days}d ago" if days is not None else actor
        verdict = (r.get("verdict") or "").replace("|", "\\|")
        out.append(f"| {emoji} | {pr_cell} | {title} | {unres} | {last} | {verdict} |")
    out.append("")
    out.append(
        f"_{len(rows)} open PR(s) queried. "
        f"Bitbucket repos (e.g. igor2) are invisible to `gh` and not included._"
    )
    if errors:
        out.append("")
        out.append("**Could not query:**")
        for e in errors:
            out.append(f"- {e}")
    return "\n".join(out)


def has_red(rows: list[dict]) -> bool:
    return any(r.get("tier") == "red" for r in rows)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def run_cli(
    authors: list[str],
    repo_filter: str | None,
    as_json: bool,
    fail_on_red: bool,
    max_workers: int,
) -> int:
    log(f"[pr-hygiene] searching open PRs for: {', '.join(authors)}")
    prs, search_errors = gather_prs(authors, repo_filter, run=None)
    log(f"[pr-hygiene] {len(prs)} PR(s) after dedupe; querying review state…")
    rows = _parallel(prs, lambda m: analyze_pr(m, run=None), max_workers)
    query_errors = [
        f"{r.get('repo')}#{r.get('number')}: {r.get('error')}"
        for r in rows
        if r.get("tier") == "error"
    ]
    all_errors = search_errors + query_errors

    if as_json:
        payload = {
            "prs": sort_rows(rows),
            "counts": {
                t: sum(1 for r in rows if r.get("tier") == t)
                for t in ("red", "yellow", "green", "error")
            },
            "errors": all_errors,
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        sys.stdout.write(render_markdown(rows, all_errors) + "\n")

    return 1 if (fail_on_red and has_red(rows)) else 0


def _build_app():  # pragma: no cover — thin Typer wrapper
    import typer

    app = typer.Typer(
        add_completion=False,
        help="Surface open PRs with genuinely unaddressed review feedback.",
    )

    @app.callback(invoke_without_command=True)
    def cli(
        ctx: typer.Context,
        author: list[str] = typer.Option(
            None,
            "--author",
            help=f"GitHub author to search (repeatable). Default: {', '.join(DEFAULT_AUTHORS)}.",
        ),
        repo: str = typer.Option(
            None, "--repo", help="Filter to a single repo (owner/name)."
        ),
        as_json: bool = typer.Option(
            False, "--json", help="Emit machine-readable JSON instead of markdown."
        ),
        no_fail: bool = typer.Option(
            False, "--no-fail", help="Always exit 0 (don't gate on 🔴)."
        ),
        max_workers: int = typer.Option(
            DEFAULT_MAX_WORKERS, "--max-workers", min=1, help="Parallel gh calls."
        ),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        authors = author or DEFAULT_AUTHORS
        raise typer.Exit(
            run_cli(
                authors, repo, as_json, fail_on_red=not no_fail, max_workers=max_workers
            )
        )

    return app


def main() -> None:
    _build_app()()


if __name__ == "__main__":
    main()
