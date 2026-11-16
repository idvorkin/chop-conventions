"""md_sims on a fake CoreSimulator Devices tree. The safety rules come from the
cleanup's contract: only a Shutdown device with no launchd_sim loses its poster
store, a symlinked store is never followed, and nothing else in the device goes.

Run from this directory: python3 -m unittest
"""

import os
import plistlib
import tempfile
import unittest
from pathlib import Path

import md_sims
from profiles import SIM_POSTERS_WARN_KB, HostFacts, sim_findings

IPAD = "9B70F326-056A-444F-8C4A-246ACBCC2D32"
PHONE = "698B8BD5-DEFA-43AD-B8AF-8844F8E9D446"
WATCH = "6E15CD1E-9839-4490-9655-7CD30970CDBB"


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
        make_device(self.root, IPAD, "iPad Pro 11-inch (M5)", 3, 900_000)
        (self.root / "not-a-device").mkdir()
        devs = md_sims.read_devices(self.root)
        self.assertEqual([d.name for d in devs], ["iPad Pro 11-inch (M5)", "iPhone 17"])
        self.assertEqual(devs[0].state_name, "Booted")
        self.assertEqual(devs[0].runtime, "iOS-27-0")
        # du-style KB of a 900,000-byte file: at least the bytes, at most one extra block.
        self.assertGreaterEqual(devs[0].poster_kb, 900_000 // 1024)

    def test_prunes_only_a_shut_down_idle_device(self):
        off = make_device(self.root, PHONE, "iPhone 17", 1, 300_000)
        on = make_device(self.root, IPAD, "iPad", 3, 300_000)
        dev, kb, why = md_sims.prune_device(off, running=set())
        self.assertIsNone(why)
        self.assertGreater(kb, 0)
        self.assertFalse((off / md_sims.POSTER_STORE).exists())
        self.assertTrue((off / "data/Library/Preferences/keep.plist").exists())
        self.assertTrue((off / "device.plist").exists())
        _, kb, why = md_sims.prune_device(on, running=set())
        self.assertEqual((kb, why), (0, "state is Booted, not Shutdown"))
        self.assertTrue((on / md_sims.POSTER_STORE).is_dir())

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
        _, kb, why = md_sims.prune_device(dev, running)
        self.assertEqual((kb, why), (0, "launchd_sim is running for it"))
        self.assertTrue((dev / md_sims.POSTER_STORE).is_dir())

    def test_never_follows_a_symlinked_store(self):
        elsewhere = self.root / "precious"
        elsewhere.mkdir()
        (elsewhere / "file").write_bytes(b"y" * 5000)
        dev = make_device(self.root, PHONE, "iPhone 17", 1, 0)
        store = dev / md_sims.POSTER_STORE
        store.parent.mkdir(parents=True, exist_ok=True)
        store.symlink_to(elsewhere)
        self.assertEqual(md_sims.dir_kb(store), 0)
        _, kb, why = md_sims.prune_device(dev, set())
        self.assertEqual((kb, why), (0, "the store is a symlink"))
        self.assertTrue((elsewhere / "file").exists())

    def test_device_without_a_store(self):
        dev = make_device(self.root, WATCH, "Apple Watch", 1, 0)
        self.assertEqual(md_sims.prune_device(dev, set())[2], "no poster store")


class TestSimPosterFinding(unittest.TestCase):
    def test_warns_past_threshold_naming_the_biggest(self):
        small = {"iPhone 17e": 20 * 1024}
        self.assertEqual(sim_findings(HostFacts(sim_posters_kb=small)), [])
        big = {"iPad Pro 11-inch (M5)": SIM_POSTERS_WARN_KB, "iPhone 17": 1024 * 1024}
        (f,) = sim_findings(HostFacts(sim_posters_kb=big))
        self.assertEqual(f.severity, "warn")
        self.assertIn("hold 6.0GB (most: iPad Pro 11-inch (M5) 5.0GB)", f.message)
        self.assertIn("sims --prune", f.message)


if __name__ == "__main__":
    unittest.main()
