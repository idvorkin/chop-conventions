default:
    @just --list

fast-test:
    @python3 -c "from pathlib import Path; import subprocess, sys; test_dirs = sorted({str(path.parent) for path in Path('skills').rglob('test_*.py')}); sys.exit(0 if all(subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', test_dir, '-p', 'test_*.py']).returncode == 0 for test_dir in test_dirs) else 1)"

test:
    @echo "All tests - Add comprehensive tests"

# Install chop-conventions skill CLIs as `uv tool install` packages.
install-tools:
    ./install-tools.py

# Preview install-tools actions without touching uv's tool env.
install-tools-dry-run:
    ./install-tools.py --dry-run

# `uv tool uninstall` every registered chop-conventions package.
uninstall-tools:
    ./install-tools.py --uninstall
