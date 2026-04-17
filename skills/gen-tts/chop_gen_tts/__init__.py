"""chop_gen_tts — Gemini 3.1 Flash TTS CLI packaged for `uv tool install`.

The console-script entry point is `gen-tts`, defined in
`[project.scripts]` of the sibling `pyproject.toml` and wired to
`chop_gen_tts.cli:main`.
"""

from chop_gen_tts.cli import main

__all__ = ["main"]
