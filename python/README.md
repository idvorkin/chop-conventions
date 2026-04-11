# Python Conventions

**Python is the default scripting language in chop-conventions projects.** Shell is reserved for one-liners and `just` targets. Anything with branching, data structures, or more than ~20 lines should be Python.

## Why Python over shell

- **Readable and testable** — pure functions can be unit-tested with `unittest`/`pytest`; shell logic can't.
- **Real data structures** — dataclasses, JSON output, exceptions.
- **Parallelism is tractable** — `concurrent.futures.ThreadPoolExecutor` beats `&`/`wait`/tempfile juggling.
- **uv makes distribution trivial** — scripts are single files with an inline dependency block; users only need `uv` installed.

The one real downside is that git/gh calls go through a `subprocess.run(...)` wrapper instead of being the script itself. One thin `git()` helper function (3 lines) makes this a non-issue after a handful of calls.

## How to write a Python script

1. **Start with the uv shebang and dependency block.** See [`uv-shebang-deps.md`](uv-shebang-deps.md). Stdlib-only scripts use `dependencies = []`; user-facing CLIs should use Typer + Rich per [`python-cli.md`](python-cli.md).
2. **Structure for testability.** Put pure logic (parsing, classification, formatting) behind functions with no I/O. Put subprocess calls and file reads in a thin orchestrator that calls those pure functions.
3. **Shell out through a wrapper.** `def git(*args): return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()` — one helper makes the rest of the script read cleanly.
4. **Write the test first.** Unit-test the pure functions with `unittest` (stdlib, zero deps). Run with `python3 -m unittest test_<name>.py` from the script's directory.

## Examples

- **Stdlib-only JSON emitter, parallel subprocess:** [`../skills/up-to-date/diagnose.py`](../skills/up-to-date/diagnose.py) + [`../skills/up-to-date/test_diagnose.py`](../skills/up-to-date/test_diagnose.py)
- **Typer + Rich user-facing CLI:** see [`python-cli.md`](python-cli.md)

## When shell is OK

- One-liners in `justfile` targets
- Hooks where startup latency matters more than maintainability (rare)
- Wrapping a Python script with a shebang-less file for a `PATH` entry
