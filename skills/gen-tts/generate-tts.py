#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
# DEPRECATED shim: the canonical entry point is now `gen-tts`, installed via
# `uv tool install <chop-conventions>/skills/gen-tts/` (or
# `just install-tools` from the repo root). This file remains only so in-flight
# callers (docs, skills, `$PATH`-free scripts) don't break during the migration.
#
# Behavior: adds the sibling package dir to sys.path, then dispatches to
# `chop_gen_tts.cli.main`. Identical CLI surface; argv is preserved as-is.
#
# Remove this shim once all callers have switched to the `gen-tts` binary.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chop_gen_tts.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
