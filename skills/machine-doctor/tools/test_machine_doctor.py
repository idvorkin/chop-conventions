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


if __name__ == "__main__":
    unittest.main()
