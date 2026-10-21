"""
Tool behaviour, exercised through a real MCP client session against the
server object, so argument validation and error reporting are in the path.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from mcp.client import Client

from aicac_mcp import server, tools

pytestmark = pytest.mark.anyio

CONTEXT_V2 = """\
version: '2.0'
summary: Tiny CLI — ünïcode summary
project:
  name: tiny
  type: cli
  description: A tiny command line tool
entrypoints:
  main: main.py
common_tasks:
  test: pytest
"""

# One workflow is valid but triggers the "fewer than 2 workflows" warning.
WORKFLOWS_ONE = """\
version: '2.0'
workflows:
  test:
    description: Run the tests
    steps:
      - action: run
        command: pytest
"""

CONTEXT_V1 = """\
version: '1.0'
project:
  name: legacy
  type: cli
entrypoints:
  main: main.py
common_commands:
  test: pytest
"""

WRITE_TOOLS = ["aicac_bootstrap", "aicac_generate_index", "aicac_migrate"]


def make_project(root: Path, **ai_files: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if ai_files:
        (root / ".ai").mkdir()
        for name, text in ai_files.items():
            (root / ".ai" / f"{name}.yaml").write_text(text, encoding="utf-8")
    return root


def project_for(tool: str, tmp_path: Path) -> Path:
    """A project where a dry run of `tool` has something to do."""
    if tool == "aicac_bootstrap":
        return make_project(tmp_path / "fresh")
    if tool == "aicac_generate_index":
        return make_project(tmp_path / "v2", context=CONTEXT_V2)
    return make_project(tmp_path / "v1", context=CONTEXT_V1)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else b"<dir>"
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture
async def client():
    async with Client(server.app) as c:
        yield c


async def call(client, tool, **args):
    result = await client.call_tool(tool, args)
    text = result.content[0].text
    return result.is_error, (text if result.is_error else json.loads(text))


def would_change(tool: str, data: dict) -> bool:
    if tool == "aicac_bootstrap":
        return bool(data["files_created"])
    if tool == "aicac_generate_index":
        return data["changed"]
    return bool(data["changes"])


async def test_lists_typed_tools(client):
    listed = {t.name: t for t in (await client.list_tools()).tools}
    assert set(listed) == {
        "aicac_validate", "aicac_bootstrap", "aicac_generate_index",
        "aicac_migrate", "aicac_sync_suggest",
    }
    for name in WRITE_TOOLS:
        schema = listed[name].input_schema
        assert schema["properties"]["apply"]["type"] == "boolean"
        assert schema["properties"]["apply"]["default"] is False
        assert listed[name].annotations.read_only_hint is False
    for name in ("aicac_validate", "aicac_sync_suggest"):
        assert listed[name].annotations.read_only_hint is True
    assert "compliance_level" not in listed["aicac_bootstrap"].input_schema["properties"]


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("explicit", [True, False], ids=["apply=false", "apply-omitted"])
async def test_dry_run_never_writes(client, tmp_path, tool, explicit):
    root = project_for(tool, tmp_path)
    before = snapshot(root)
    args = {"project_path": str(root)}
    if explicit:
        args["apply"] = False
    is_error, data = await call(client, tool, **args)
    assert not is_error, data
    assert data["applied"] is False
    assert would_change(tool, data), "dry run found nothing to do; the test proves nothing"
    assert snapshot(root) == before


@pytest.mark.parametrize("tool", WRITE_TOOLS)
async def test_apply_writes(client, tmp_path, tool):
    root = project_for(tool, tmp_path)
    before = snapshot(root)
    is_error, data = await call(client, tool, project_path=str(root), apply=True)
    assert not is_error, data
    assert data["applied"] is True
    assert snapshot(root) != before
    if tool == "aicac_bootstrap":
        assert sorted(data["files_created"]) == ["README.md", "context.yaml"]
        assert (root / ".ai" / "context.yaml").is_file()
    elif tool == "aicac_generate_index":
        assert (root / ".ai" / "index.yaml").is_file()
    else:
        migrated = yaml.safe_load((root / ".ai" / "context.yaml").read_text())
        assert migrated["version"] == "2.0"


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("bad", ["false", "true", "nope", 0, 1, None, []])
async def test_non_boolean_apply_is_rejected(client, tmp_path, tool, bad):
    root = project_for(tool, tmp_path)
    before = snapshot(root)
    is_error, text = await call(client, tool, project_path=str(root), apply=bad)
    assert is_error
    assert "apply" in text
    assert snapshot(root) == before


@pytest.mark.parametrize("apply", [False, True])
async def test_bootstrap_never_overwrites(client, tmp_path, apply):
    root = make_project(tmp_path / "p", context=CONTEXT_V2)
    is_error, data = await call(client, "aicac_bootstrap", project_path=str(root), apply=apply)
    assert not is_error, data
    assert data["files_skipped"] == ["context.yaml"]
    assert data["files_created"] == ["README.md"]
    assert (root / ".ai" / "context.yaml").read_text(encoding="utf-8") == CONTEXT_V2
    assert (root / ".ai" / "README.md").exists() is apply


async def test_bootstrap_skips_everything_when_complete(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2)
    (root / ".ai" / "README.md").write_text("mine")
    before = snapshot(root)
    is_error, data = await call(client, "aicac_bootstrap", project_path=str(root), apply=True)
    assert not is_error, data
    assert data["files_created"] == []
    assert data["files_skipped"] == ["README.md", "context.yaml"]
    assert snapshot(root) == before


async def test_bootstrap_does_not_write_through_symlink(client, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    root = make_project(tmp_path / "p")
    (root / ".ai").mkdir()
    (root / ".ai" / "README.md").symlink_to(outside)
    is_error, data = await call(client, "aicac_bootstrap", project_path=str(root), apply=True)
    assert not is_error, data
    assert "README.md" in data["files_skipped"]
    assert outside.read_text() == "keep"


async def test_validate_strict_is_passed_through(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2, workflows=WORKFLOWS_ONE)
    _, lax = await call(client, "aicac_validate", project_path=str(root))
    _, strict = await call(client, "aicac_validate", project_path=str(root), strict=True)
    assert lax["warnings"] and lax["valid"] is True
    assert strict["valid"] is False


async def test_validate_rejects_string_strict(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2)
    is_error, _ = await call(client, "aicac_validate", project_path=str(root), strict="false")
    assert is_error


async def test_invalid_project_is_a_result_not_an_error(client, tmp_path):
    root = make_project(tmp_path / "p")
    is_error, data = await call(client, "aicac_validate", project_path=str(root))
    assert not is_error
    assert data["valid"] is False


TOOLS = WRITE_TOOLS + ["aicac_validate", "aicac_sync_suggest"]


@pytest.mark.parametrize("tool", TOOLS)
async def test_missing_project_path_is_an_error(client, tmp_path, tool):
    missing = tmp_path / "does-not-exist"
    is_error, text = await call(client, tool, project_path=str(missing), changed_files=[]) \
        if tool == "aicac_sync_suggest" else await call(client, tool, project_path=str(missing))
    assert is_error
    assert "not an existing directory" in text
    assert not missing.exists()


@pytest.mark.parametrize("tool", TOOLS)
async def test_file_as_project_path_is_an_error(client, tmp_path, tool):
    f = tmp_path / "file.txt"
    f.write_text("x")
    is_error, text = await call(client, tool, project_path=str(f))
    assert is_error
    assert "not an existing directory" in text


@pytest.mark.parametrize("tool", ["aicac_generate_index", "aicac_migrate"])
async def test_missing_ai_dir_is_an_error(client, tmp_path, tool):
    root = make_project(tmp_path / "p")
    is_error, text = await call(client, tool, project_path=str(root), apply=True)
    assert is_error
    assert "No .ai/ directory" in text
    assert snapshot(root) == {}


async def test_generate_index_matches_repo_script(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2, workflows=WORKFLOWS_ONE)
    is_error, data = await call(client, "aicac_generate_index", project_path=str(root), apply=True)
    assert not is_error and data["changed"] is True
    written = (root / ".ai" / "index.yaml").read_text(encoding="utf-8")
    assert "ünïcode" in written
    check = subprocess.run(
        [sys.executable, str(tools.DATA / "scripts" / "generate_index.py"), str(root), "--check"],
        capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stderr
    _, again = await call(client, "aicac_generate_index", project_path=str(root), apply=False)
    assert again["changed"] is False


async def test_sync_suggest_with_explicit_files(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2, workflows=WORKFLOWS_ONE)
    is_error, data = await call(
        client, "aicac_sync_suggest", project_path=str(root), changed_files=["src/app.py"]
    )
    assert not is_error, data
    assert data["changed_files"] == ["src/app.py"]
    assert data["candidate_ai_files"] == ["context.yaml", "workflows.yaml"]
    assert "src/app.py" in data["prompt"]


async def test_sync_suggest_outside_git_is_an_error(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2)
    is_error, text = await call(client, "aicac_sync_suggest", project_path=str(root))
    assert is_error
    assert "pass changed_files explicitly" in text
    assert "not a git repository" in text
    assert "usage:" not in text.lower()
    assert "\n" not in text


async def test_sync_suggest_without_head_keeps_one_git_line(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    is_error, text = await call(client, "aicac_sync_suggest", project_path=str(root))
    assert is_error
    assert "ambiguous argument 'HEAD'" in text
    assert "pass changed_files explicitly" in text
    assert "Use '--'" not in text
    assert "\n" not in text


BROKEN_YAML = "version: '2.0'\nproject: [\n  name: nope\n"


@pytest.mark.parametrize("tool", ["aicac_generate_index", "aicac_migrate"])
@pytest.mark.parametrize("apply", [False, True])
async def test_yaml_error_names_the_file(client, tmp_path, tool, apply):
    root = make_project(tmp_path / "p")
    context = root / ".ai" / "context.yaml"
    (root / ".ai").mkdir()
    context.write_text(BROKEN_YAML, encoding="utf-8")
    before = snapshot(root)
    is_error, text = await call(client, tool, project_path=str(root), apply=apply)
    assert is_error
    assert str(context) in text
    assert "line " in text
    assert snapshot(root) == before


@pytest.mark.parametrize("tool", ["aicac_generate_index", "aicac_migrate"])
async def test_oserror_names_the_file(client, tmp_path, tool):
    root = make_project(tmp_path / "p")
    context = root / ".ai" / "context.yaml"
    (root / ".ai").mkdir()
    context.mkdir()
    before = snapshot(root)
    is_error, text = await call(client, tool, project_path=str(root), apply=True)
    assert is_error
    assert str(context) in text
    assert "Is a directory" in text
    assert snapshot(root) == before


@pytest.mark.parametrize("apply", [False, True])
async def test_bootstrap_refuses_symlink_ai_directory(client, tmp_path, apply):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    root = make_project(tmp_path / "p")
    (root / ".ai").symlink_to(outside, target_is_directory=True)
    before = snapshot(outside)
    is_error, text = await call(client, "aicac_bootstrap", project_path=str(root), apply=apply)
    assert is_error
    assert "symlink" in text
    assert "does not follow" in text
    assert snapshot(outside) == before
    assert not (outside / "README.md").exists()
    assert not (outside / "context.yaml").exists()


async def test_bootstrap_refuses_dangling_ai_symlink(client, tmp_path):
    root = make_project(tmp_path / "p")
    target = tmp_path / "missing-ai"
    (root / ".ai").symlink_to(target)
    is_error, text = await call(client, "aicac_bootstrap", project_path=str(root), apply=True)
    assert is_error
    assert "does not follow" in text
    assert not target.exists()


@pytest.mark.parametrize("tool", TOOLS)
async def test_empty_project_path_is_rejected(client, tmp_path, monkeypatch, tool):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marker").write_text("x")
    before = snapshot(tmp_path)
    kwargs = {"project_path": ""}
    if tool in WRITE_TOOLS:
        kwargs["apply"] = True
    is_error, text = await call(client, tool, **kwargs)
    assert is_error
    assert "empty" in text
    assert snapshot(tmp_path) == before


async def test_blank_project_path_is_rejected(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = snapshot(tmp_path)
    is_error, text = await call(
        client, "aicac_bootstrap", project_path=" \n\t", apply=True
    )
    assert is_error
    assert "empty" in text
    assert snapshot(tmp_path) == before


async def test_dot_project_path_is_the_working_directory(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    is_error, data = await call(client, "aicac_bootstrap", project_path=".", apply=False)
    assert not is_error, data
    assert data["files_created"]
    assert not (tmp_path / ".ai").exists()


async def test_sync_suggest_reads_git_diff(client, tmp_path):
    root = make_project(tmp_path / "p", context=CONTEXT_V2)
    (root / "app.py").write_text("print(1)\n")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    (root / "app.py").write_text("print(2)\n")
    is_error, data = await call(client, "aicac_sync_suggest", project_path=str(root))
    assert not is_error, data
    assert data["changed_files"] == ["app.py"]
