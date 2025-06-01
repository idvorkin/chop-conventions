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
