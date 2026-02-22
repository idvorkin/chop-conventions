In justfiles, when called with no params, the first command listed is called.
Make sure the first commmand in the file lists the callable commands e.g.

```
default:
    @just --list
```

Ensure we always have a test command, and fast-test command (it's called by prek hooks).
It can just print 0/0 tests passed until we have more tests

```
fast-test:
    @echo "0/0 tests passed - Add tests"
test:
    @echo "All tests - Add comprehensive tests"
```

When creating python tool install scripts, **only add these if the project has Python packages to install**:

```
install:
    uv venv
    . .venv/bin/activate
    uv pip install --upgrade --editable .

global-install: install
    uv tool install --force --editable  .
```
