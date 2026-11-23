"""Dev-server census: classify, fold wrappers, parse listeners and swap, and
the report-only findings. Pure functions over fixture text; the one I/O test
mocks every read. Run from this directory: python3 -m unittest"""

import unittest
from unittest import mock

import machine_doctor as md
from md_probe import (
    DevServer,
    ProcSample,
    fmt_age,
    parse_lsof_listeners,
    parse_ss_listeners,
    parse_status_mem,
)
from profiles import (
    DEV_SERVERS_WARN,
    STALE_DEV_SERVER_S,
    dev_server_findings,
    fold_wrappers,
    server_kind,
    server_target,
)

GB = 1024 * 1024


def _srv(pid, kind="jekyll", etime=60, rss=100 * 1024, swap=0, **kw):
    return DevServer(pid=pid, kind=kind, etime_s=etime, rss_kb=rss, swap_kb=swap, **kw)


def _msgs(findings):
    return " | ".join(f.message for f in findings)


class TestServerKind(unittest.TestCase):
    def test_kinds(self):
        cases = [
            ("bundle", "/h/.bundle/bin/jekyll server --port 4053", "jekyll"),
            ("ruby", "jekyll serve --livereload", "jekyll"),
            (
                "node",
                "node /h/lib/node_modules/lavish-axi/dist/server.mjs server --port 4387",
                "lavish",
            ),
            ("node", "node /h/bin/lavish-axi poll /w/review.html", "lavish-poll"),
            ("python3", "python3 -m http.server 8763", "http.server"),
            ("python", "/h/env/bin/python /w/app/serve.py 8931", "serve.py"),
            ("uv", "uv run --script /w/app/serve.py 8931", "serve.py"),
        ]
        for comm, cl, want in cases:
            self.assertEqual(server_kind(comm, cl), want, cl)

    def test_not_servers(self):
        cases = [
            ("bash", "bash -c 'jekyll serve --port 4000'"),
            ("just", "just jekyll-serve 4053 35853"),
            ("ruby", "jekyll build"),
            ("node", "node /h/bin/lavish-axi open /w/x.html"),
            ("python3", "python3 /w/tools/other.py"),
            ("zsh", "zsh -c 'python3 -m http.server'"),
        ]
        for comm, cl in cases:
            self.assertIsNone(server_kind(comm, cl), cl)


class TestServerTarget(unittest.TestCase):
    def test_serve_py_uses_script_dir(self):
        self.assertEqual(
            server_target("serve.py", "python /w/wt/feature/serve.py 1", "/elsewhere"),
            "/w/wt/feature",
        )

    def test_lavish_poll_relative_and_absolute(self):
        self.assertEqual(
            server_target(
                "lavish-poll", "node lavish-axi poll .lavish/a.html", "/w/repo"
            ),
            "/w/repo/.lavish",
        )
        self.assertEqual(
            server_target("lavish-poll", "node lavish-axi poll /x/y/a.html", "/w/repo"),
            "/x/y",
        )

    def test_jekyll_uses_cwd(self):
        self.assertEqual(server_target("jekyll", "jekyll serve", "/w/blog"), "/w/blog")


class TestFoldWrappers(unittest.TestCase):
    def test_uv_parent_folds_into_python_child(self):
        parent = _srv(10, "serve.py", etime=500, rss=50 * 1024)
        child = _srv(
            11, "serve.py", etime=499, rss=20 * 1024, swap=30 * 1024, ports=(8931,)
        )
        other = _srv(12, "jekyll", ports=(4000,))
        got = fold_wrappers([parent, child, other], {11: 10, 10: 1, 12: 1})
        self.assertEqual([s.pid for s in got], [11, 12])
        self.assertEqual(got[0].rss_kb, 70 * 1024)
        self.assertEqual(got[0].swap_kb, 30 * 1024)
        self.assertEqual(got[0].ports, (8931,))
        self.assertEqual(got[0].etime_s, 500)

    def test_different_kinds_do_not_fold(self):
        a, b = _srv(10, "http.server"), _srv(11, "serve.py")
        self.assertEqual(len(fold_wrappers([a, b], {11: 10})), 2)


class TestParsers(unittest.TestCase):
    def test_status_mem_reads_swap(self):
        text = "Name:\truby\nVmRSS:\t    1024 kB\nVmSwap:\t  227328 kB\n"
        self.assertEqual(parse_status_mem(text), (1024, 227328))
        self.assertEqual(parse_status_mem("Name:\tkthreadd\n"), (0, 0))

    def test_ss_listeners(self):
        text = (
            'LISTEN 0 4096 0.0.0.0:4053 0.0.0.0:* users:(("bundle",pid=929702,fd=9))\n'
            'LISTEN 0 100  0.0.0.0:35853 0.0.0.0:* users:(("bundle",pid=929702,fd=13))\n'
            'LISTEN 0 5    [::1]:8080 [::]:* users:(("python",pid=7,fd=3),("python",pid=8,fd=3))\n'
            "LISTEN 0 5    127.0.0.1:631 0.0.0.0:*\n"
        )
        self.assertEqual(
            parse_ss_listeners(text), {929702: [4053, 35853], 7: [8080], 8: [8080]}
        )

    def test_lsof_listeners(self):
        text = "p501\nf9\nn*:4000\nf10\nn127.0.0.1:35729\np77\nn[::1]:8080\n"
        self.assertEqual(parse_lsof_listeners(text), {501: [4000, 35729], 77: [8080]})

    def test_fmt_age(self):
        self.assertEqual(fmt_age(14 * 86400 + 4 * 3600), "14d4h")
        self.assertEqual(fmt_age(3 * 3600 + 5 * 60), "3h05m")
        self.assertEqual(fmt_age(59), "0m")


class TestFindings(unittest.TestCase):
    def test_quiet_box_has_no_findings(self):
        self.assertEqual(
            dev_server_findings([_srv(1), _srv(2, "serve.py")], 16 * GB), []
        )

    def test_count_ignores_pollers(self):
        polls = [_srv(i, "lavish-poll", rss=1024) for i in range(20)]
        self.assertEqual(dev_server_findings(polls, 64 * GB), [])
        many = [_srv(i, rss=1024) for i in range(DEV_SERVERS_WARN)]
        self.assertIn(
            f"{DEV_SERVERS_WARN} dev servers running",
            _msgs(dev_server_findings(many, 64 * GB)),
        )

    def test_memory_counts_swap(self):
        # RSS alone (47 x 1MB) looks harmless; swap is where previews hide.
        swapped = [_srv(i, rss=1024, swap=160 * 1024) for i in range(3)]
        got = _msgs(dev_server_findings(swapped, 3 * GB))
        self.assertIn("in swap", got)

    def test_old_servers_name_branch_and_sigint(self):
        old = _srv(
            929702,
            etime=STALE_DEV_SERVER_S + 3600,
            repo="/w/blog/.worktrees/feature-x",
            branch="feature-x",
        )
        got = dev_server_findings([old, _srv(5)], 64 * GB)
        self.assertEqual([f.severity for f in got], ["warn"])
        msg = got[0].message
        self.assertIn("pid=929702", msg)
        self.assertIn("feature-x@feature-x", msg)
        self.assertIn("SIGINT", msg)
        self.assertNotIn("pid=5 ", msg)


class TestReadDevServersLinux(unittest.TestCase):
    """Every read mocked: never touches the real process table."""

    def test_census(self):
        procs = [
            ProcSample(pid=10, ppid=1, comm="uv", cpu_pct=None, rss_kb=50, etime_s=900),
            ProcSample(
                pid=11, ppid=10, comm="python", cpu_pct=None, rss_kb=60, etime_s=899
            ),
            ProcSample(
                pid=20, ppid=1, comm="bash", cpu_pct=None, rss_kb=5, etime_s=900
            ),
        ]
        cmdlines = {
            10: "uv run --script /w/app/serve.py 8931",
            11: "/env/bin/python /w/app/serve.py 8931",
            20: "bash -c 'uv run --script /w/app/serve.py 8931'",
        }
        status = {
            "/proc/10/status": "VmRSS:\t 40000 kB\nVmSwap:\t 1000 kB\n",
            "/proc/11/status": "VmRSS:\t 4000 kB\nVmSwap:\t 60000 kB\n",
        }
        with (
            mock.patch.object(md, "IS_DARWIN", False),
            mock.patch.object(
                md, "_cmdlines", lambda pids: {p: cmdlines[p] for p in pids}
            ),
            mock.patch.object(md, "_listen_ports", lambda pids: {11: [8931]}),
            mock.patch.object(md, "_cwds", lambda pids: {p: "/w/app" for p in pids}),
            mock.patch.object(md, "_read", lambda path: status.get(path, "")),
            mock.patch.object(md, "_git_where", lambda path: ("/w/app", "feature-x")),
        ):
            got = md.read_dev_servers(procs)
        self.assertEqual(len(got), 1)
        s = got[0]
        self.assertEqual((s.pid, s.kind, s.ports), (11, "serve.py", (8931,)))
        self.assertEqual((s.rss_kb, s.swap_kb), (44000, 61000))
        self.assertEqual((s.repo, s.branch), ("/w/app", "feature-x"))


if __name__ == "__main__":
    unittest.main()
