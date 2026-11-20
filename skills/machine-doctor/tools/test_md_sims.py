"""md_sims on a fake CoreSimulator Devices tree. The safety rules come from the
cleanup's contract: only a Shutdown device with no launchd_sim loses its poster
store, a symlinked store is never followed, and nothing else in the device goes.

Run from this directory: python3 -m unittest
"""

import os
import plistlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import md_sims
from profiles import SIM_POSTERS_WARN_KB, HostFacts, sim_findings

IPAD = "9B70F326-056A-444F-8C4A-246ACBCC2D32"
PHONE = "698B8BD5-DEFA-43AD-B8AF-8844F8E9D446"
WATCH = "6E15CD1E-9839-4490-9655-7CD30970CDBB"


def IDLE() -> set[str]:  # ps ran; no launchd_sim at all
    return set()


def set_state(dev: Path, state: int, udid: str | None = None) -> None:
    plist_path = dev / "device.plist"
    with plist_path.open("rb") as fh:
        plist = plistlib.load(fh)
    plist["state"] = state
    if udid:
        plist["UDID"] = udid
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)


def make_device(
    root: Path, udid: str, name: str, state: int, poster_bytes: int
) -> Path:
    dev = root / udid
    (dev / "data/Library/Preferences").mkdir(parents=True)
    (dev / "data/Library/Preferences/keep.plist").write_bytes(b"x" * 100)
    with (dev / "device.plist").open("wb") as fh:
        plistlib.dump(
            {
                "UDID": udid,
                "name": name,
                "state": state,
                "runtime": "com.apple.CoreSimulator.SimRuntime.iOS-27-0",
            },
            fh,
        )
    if poster_bytes:
        snap = dev / md_sims.POSTER_STORE / "61/Extensions/com.apple.MercuryPoster"
        snap.mkdir(parents=True)
        (snap / "Composite.atx").write_bytes(os.urandom(poster_bytes))
    return dev


class TestSims(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_devices_biggest_store_first(self):
        make_device(self.root, PHONE, "iPhone 17", 1, 200_000)
        ipad = make_device(self.root, IPAD, "iPad Pro 11-inch (M5)", 3, 900_000)
        (self.root / "not-a-device").mkdir()
        devs = md_sims.read_devices(self.root)
        self.assertEqual([d.name for d in devs], ["iPad Pro 11-inch (M5)", "iPhone 17"])
        self.assertEqual(devs[0].state_name, "Booted")
        self.assertEqual(devs[0].runtime, "iOS-27-0")
        self.assertEqual(devs[0].path, ipad)
        # Oracle: du(1) over the same store.
        du = subprocess.run(
            ["/usr/bin/du", "-sk", str(ipad / md_sims.POSTER_STORE)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(devs[0].poster_kb, int(du.stdout.split()[0]))

    def test_size_counts_directory_blocks_like_du(self):
        # btrfs/tmpfs report 0 blocks for a directory, ext4/APFS do not: fake
        # ext4's 4 KB per directory so the count is exercised on any test box.
        store = make_device(self.root, PHONE, "iPhone 17", 1, 0) / "store"
        (store / "a/b").mkdir(parents=True)
        (store / "a/b/f").write_bytes(b"")
        os.symlink(store / "a", store / "link-to-a")
        real_lstat = os.lstat

        def lstat(p):  # 8 x 512-byte blocks per real directory, as on ext4
            st = real_lstat(p)
            is_dir = stat.S_ISDIR(st.st_mode)
            return SimpleNamespace(
                st_mode=st.st_mode, st_blocks=8 if is_dir else st.st_blocks
            )

        with mock.patch.object(md_sims.os, "lstat", lstat):
            got = md_sims.dir_kb(store)
        # store, a, a/b at 4 KB each, plus the link's own inode (du counts it)
        # without following it into a again.
        link_kb = real_lstat(store / "link-to-a").st_blocks // 2
        self.assertEqual(got, 3 * 4 + link_kb)

    def test_prunes_only_a_shut_down_idle_device(self):
        off = make_device(self.root, PHONE, "iPhone 17", 1, 300_000)
        on = make_device(self.root, IPAD, "iPad", 3, 300_000)
        dev, kb, why = md_sims.prune_device(off, IDLE)
        self.assertIsNone(why)
        self.assertGreater(kb, 0)
        self.assertFalse((off / md_sims.POSTER_STORE).exists())
        self.assertTrue((off / "data/Library/Preferences/keep.plist").exists())
        self.assertTrue((off / "device.plist").exists())
        _, kb, why = md_sims.prune_device(on, IDLE)
        self.assertEqual((kb, why), (0, "state is Booted, not Shutdown"))
        self.assertTrue((on / md_sims.POSTER_STORE).is_dir())

    def test_rechecks_state_just_before_deleting(self):
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 100_000)

        def boots_meanwhile():  # someone boots it after the size walk
            set_state(dev, 2)
            return set()

        _, kb, why = md_sims.prune_device(dev, boots_meanwhile)
        self.assertEqual((kb, why), (0, "state is Booting, not Shutdown"))
        self.assertTrue((dev / md_sims.POSTER_STORE).is_dir())

    def test_refuses_when_ps_fails(self):
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 100_000)
        _, kb, why = md_sims.prune_device(dev, lambda: None)
        self.assertEqual(kb, 0)
        self.assertIn("could not re-check", why)
        self.assertTrue((dev / md_sims.POSTER_STORE).is_dir())

    def test_keeps_a_device_whose_launchd_sim_still_runs(self):
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 100_000)
        ps = (
            "/Library/Developer/PrivateFrameworks/CoreSimulator.framework/Resources/bin/"
            f"launchd_sim /Users/x/Library/Developer/CoreSimulator/Devices/{PHONE}/data/"
            "var/run/launchd_bootstrap.plist\n"
            f"zsh -c grep {WATCH}\n"  # mentions a UDID but isn't launchd_sim
        )
        running = md_sims.running_udids(ps)
        self.assertEqual(running, {PHONE})
        _, kb, why = md_sims.prune_device(dev, lambda: running)
        self.assertEqual((kb, why), (0, "launchd_sim is running for it"))
        self.assertTrue((dev / md_sims.POSTER_STORE).is_dir())

    def test_never_follows_a_symlinked_store(self):
        elsewhere = self._precious()
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 0)
        store = dev / md_sims.POSTER_STORE
        store.parent.mkdir(parents=True, exist_ok=True)
        store.symlink_to(elsewhere)
        self.assertEqual(md_sims.read_device(dev).poster_kb, 0)
        _, kb, why = md_sims.prune_device(dev, IDLE)
        self.assertEqual((kb, why), (0, "the store's path goes through a symlink"))
        self.assertTrue((elsewhere / "file").exists())

    def test_never_follows_a_symlinked_parent(self):
        # <device>/data -> another device's data: its store must survive.
        other = make_device(self.root, IPAD, "iPad", 1, 100_000)
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 0)
        shutil.rmtree(dev / "data")
        (dev / "data").symlink_to(other / "data")
        _, kb, why = md_sims.prune_device(dev, IDLE)
        self.assertEqual((kb, why), (0, "the store's path goes through a symlink"))
        self.assertTrue((other / md_sims.POSTER_STORE).is_dir())

    def test_refuses_a_folder_whose_plist_names_another_udid(self):
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 100_000)
        set_state(dev, 1, udid=IPAD)
        _, kb, why = md_sims.prune_device(dev, IDLE)
        self.assertEqual(kb, 0)
        self.assertIn("folder is", why)

    def test_device_without_a_store(self):
        dev = make_device(self.root, WATCH, "Apple Watch", 1, 0)
        self.assertEqual(md_sims.prune_device(dev, IDLE)[2], "no poster store")

    def _precious(self) -> Path:
        elsewhere = self.root / "precious"
        elsewhere.mkdir()
        (elsewhere / "file").write_bytes(b"y" * 5000)
        return elsewhere


class TestSimPosterFinding(unittest.TestCase):
    def test_warns_past_threshold_naming_the_biggest(self):
        small = {"iPhone 17e": 20 * 1024}
        self.assertEqual(sim_findings(HostFacts(sim_posters_kb=small)), [])
        big = {"iPad Pro 11-inch (M5)": SIM_POSTERS_WARN_KB, "iPhone 17": 1024 * 1024}
        (f,) = sim_findings(HostFacts(sim_posters_kb=big))
        self.assertEqual(f.severity, "warn")
        self.assertIn("hold 6.0GB (most: iPad Pro 11-inch (M5) 5.0GB)", f.message)
        self.assertIn("sims --prune", f.message)


class TestPruneAll(unittest.TestCase):
    """The watch --prune-sims pass: shut-down stores go, booted ones stay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clears_shut_down_keeps_booted_and_running(self):
        off = make_device(self.root, PHONE, "iPhone 17", 1, 300_000)
        booted = make_device(self.root, IPAD, "iPad", 3, 300_000)
        live = make_device(self.root, WATCH, "Watch", 1, 300_000)
        done = md_sims.prune_all(self.root, lambda: {WATCH})
        why = {d.name: r for d, _kb, r in done}
        self.assertEqual(
            why,
            {
                "iPhone 17": None,
                "iPad": "state is Booted, not Shutdown",
                "Watch": "launchd_sim is running for it",
            },
        )
        self.assertFalse((off / md_sims.POSTER_STORE).exists())
        self.assertTrue((booted / md_sims.POSTER_STORE).is_dir())
        self.assertTrue((live / md_sims.POSTER_STORE).is_dir())

    def test_watch_pass_reports_freed_and_survives_errors(self):
        import machine_doctor as md

        import contextlib
        import io

        phone = make_device(self.root, PHONE, "iPhone 17", 1, 300_000)
        make_device(self.root, WATCH, "Watch", 3, 300_000)  # booted: kept quietly
        # Oracle: du(1) before the pass.
        du = subprocess.run(
            ["/usr/bin/du", "-sk", str(phone / md_sims.POSTER_STORE)],
            capture_output=True,
            text=True,
            check=True,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            freed = md._watch_prune_sims(self.root, IDLE)
        self.assertEqual(freed, int(du.stdout.split()[0]))
        self.assertFalse((phone / md_sims.POSTER_STORE).exists())
        self.assertIn("cleared simulator wallpaper caches: iPhone 17", out.getvalue())
        self.assertNotIn("Watch", out.getvalue())

        ipad = make_device(self.root, IPAD, "iPad", 1, 300_000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            freed = md._watch_prune_sims(self.root, lambda: None)  # ps failed
        self.assertEqual(freed, 0)
        self.assertTrue((ipad / md_sims.POSTER_STORE).is_dir())
        self.assertIn("kept iPad: could not re-check", out.getvalue())

        def boom():
            raise RuntimeError("ps exploded")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(md._watch_prune_sims(self.root, boom), 0)  # no raise
        self.assertTrue((ipad / md_sims.POSTER_STORE).is_dir())


if __name__ == "__main__":
    unittest.main()
