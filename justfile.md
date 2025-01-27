In justfiles, when called with no params, the first command listed is called.
Make sure the first commmand in the file lists the callable commands e.g.

```
default:
    @just --list
```

When creating python tool install scripts, set it up as follows

```
install:
    uv venv
    . .venv/bin/activate
    uv pip install --upgrade --editable .

global-install: install
    uv tool install --force --editable  .
```
