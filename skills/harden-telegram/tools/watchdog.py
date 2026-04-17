#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
# DEPRECATED shim: the canonical entry point is now `tg-watchdog`, installed via
# `uv tool install <chop-conventions>/skills/harden-telegram/` (or `just
# install-tools` from the repo root). This file remains so in-flight callers
# (server.ts spawn, startup-larry, docs) don't break during the migration.
#
# Behavior: adds the sibling skill dir to sys.path, re-exports every public
# name from `chop_telegram_tools.watchdog`, and forwards CLI invocation to its
# `main()`. Tests under `skills/harden-telegram/tools/` that `sys.path.insert(
# Path(__file__).parent)` and then `from watchdog import …` continue to
# resolve the same names.
#
# Remove this shim once all callers have switched to the `tg-watchdog` binary.

import sys
from pathlib import Path

# Make `chop_telegram_tools` importable from the source tree (one level up).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chop_telegram_tools.watchdog as _impl  # noqa: E402

# Alias this module to the canonical package module. After this line, any
# importer who wrote `import watchdog` gets the SAME object as
# `import chop_telegram_tools.watchdog`. This matters for two callers:
#   1. Tests patching `watchdog.subprocess.run` — now equivalent to
#      patching `chop_telegram_tools.watchdog.subprocess.run`.
#   2. `from watchdog import _FALLBACK` — underscore-prefixed names resolve
#      because they live on the package module's dict.
# Mirror-copying globals (the previous approach) doesn't satisfy (1)
# because functions imported into the shim still look up names via the
# package module, not the shim. Direct aliasing fixes that.
sys.modules["watchdog"] = _impl
# Script-invocation path (e.g. `./watchdog.py reload`): dispatch to the
# package's console-script main function. The aliasing above is for
# `import watchdog` callers; this branch is for `python watchdog.py ...`.
if __name__ == "__main__":
    _impl.main()
