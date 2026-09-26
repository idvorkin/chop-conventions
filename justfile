default:
    @just --list

# Every skill's test_*.py under unittest, in one throwaway uv environment: the system python3 has neither
# pytest (imported by harden-telegram's kill-test) nor typer (the watchdog CLI). Every directory runs, even
# after a failure.
fast-test:
    @python3 -c "from pathlib import Path; import subprocess, sys; test_dirs = sorted({str(path.parent) for path in Path('skills').rglob('test_*.py')}); codes = [subprocess.run(['uv', 'run', '--quiet', '--no-project', '--with', 'pytest', '--with', 'typer', 'python', '-m', 'pytest', '-q', '-m', 'not slow', test_dir]).returncode for test_dir in test_dirs]; sys.exit(0 if all(c == 0 for c in codes) else 1)"

test:
    @echo "All tests - Add comprehensive tests"

# Install the bulk-parallel CLIs (`bulk-gh-pr-details`, `bulk-bd-show`, etc.)
# via `uv tool install`. Pre-PR-169: standalone entry point. Once #169 lands
# `install-tools.py` will cover this too.
install-bulk:
    uv tool install --force --reinstall ./skills/bulk/
