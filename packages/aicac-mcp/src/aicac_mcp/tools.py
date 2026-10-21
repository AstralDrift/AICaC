"""
Library wrappers around the AICaC adoption scripts vendored in data/scripts.

Every function takes a project root that must already exist. Failures raise
ToolError, which the MCP server reports as a tool error (isError: true).
Write functions default to a dry run and only touch disk when apply is True.
"""

import functools
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver.exceptions import ToolError

DATA = Path(__file__).resolve().parent / "data"

NEXT_STEPS = [
    "Review and complete TODO items in .ai/context.yaml",
    "Add optional files (architecture.yaml, workflows.yaml, etc.)",
    "Run aicac_validate to check compliance",
]


@functools.cache
def _script(name: str):
    """Import data/scripts/<name>.py once, namespaced under aicac_mcp._scripts."""
    module_name = f"aicac_mcp._scripts.{name}"
    spec = importlib.util.spec_from_file_location(module_name, DATA / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _project(project_path: str) -> Path:
    # Path("") is ".", so an empty string would otherwise be the server's cwd.
    if not project_path.strip():
        raise ToolError(
            "project_path is empty; pass '.' for the server's working directory"
        )
    root = Path(project_path).expanduser()
    if not root.is_dir():
        raise ToolError(f"project_path is not an existing directory: {project_path}")
    return root


def _failure_path(fallback: Path, exc: Exception) -> Path:
    """Use the file the parser or the OS named. The SDK drops the traceback."""
    filename = exc.filename if isinstance(exc, OSError) else None
    if isinstance(filename, str) and filename:
        return Path(filename)
    mark = getattr(exc, "problem_mark", None)
    name = getattr(mark, "name", None)
    if isinstance(name, str) and name and not name.startswith("<"):
        return Path(name)
    return fallback


def _io_error(fallback: Path, exc: Exception) -> ToolError:
    return ToolError(f"{_failure_path(fallback, exc)}: {exc}")


def _ai_dir(project_path: str) -> Path:
    ai_dir = _project(project_path) / ".ai"
    if not ai_dir.is_dir():
        raise ToolError(f"No .ai/ directory in {project_path}")
    return ai_dir


def _occupied(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def validate_project(project_path: str = ".", strict: bool = False) -> dict[str, Any]:
    """Validate .ai/ against the v2.0 schemas. A failed validation is a result, not an error."""
    root = _project(project_path)
    validator = _script("validate").AICaCValidator(
        str(root), schema_dir=DATA / "schemas", strict=strict
    )
    return validator.validate()


def bootstrap_project(project_path: str = ".", apply: bool = False) -> dict[str, Any]:
    """
    Create .ai/context.yaml and .ai/README.md with TODO placeholders.

    The bootstrap script always writes both files, so it renders into a scratch
    directory and only files that don't already exist are copied into .ai/.
    Existing files are never touched and are reported in files_skipped.
    A symlink at .ai is an error: following it would write outside the project.
    """
    root = _project(project_path)
    ai_dir = root / ".ai"
    if ai_dir.is_symlink():
        raise ToolError(f"{ai_dir} is a symlink; aicac_bootstrap does not follow it")
    if _occupied(ai_dir) and not ai_dir.is_dir():
        raise ToolError(f"{ai_dir} exists and is not a directory")

    bootstrap = _script("bootstrap").AICaCBootstrap(str(root))
    analysis = bootstrap.analyze_project()
    created, skipped = [], []
    with tempfile.TemporaryDirectory() as scratch:
        bootstrap.ai_dir = Path(scratch)
        bootstrap.create_minimal_structure(analysis)
        for rendered in sorted(Path(scratch).iterdir()):
            target = ai_dir / rendered.name
            if _occupied(target):
                skipped.append(rendered.name)
                continue
            if apply:
                ai_dir.mkdir(exist_ok=True)
                try:
                    with open(target, "xb") as f:
                        f.write(rendered.read_bytes())
                except FileExistsError:
                    skipped.append(rendered.name)
                    continue
            created.append(rendered.name)

    return {
        "applied": apply,
        "files_created": created,
        "files_skipped": skipped,
        "detected_type": analysis.get("project_type", "unknown"),
        "next_steps": NEXT_STEPS,
    }


def generate_index(project_path: str = ".", apply: bool = False) -> dict[str, Any]:
    """Regenerate .ai/index.yaml with the same rendering as generate_index.py."""
    ai_dir = _ai_dir(project_path)
    script = _script("generate_index")
    try:
        idx = script.build_index(ai_dir)
        content = script.render_index(idx)
        index_path = ai_dir / "index.yaml"
        existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        changed = existing.strip() != content.strip()
        if apply and changed:
            index_path.write_text(content, encoding="utf-8")
    except (yaml.YAMLError, OSError) as exc:
        raise _io_error(ai_dir, exc) from exc

    return {"applied": apply, "changed": changed, "ids_by_file": idx.get("keys", {})}


def migrate_project(project_path: str = ".", apply: bool = False) -> dict[str, Any]:
    """Migrate .ai/ files from v1.x to v2.0 shapes."""
    ai_dir = _ai_dir(project_path)
    script = _script("migrate_v2")

    changes = []
    for filename in script.MIGRATORS:
        path = ai_dir / filename
        try:
            operations = script.migrate_file(path, dry_run=not apply)
        except (yaml.YAMLError, OSError) as exc:
            raise _io_error(path, exc) from exc
        if operations:
            changes.append({"file": filename, "operations": operations})

    return {"applied": apply, "changes": changes}


def sync_suggest(
    project_path: str = ".", changed_files: list[str] | None = None
) -> dict[str, Any]:
    """
    List .ai/ files that may need updating after code changes.

    Without changed_files, uses `git diff --name-only HEAD`, which needs a git
    work tree with at least one commit. A git failure keeps one line of git's
    message, not its usage text. Nothing is scored or ranked; the model decides.
    """
    root = _project(project_path)
    if changed_files is None:
        changed_files = _git_diff_names(project_path, root)

    ai_dir = root / ".ai"
    candidates = sorted(
        f.name for f in ai_dir.glob("*.yaml") if f.name != "index.yaml"
    ) if ai_dir.is_dir() else []

    prompt = SYNC_PROMPT.format(
        changed_files="\n".join(f"  - {f}" for f in changed_files) or "  (none)",
        candidates="\n".join(f"  - {f}" for f in candidates) or "  (none)",
    )
    return {"changed_files": changed_files, "candidate_ai_files": candidates, "prompt": prompt}


def _git_error_line(stderr: str) -> str:
    for line in stderr.splitlines():
        text = line.strip()
        if text:
            return text
    return "no error message"


def _git_diff_names(project_path: str, root: Path) -> list[str]:
    # `git diff` outside a work tree prints its whole usage text. Ask rev-parse
    # first, and keep a single line if diff itself fails (for example, no HEAD).
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root, capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        raise ToolError("git is not installed; pass changed_files explicitly") from exc
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        detail = _git_error_line(probe.stderr) if probe.stderr.strip() else "not a git work tree"
        raise ToolError(
            f"git diff failed in {project_path} ({detail}); "
            "pass changed_files explicitly"
        )
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=root, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ToolError(
            f"git diff failed in {project_path} ({_git_error_line(exc.stderr)}); "
            "pass changed_files explicitly"
        ) from exc
    return [line for line in result.stdout.splitlines() if line]


SYNC_PROMPT = """Given the changed files below, determine which .ai/ files need updates:

Changed files:
{changed_files}

Available .ai/ files:
{candidates}

Guidance:
- context.yaml: project metadata, entrypoints, common tasks
- architecture.yaml: components, dependencies, data flow
- workflows.yaml: development procedures, how-to steps
- decisions.yaml: architectural decision records (ADRs)
- errors.yaml: common error patterns and solutions

Update only the files that are directly affected by the changes.
Do not update files based on speculation.
"""
