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


if __name__ == "__main__":
    unittest.main()
