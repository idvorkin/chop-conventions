#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# DEPRECATED shim: the canonical entry point is now `up-to-date-hook-trust`,
# installed via `uv tool install <chop-conventions>/skills/up-to-date/` (or
# `just install-tools` from the repo root). This file remains so in-flight
# callers (the /up-to-date skill prose, docs) don't break during the migration.
#
# Behavior: adds the sibling skill dir to sys.path, mirrors every public name
# from `chop_up_to_date.hook_trust` into this module's namespace (including
# underscore-prefixed internals that tests import), and forwards CLI
# invocation to its `cli_main()`.
#
# Remove this shim once all callers have switched to the
# `up-to-date-hook-trust` binary.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chop_up_to_date.hook_trust as _impl  # noqa: E402

# Alias this module to the canonical package module. See the sibling shims
# in `skills/harden-telegram/tools/` for the full rationale.
sys.modules["hook_trust"] = _impl
if __name__ == "__main__":
    _impl.cli_main()
