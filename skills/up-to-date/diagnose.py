#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# DEPRECATED shim: the canonical entry point is now `up-to-date-diag`, installed
# via `uv tool install <chop-conventions>/skills/up-to-date/` (or `just
# install-tools` from the repo root). This file remains so in-flight callers
# (the /up-to-date skill prose, delegate-to-other-repo subagent briefs, docs)
# don't break during the migration.
#
# Behavior: adds the sibling skill dir to sys.path, mirrors every public name
# from `chop_up_to_date.diagnose` into this module's namespace (including
# underscore-prefixed internals that tests import), and forwards CLI
# invocation to its `cli_main()`.
#
# Remove this shim once all callers have switched to the `up-to-date-diag` binary.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chop_up_to_date.diagnose as _impl  # noqa: E402

# Alias this module to the canonical package module so `import diagnose` /
# `from diagnose import _run` and `mock.patch("diagnose._run", ...)` all
# resolve to the same object as `chop_up_to_date.diagnose`. See the
# sibling shims in `skills/harden-telegram/tools/` for the full rationale.
sys.modules["diagnose"] = _impl
if __name__ == "__main__":
    _impl.cli_main()
