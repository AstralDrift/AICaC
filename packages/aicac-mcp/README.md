# AICaC MCP Server

Model Context Protocol (MCP) server for [AI Context as Code (AICaC) v2.0](https://github.com/eFAILution/AICaC).

Provides AICaC validation, bootstrap, migration, and sync capabilities as MCP tools and resources for any MCP-compatible AI coding assistant (Claude Code, Cursor, Windsurf, Continue, Zed, Aider, etc.).

## Features

### Tools

Read-only:

- **`aicac_validate`** — Validate `.ai/` against the v2.0 schemas plus the validator's content checks, and report the compliance level
- **`aicac_sync_suggest`** — List the `.ai/` files that may need updates after code changes. Uses `git diff --name-only HEAD` unless you pass `changed_files`. Outside a git work tree it returns an error asking for `changed_files`. That error quotes one line from git, not git's usage text

Writing (dry run unless `apply` is the JSON boolean `true`):

- **`aicac_bootstrap`** — Create `.ai/context.yaml` and `.ai/README.md` with TODO placeholders. Never overwrites: files that already exist are left alone and listed in `files_skipped`. If `.ai` is a symlink, the tool returns an error and does not follow it
- **`aicac_generate_index`** — Regenerate `.ai/index.yaml` (byte-for-byte what `generate_index.py` writes, so `generate_index.py --check` passes afterwards)
- **`aicac_migrate`** — Migrate v1.x `.ai/` to v2.0 (list→dict, `common_commands`→`common_tasks`, etc.)

Arguments are checked against the typed tool signatures before any tool code runs. `apply` and `strict` must be real booleans; `"false"`, `0` or `null` are rejected as errors rather than guessed at. `project_path` must be an existing directory. An empty string is rejected; pass `.` for the server's working directory. Failures come back as MCP tool errors (`isError: true`). A YAML or IO failure names the file.

### Resources (read-only)

- **JSON Schemas** — `aicac://spec/v2/{context,architecture,workflows,decisions,errors,index}.schema.json`
- **Skill content** — `aicac://skill/{router,bootstrap,validate,sync,migrate}` serving the recipes from `.claude/skills/aicac/*.md` (the Claude Code entry point `SKILL.md` is not served)

Only the URIs above are served. Anything else, including `..` paths, fails with "Unknown resource".

## Installation

The package includes all necessary assets (schemas, skills, scripts) and works standalone without requiring the full monorepo checkout.

### Via uvx (recommended)

```bash
uvx --from "git+https://github.com/eFAILution/AICaC#subdirectory=packages/aicac-mcp" aicac-mcp
```

### Via pip

```bash
pip install git+https://github.com/eFAILution/AICaC#subdirectory=packages/aicac-mcp
```

### Development install

```bash
cd packages/aicac-mcp
pip install -e '.[dev]'
pytest
```

Requires Python 3.10+ and the MCP Python SDK 2.x (`mcp>=2.0.0,<3`).

## Usage

### Cursor

Add to your MCP configuration (Cursor Settings > MCP):

```json
{
  "mcpServers": {
    "aicac": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/eFAILution/AICaC#subdirectory=packages/aicac-mcp",
        "aicac-mcp"
      ]
    }
  }
}
```

Or for local development:

```json
{
  "mcpServers": {
    "aicac": {
      "command": "python",
      "args": ["-m", "aicac_mcp.server"]
    }
  }
}
```

### Claude Code / Claude Desktop

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "aicac": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/eFAILution/AICaC#subdirectory=packages/aicac-mcp",
        "aicac-mcp"
      ]
    }
  }
}
```

### Other MCP clients

Use the same pattern — all MCP-capable tools use stdio transport. Consult your tool's MCP configuration documentation.

## Tool usage examples

### Validate a project

```python
# Via MCP tool call
{
  "tool": "aicac_validate",
  "arguments": {
    "project_path": ".",
    "strict": false
  }
}
```

Returns:
```json
{
  "valid": true,
  "compliance_level": "Standard",
  "errors": [],
  "warnings": [],
  "found_files": {
    "context.yaml": true,
    "architecture.yaml": true,
    "workflows.yaml": true
  }
}
```

### Bootstrap a new project (dry-run)

```python
{
  "tool": "aicac_bootstrap",
  "arguments": {
    "project_path": ".",
    "apply": false
  }
}
```

Returns what would be created without writing files:

```json
{
  "applied": false,
  "files_created": ["README.md"],
  "files_skipped": ["context.yaml"],
  "detected_type": "python-app",
  "next_steps": ["Review and complete TODO items in .ai/context.yaml", "..."]
}
```

### Apply bootstrap

```python
{
  "tool": "aicac_bootstrap",
  "arguments": {
    "project_path": ".",
    "apply": true
  }
}
```

Creates whichever of `.ai/context.yaml` and `.ai/README.md` don't exist yet, with TODO markers. It does not create `AGENTS.md` or `.ai/index.yaml`; run `aicac_generate_index` for the index.

### Migrate v1.x to v2.0 (dry-run)

```python
{
  "tool": "aicac_migrate",
  "arguments": {
    "project_path": ".",
    "apply": false
  }
}
```

Returns `changes`, a list of `{file, operations}` describing what would change. Set `"apply": true` to write.

### Check which .ai/ files might need updates

```python
{
  "tool": "aicac_sync_suggest",
  "arguments": {
    "project_path": ".",
    "changed_files": ["src/api/router.py", "src/models/user.py"]
  }
}
```

Returns candidate `.ai/` files and a prompt for the model to decide.

## Design

This server is **local and filesystem-bound by design**. AICaC operations read/write a specific project's `.ai/` directory, so local scope is the natural fit. The tools act on any directory the process can access. An empty `project_path` is not that directory: pass `.` for the server's working directory.

- **Language:** Python (reuses existing `validate.py`, `bootstrap.py`, `migrate_v2.py`, `generate_index.py` as libraries)
- **SDK:** `mcp` (official Python MCP SDK)
- **Transport:** stdio only (standard for local MCP)
- **Versioning:** Pinned to AICaC spec version (`aicac-mcp@2.x` for spec v2.0)

See [`validation/docs/mcp-server-design.md`](../../validation/docs/mcp-server-design.md) for the full design rationale.

## Limitations

- **Standalone packaging** — all required assets (schemas, skills, scripts) are vendored in `data/` subdirectories. No monorepo checkout required. (Addresses [#22](https://github.com/eFAILution/AICaC/issues/22)) `tests/test_vendored_sync.py` fails if a copy drifts from its source in the repo.
- **Not implemented yet** from the [design doc](../../validation/docs/mcp-server-design.md):
  - MCP prompts `aicac:router`, `aicac:bootstrap`, `aicac:sync`
  - Resources `aicac://spec/behavior.md`, `aicac://project/index`, `aicac://project/summaries`
  - `aicac_migrate` dry-run returns a per-file list of operations, not a unified diff
  - `aicac_bootstrap` has no `compliance_level` option; it only writes the minimal skeleton
  - `aicac_validate` returns flat `errors`/`warnings` lists, not the separate `schema_violations`, `cross_ref_issues`, and `content_quality` fields
- **Local filesystem only** — no remote/HTTP transport (see design doc rationale)
- **Dry-run default** — write tools only touch disk with `apply: true`
- **No PyPI publish yet** — install via git URL until first stable release

## Contributing

See the main [AICaC repository](https://github.com/eFAILution/AICaC) for contribution guidelines.

## License

MIT (see [LICENSE-CODE](../../LICENSE-CODE) in the repository root)
