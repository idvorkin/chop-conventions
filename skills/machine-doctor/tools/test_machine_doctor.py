"""Unit tests for machine_doctor's OrbStack gate: snapshot must never run an
orb or docker command that can start a stopped OrbStack VM. Every subprocess
call is mocked — no real orb/docker invocation.

Run from this directory: python3 -m unittest
"""

import unittest
from unittest import mock

import machine_doctor as md


class TestOrbStackGate(unittest.TestCase):
    def _patch(self, *, installed, status="Running\n", darwin=True):
        calls = []

        def fake_run(cmd, timeout=20):
            calls.append(cmd)
            if cmd[:2] == ["orb", "status"]:
                return status
            if cmd[:3] == ["orb", "config", "show"]:
                return "cpu: 8\nmemory_mib: 12288\n"
            if cmd[:2] == ["docker", "stats"]:
                return "web cpu=1.0% mem=1GiB / 12GiB\n"
            raise AssertionError(f"unexpected command {cmd}")

        def fake_which(name):
            return f"/usr/local/bin/{name}" if name in installed else None

        for p in (
            mock.patch.object(md, "_run", fake_run),
            mock.patch.object(md.shutil, "which", fake_which),
            mock.patch.object(md, "IS_DARWIN", darwin),
        ):
            p.start()
            self.addCleanup(p.stop)
        return calls

    def test_running_vm_reports_cap_and_containers(self):
        calls = self._patch(installed={"orb", "docker"})
        self.assertEqual(md._orbstack_mem_mib(), {"OrbStack VM": 12288})
        self.assertEqual(md.read_containers(), ["web cpu=1.0% mem=1GiB / 12GiB"])
        self.assertIn(["orb", "status"], calls)

    def test_stopped_vm_runs_only_orb_status(self):
        calls = self._patch(installed={"orb", "docker"}, status="Stopped\n")
        self.assertEqual(md._orbstack_mem_mib(), {})
        self.assertEqual(md.read_containers(), [])
        self.assertEqual({tuple(c) for c in calls}, {("orb", "status")})

    def test_no_orb_keeps_docker_behavior(self):
        calls = self._patch(installed={"docker"})
        self.assertEqual(md._orbstack_mem_mib(), {})
        self.assertEqual(md.read_containers(), ["web cpu=1.0% mem=1GiB / 12GiB"])
        self.assertFalse(any(c[0] == "orb" for c in calls))

    def test_linux_docker_not_gated_on_orb(self):
        calls = self._patch(installed={"orb", "docker"}, status="", darwin=False)
        self.assertEqual(md.read_containers(), ["web cpu=1.0% mem=1GiB / 12GiB"])
        self.assertFalse(any(c[0] == "orb" for c in calls))

    def test_linux_guest_skips_vm_cap(self):
        calls = self._patch(installed={"orb", "docker"}, darwin=False)
        self.assertEqual(md._orbstack_mem_mib(), {})
        self.assertEqual(calls, [])


class TestSendAlert(unittest.TestCase):
    def _popen(self, *, rc=0, err="", timeout=False):
        p = mock.MagicMock(pid=4242, returncode=rc)
        if timeout:
            p.communicate.side_effect = md.subprocess.TimeoutExpired("x", 15)
        else:
            p.communicate.return_value = ("", err)
        return p

    def test_success_feeds_stdin(self):
        p = self._popen()
        with mock.patch.object(md.subprocess, "Popen", return_value=p) as popen:
            self.assertTrue(md.send_alert("notify", "hot"))
        p.communicate.assert_called_once_with("hot", timeout=15)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_nonzero_exit_is_failure(self):
        with mock.patch.object(
            md.subprocess, "Popen", return_value=self._popen(rc=1, err="nope")
        ):
            self.assertFalse(md.send_alert("notify", "hot"))

    def test_timeout_kills_the_process_group(self):
        p = self._popen(timeout=True)
        with (
            mock.patch.object(md.subprocess, "Popen", return_value=p),
            mock.patch.object(md.os, "killpg") as killpg,
        ):
            self.assertFalse(md.send_alert("notify", "hot"))
        killpg.assert_called_once_with(4242, md.signal.SIGKILL)

    def test_timeout_survives_an_already_exited_group(self):
        p = self._popen(timeout=True)
        with (
            mock.patch.object(md.subprocess, "Popen", return_value=p),
            mock.patch.object(md.os, "killpg", side_effect=ProcessLookupError),
        ):
            self.assertFalse(md.send_alert("notify", "hot"))

    def test_unstartable_command_is_failure(self):
        with mock.patch.object(md.subprocess, "Popen", side_effect=OSError("no shell")):
            self.assertFalse(md.send_alert("notify", "hot"))


class TestLinuxAppLabels(unittest.TestCase):
    def test_interpreters_named_by_script(self):
        procs = [
            md.ProcSample(1, 0, "python3", 0.0, 100, 1),
            md.ProcSample(2, 0, "python3", 0.0, 100, 1),
        ]
        cmds = {1: "python3 serve.py", 2: "python3 worker.py"}
        with (
            mock.patch.object(md, "IS_DARWIN", False),
            mock.patch.object(md, "_cmdlines", return_value=cmds),
        ):
            got = md._with_app_labels(procs)
        self.assertEqual(
            [p.app for p in got], ["python3:serve.py", "python3:worker.py"]
        )

    def test_xdist_workers_count_toward_pytest(self):
        procs = [
            md.ProcSample(10, 0, "python3", 0.0, 100, 1),
            md.ProcSample(11, 10, "python3", 0.0, 100, 1),
        ]
        cmds = {10: "python3 -m pytest -n 2", 11: "python3 -c from xdist import x"}
        with (
            mock.patch.object(md, "IS_DARWIN", False),
            mock.patch.object(md, "_cmdlines", return_value=cmds),
        ):
            got = md._with_app_labels(procs)
        self.assertEqual([p.app for p in got], ["pytest", "pytest"])


class TestReadLoad1(unittest.TestCase):
    def test_darwin_parses_sysctl(self):
        with (
            mock.patch.object(md, "IS_DARWIN", True),
            mock.patch.object(
                md, "_run", lambda cmd, timeout=20: "{ 1.50 2.00 3.00 }\n"
            ),
        ):
            self.assertEqual(md.read_load1(), 1.5)

    def test_empty_sysctl_output_does_not_raise(self):
        # _run returns "" when sysctl times out; that used to be an IndexError
        # that ended `watch`.
        with (
            mock.patch.object(md, "IS_DARWIN", True),
            mock.patch.object(md, "_run", lambda cmd, timeout=20: ""),
            mock.patch.object(md.os, "getloadavg", lambda: (2.349, 1.0, 1.0)),
        ):
            self.assertEqual(md.read_load1(), 2.35)

    def test_unreadable_everywhere_is_minus_one(self):
        def no_load():
            raise OSError("unobtainable")

        with (
            mock.patch.object(md, "IS_DARWIN", True),
            mock.patch.object(md, "_run", lambda cmd, timeout=20: ""),
            mock.patch.object(md.os, "getloadavg", no_load),
        ):
            self.assertEqual(md.read_load1(), -1.0)


class TestWatchPruneSimsDefaults(unittest.TestCase):
    def test_defaults_resolve_at_call_time(self):
        """prune_all is mocked: a no-argument call must never reach the real
        simulator caches from a test."""
        fake_root = object()

        def fake_running():
            return set()

        with (
            mock.patch.object(md.md_sims, "DEVICES_ROOT", fake_root),
            mock.patch.object(md, "_sims_running", fake_running),
            mock.patch.object(md.md_sims, "prune_all", return_value=[]) as prune,
        ):
            self.assertEqual(md._watch_prune_sims(), 0)
        prune.assert_called_once_with(fake_root, fake_running)


try:
    from typer.testing import CliRunner
except ImportError:  # system python3; `just test` runs with typer
    CliRunner = None


@unittest.skipIf(CliRunner is None, "typer not installed")
class TestMemCommand(unittest.TestCase):
    """Regression: `mem` once called walk_procs({}, 0.0, _uptime_s()) after the
    signature dropped to (prev, prev_t) and crashed with TypeError. autospec
    holds every call to the real signature."""

    def _invoke(self, cmd, *, darwin):
        procs = [md.ProcSample(1, 0, "jekyll", None, 200 * 1024, 60, app="jekyll")]
        meminfo = (
            "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
        )
        with (
            mock.patch.object(md, "IS_DARWIN", darwin),
            mock.patch.object(
                md, "walk_procs", autospec=True, return_value=(procs, {}, [], 0.0)
            ) as walk,
            mock.patch.object(md, "_cmdline", lambda pid: "jekyll serve"),
            mock.patch.object(md, "_cmdlines", lambda pids: {p: "x" for p in pids}),
            mock.patch.object(md, "_read", lambda path: meminfo),
            mock.patch.object(md, "read_mem", lambda: md.parse_meminfo(meminfo)),
            mock.patch.object(md, "read_dev_servers", lambda procs: []),
        ):
            result = CliRunner().invoke(md._build_app(), cmd)
        self.assertIsNone(result.exception, result.output)
        self.assertEqual(result.exit_code, 0, result.output)
        walk.assert_called()
        return result.output

    def test_mem_linux(self):
        self.assertIn("TOTAL", self._invoke(["mem"], darwin=False))

    def test_mem_darwin(self):
        self.assertIn("TOTAL", self._invoke(["mem"], darwin=True))

    def test_servers(self):
        self.assertIn("dev servers: none", self._invoke(["servers"], darwin=False))


if __name__ == "__main__":
    unittest.main()
