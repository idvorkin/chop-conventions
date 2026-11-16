"""iOS/watchOS simulator wallpaper caches: measure them and prune them safely.

On iOS 27 simulators every boot adds ~300 MB of Lock/Home Screen wallpaper
snapshots under PRBPosterExtensionDataStore, and nothing evicts them: 17.8 GB
on one iPad simulator after a few weeks of test-ladder boots. PosterBoard
rebuilds the store on the next boot, so removing it from a shut-down device
loses nothing but those snapshots. The device, its UDID and its apps stay.

Filesystem only, rooted at a Devices directory, so tests run on a fake tree.
Which simulators are running comes in from the caller.
"""

import os
import plistlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

DEVICES_ROOT = Path("~/Library/Developer/CoreSimulator/Devices").expanduser()
POSTER_STORE = Path("data/Library/Application Support/PRBPosterExtensionDataStore")
# device.plist `state`: simctl's SimDeviceState. Only Shutdown is safe to prune.
STATE_NAMES = {
    0: "Creating",
    1: "Shutdown",
    2: "Booting",
    3: "Booted",
    4: "ShuttingDown",
}
SHUTDOWN = 1
UDID_RE = re.compile(r"[0-9A-F]{8}-(?:[0-9A-F]{4}-){3}[0-9A-F]{12}")


@dataclass(frozen=True)
class SimDevice:
    udid: str
    name: str
    runtime: str  # "iOS-27-0"
    state: int | None
    poster_kb: int

    @property
    def state_name(self) -> str:
        return STATE_NAMES.get(self.state, "?") if self.state is not None else "?"


def dir_kb(path: Path) -> int:
    """Disk use of a tree in KB, like `du -sk`, never following symlinks."""
    if path.is_symlink() or not path.is_dir():
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_blocks * 512
            except OSError:
                continue
    return total // 1024


def _read_plist(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}


def read_device(dev_dir: Path) -> SimDevice | None:
    """A device folder's identity and poster store size; None if not a device."""
    plist = _read_plist(dev_dir / "device.plist")
    if not plist.get("UDID"):
        return None
    state = plist.get("state")
    return SimDevice(
        udid=plist["UDID"],
        name=plist.get("name", "?"),
        runtime=str(plist.get("runtime", "")).rsplit(".", 1)[-1],
        state=state if isinstance(state, int) else None,
        poster_kb=dir_kb(dev_dir / POSTER_STORE),
    )


def read_devices(root: Path = DEVICES_ROOT) -> list[SimDevice]:
    """Every simulator device under root, biggest poster store first."""
    if not root.is_dir():
        return []
    devs = [d for p in sorted(root.iterdir()) if (d := read_device(p))]
    return sorted(devs, key=lambda d: d.poster_kb, reverse=True)


def running_udids(ps_commands: str) -> set[str]:
    """UDIDs of devices with a live launchd_sim, from `ps -axo command=` text.
    A device can say Shutdown in its plist while launchd_sim is still up."""
    return {
        u
        for ln in ps_commands.splitlines()
        if "launchd_sim" in ln
        for u in UDID_RE.findall(ln)
    }


def prune_refusal(dev: SimDevice, running: set[str], store: Path) -> str | None:
    """Why this device's poster store must not be removed now, or None if it may."""
    if dev.state != SHUTDOWN:
        return f"state is {dev.state_name}, not Shutdown"
    if dev.udid in running:
        return "launchd_sim is running for it"
    if store.is_symlink():
        return "the store is a symlink"
    if not store.is_dir():
        return "no poster store"
    return None


def prune_device(
    dev_dir: Path, running: set[str]
) -> tuple[SimDevice | None, int, str | None]:
    """Remove one device's poster store if it is safe. Re-reads the device's state
    right before deleting. Returns (device, freed KB, refusal reason or None)."""
    dev = read_device(dev_dir)
    if dev is None:
        return None, 0, "not a simulator device"
    store = dev_dir / POSTER_STORE
    reason = prune_refusal(dev, running, store)
    if reason:
        return dev, 0, reason
    shutil.rmtree(store)
    return dev, dev.poster_kb, None
