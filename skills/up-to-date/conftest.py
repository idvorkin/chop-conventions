"""Pytest + pyright module-discovery shim for this skill directory.

The production entry points (`diagnose.py`, `hook_trust.py`) live
directly in this directory, not under a package, so importing them
from sibling test files requires the directory itself to be on
`sys.path`. `unittest discover` already adds the start directory
to `sys.path` automatically, so the `just fast-test` path works
without this file. Pytest and pyright do not, so we shim here so
both tools can resolve `from hook_trust import ...` and similar.

Keeping this as the single source of truth lets individual test
files drop their inline `sys.path.insert` boilerplate.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
