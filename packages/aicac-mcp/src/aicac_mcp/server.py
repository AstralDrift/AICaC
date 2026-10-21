"""
AICaC MCP server (stdio).

Tools wrap the AICaC adoption scripts; resources serve the v2.0 JSON Schemas
and the skill recipes. Argument schemas come from the typed signatures below,
and the SDK rejects calls that don't match them before any tool code runs.
"""

from importlib.metadata import version
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.resources import FileResource
from mcp.types import ToolAnnotations
from pydantic import Field, Strict

from . import tools

ProjectPath = Annotated[
    str,
    Field(description=(
        "Path to an existing project root. Use '.' for the server's working "
        "directory; an empty string is rejected."
    )),
]
Apply = Annotated[
    bool, Strict(), Field(description="Write changes to disk. Default false is a dry run.")
]

READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
REWRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)

app = MCPServer("aicac-mcp", version=version("aicac-mcp"))


@app.tool(annotations=READ_ONLY)
def aicac_validate(
    project_path: ProjectPath = ".",
    strict: Annotated[bool, Strict(), Field(description="Treat warnings as errors")] = False,
) -> dict[str, Any]:
    """Validate a project's .ai/ directory against the AICaC v2.0 schemas and report
    the compliance level (None, Minimal, Standard, Comprehensive), errors and warnings."""
    return tools.validate_project(project_path, strict)


@app.tool(annotations=WRITES)
def aicac_bootstrap(project_path: ProjectPath = ".", apply: Apply = False) -> dict[str, Any]:
    """Create .ai/context.yaml and .ai/README.md with TODO placeholders. Files that
    already exist are never overwritten; they are listed in files_skipped. If .ai
    is a symlink, the call is an error and nothing is written."""
    return tools.bootstrap_project(project_path, apply)


@app.tool(annotations=REWRITES)
def aicac_generate_index(project_path: ProjectPath = ".", apply: Apply = False) -> dict[str, Any]:
    """Regenerate .ai/index.yaml from the other .ai/ files and report whether it changed."""
    return tools.generate_index(project_path, apply)


@app.tool(annotations=REWRITES)
def aicac_migrate(project_path: ProjectPath = ".", apply: Apply = False) -> dict[str, Any]:
    """Migrate .ai/ files from v1.x to v2.0 and list the changes per file."""
    return tools.migrate_project(project_path, apply)


@app.tool(annotations=READ_ONLY)
def aicac_sync_suggest(
    project_path: ProjectPath = ".",
    changed_files: Annotated[
        list[str] | None,
        Field(description="Changed source files. If omitted, uses `git diff --name-only HEAD`."),
    ] = None,
) -> dict[str, Any]:
    """List the .ai/ files that may need updating after code changes, with a prompt
    for deciding which ones. Nothing is ranked; the model decides."""
    return tools.sync_suggest(project_path, changed_files)


# Resources are registered as exact URIs, so a read of anything not listed
# here fails with "Unknown resource" instead of touching the filesystem.
for _path in sorted((tools.DATA / "schemas").glob("*.schema.json")):
    _name = _path.name.removesuffix(".schema.json")
    app.add_resource(FileResource(
        uri=f"aicac://spec/v2/{_path.name}",
        name=f"schema-{_name}",
        description=f"AICaC v2.0 JSON Schema for .ai/{_name}.yaml",
        mime_type="application/json",
        path=_path,
    ))

# SKILL.md is the Claude Code entry point and is not served; the recipes are.
for _path in sorted((tools.DATA / "skills").glob("*.md")):
    if _path.name == "SKILL.md":
        continue
    app.add_resource(FileResource(
        uri=f"aicac://skill/{_path.stem}",
        name=f"skill-{_path.stem}",
        description=f"AICaC skill recipe: {_path.stem}",
        mime_type="text/markdown",
        path=_path,
    ))


def main() -> None:
    """Entry point for the aicac-mcp command."""
    app.run("stdio")


if __name__ == "__main__":
    main()
