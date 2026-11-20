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
from collections.abc import Callable
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
    path: Path  # the device folder; prune acts on this, not on the plist's UDID

    @property
    def state_name(self) -> str:
        return STATE_NAMES.get(self.state, "?") if self.state is not None else "?"


def dir_kb(path: Path) -> int:
    """Disk use of a tree in KB, like `du -sk`, never following symlinks."""
    if path.is_symlink() or not path.is_dir():
        return 0
    total = 0
    for dirpath, dirs, files in os.walk(path):
        # du counts each directory's own blocks (4 KB apiece on ext4/APFS) and
        # a symlinked dir's link inode, which os.walk lists under dirs.
        links = [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]
        for name in (".", *files, *links):
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
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
        poster_kb=0 if store_escapes(dev_dir) else dir_kb(dev_dir / POSTER_STORE),
        path=dev_dir,
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


def store_escapes(dev_dir: Path) -> bool:
    """True if any part of the store's path below the device folder is a symlink
    (a symlinked `data`, `Library` … would point the delete somewhere else)."""
    p = dev_dir
    for part in POSTER_STORE.parts:
        p = p / part
        if p.is_symlink():
            return True
    return False


def prune_refusal(dev: SimDevice, running: set[str], dev_dir: Path) -> str | None:
    """Why this device's poster store must not be removed now, or None if it may."""
    if dev.udid != dev_dir.name:
        return f"device.plist says {dev.udid}, folder is {dev_dir.name}"
    if dev.state != SHUTDOWN:
        return f"state is {dev.state_name}, not Shutdown"
    if dev.udid in running:
        return "launchd_sim is running for it"
    if store_escapes(dev_dir):
        return "the store's path goes through a symlink"
    if not (dev_dir / POSTER_STORE).is_dir():
        return "no poster store"
    return None


def prune_device(
    dev_dir: Path, running: Callable[[], set[str] | None]
) -> tuple[SimDevice | None, int, str | None]:
    """Remove one device's poster store if it is safe. Measures first, then re-reads
    the device's state and asks `running` for a fresh launchd_sim list right
    before deleting; `running` returning None (ps failed) refuses.
    Returns (device, freed KB, refusal reason or None)."""
    dev = read_device(dev_dir)  # includes the size walk
    if dev is None:
        return None, 0, "not a simulator device"
    live = running()
    # Identity and state only: no second size walk between the check and the delete.
    plist = _read_plist(dev_dir / "device.plist")
    state = plist.get("state")
    now = (
        SimDevice(
            udid=plist["UDID"],
            name=dev.name,
            runtime=dev.runtime,
            state=state if isinstance(state, int) else None,
            poster_kb=dev.poster_kb,
            path=dev_dir,
        )
        if plist.get("UDID")
        else None
    )
    if now is None or live is None:
        return dev, 0, "could not re-check the device (device.plist or ps unreadable)"
    reason = prune_refusal(now, live, dev_dir)
    if reason:
        return dev, 0, reason
    try:
        shutil.rmtree(dev_dir / POSTER_STORE)
    except OSError as e:
        return dev, 0, f"delete failed: {e}"
    return dev, dev.poster_kb, None


def prune_all(
    root: Path, running: Callable[[], set[str] | None]
) -> list[tuple[SimDevice, int, str | None]]:
    """prune_device on every device under root that has a poster store.
    Returns (device, freed KB, refusal reason or None) for each one tried."""
    out = []
    for d in read_devices(root):
        if not d.poster_kb:
            continue
        dev, kb, why = prune_device(d.path, running)
        out.append((dev or d, kb, why))
    return out
