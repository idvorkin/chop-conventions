
When creating a justfile ensure the first command is called default and lists everything

```
defaut:
    @just --list
```

When creating tool install scripts, set it up as follows


```
install:
    uv venv
    . .venv/bin/activate
    uv pip install --upgrade --editable .

global-install: install
    uv tool install --force --editable  .
```
