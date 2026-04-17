# Python CLI Files Configuration

## Context

- When creating or modifying Python CLI tools

## Requirements

- All Python CLI files must start with UV script configuration
- Dependencies must be declared in the script header
- Executables must be registered in pyproject.toml when it exists
- UV configuration must be present in pyproject.toml
- Use Typer for CLI framework and Rich for terminal output
- CLI must show help when called with no commands
- Use Python 3.13+ Annotated types for better type hints and help text

## Examples

<example>
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer",
#     "rich",
# ]
# ///

import typer
from typing_extensions import Annotated
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
help="My awesome CLI tool",
add_completion=False,
no_args_is_help=True,
)

console = Console()

@app.command(help="Greet someone")
def greet(
name: Annotated[str, typer.Argument(help="Name to greet")],
formal: Annotated[bool, typer.Option(help="Use formal greeting")] = False,
):
"""
Say hi to NAME, optionally with a formal greeting.
"""
if formal:
console.print(Panel(f"Good day [bold blue]{name}[/]!", title="Formal Greeting"))
else:
console.print(Panel(f"Hello [bold blue]{name}[/]!", title="Greeting"))

@app.command(help="Show version")
def version():
"""
Display the current version of the tool.
"""
console.print(Panel("v1.0.0", title="Version"))

if **name** == "**main**":
app()
</example>

<example>
# Lazy-import pattern for testability (tests run in system Python without uv)
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""My CLI tool — single or batch mode."""

import json
import sys
from pathlib import Path

# Pure logic — importable by tests without typer
def process_one(item: dict) -> dict:
    return {"result": item["name"].upper()}

def _build_app():
    """Wire Typer app. Called only from __main__ so tests skip the typer import."""
    import typer

    app = typer.Typer(
        help="My CLI tool — process items.",
        add_completion=False,
        no_args_is_help=True,
    )

    @app.command()
    def single(
        name: str = typer.Argument(..., help="Item name to process"),
        output: str = typer.Option(..., "--output", help="Output file path"),
    ) -> None:
        """Process a single item."""
        result = process_one({"name": name})
        Path(output).write_text(json.dumps(result))
        print(output)

    @app.command()
    def batch(
        json_file: str = typer.Argument(..., help="JSON file with items array"),
    ) -> None:
        """Process items in parallel from a JSON file."""
        items = json.loads(Path(json_file).read_text())
        for item in items:
            result = process_one(item)
            print(json.dumps(result))

    return app

if __name__ == "__main__":
    _build_app()()
</example>

<example type="invalid">
import typer  # Missing UV script configuration

app = typer.Typer() # Missing help configuration

@app.command()
def main():
print("Hello World!") # Using print instead of Rich

if **name** == "**main**":
app()
</example>

<example>
# pyproject.toml
[tool.poetry.scripts]
my-cli = "my_cli:app"

[tool.uv]
pip = true
</example>

<example type="invalid">
# pyproject.toml
[tool.poetry]
name = "my-project"
# Missing executable registration and UV configuration
</example>
