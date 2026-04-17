#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
# DEPRECATED shim: the canonical entry point is now `tg-doctor`, installed via
# `uv tool install <chop-conventions>/skills/harden-telegram/` (or `just
# install-tools` from the repo root). This file remains so in-flight callers
# (cron crons, startup-larry, docs) don't break during the migration.
#
# Behavior: adds the sibling skill dir to sys.path, re-exports every public
# name from `chop_telegram_tools.telegram_debug`, and forwards CLI invocation
# to its `main()`. Tests under `skills/harden-telegram/tools/` that
# `sys.path.insert(Path(__file__).parent)` and then
# `from telegram_debug import …` continue to resolve the same names.
#
# Remove this shim once all callers have switched to the `tg-doctor` binary.

import sys
from pathlib import Path

# Make `chop_telegram_tools` importable from the source tree (one level up).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chop_telegram_tools.telegram_debug as _impl  # noqa: E402

# Alias this module to the canonical package module. After this line, any
# importer who wrote `import telegram_debug` gets the SAME object as
# `import chop_telegram_tools.telegram_debug`. This matters for both:
#   1. Tests patching `telegram_debug.<name>` — now equivalent to patching
#      `chop_telegram_tools.telegram_debug.<name>`.
#   2. `from telegram_debug import _anything` — underscore-prefixed names
#      resolve because they live on the package module's dict.
sys.modules["telegram_debug"] = _impl
# Script-invocation path (e.g. `./telegram_debug.py doctor`): dispatch to
# the package's console-script main function.
if __name__ == "__main__":
    _impl.main()
