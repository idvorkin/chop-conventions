Use `uv` shebang and dependency blocks for Python scripts to automatically manage dependencies and environments.

Start scripts with this shebang:

```python
#!uv run
```

Add dependency block immediately after shebang:

```python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer",
#     "icecream",
#     "rich",
#     "langchain",
#     "langchain-core",
#     "langchain-community",
#     "langchain-openai",
#     "openai",
#     "loguru",
#     "pydantic",
#     "requests",
# ]
# ///
```

**Always update the dependencies list when adding or removing imports**. Anyone with `uv` installed can run the script directly without manual environment setup.

See [changes.py](mdc:changes.py) for a real implementation.
