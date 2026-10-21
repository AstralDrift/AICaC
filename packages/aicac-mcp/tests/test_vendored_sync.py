"""
The package ships copies of repo files under src/aicac_mcp/data/ so a
standalone install works. These tests fail when a copy drifts from its
source of truth in the monorepo. Skipped outside a monorepo checkout.
"""

from pathlib import Path

import pytest

PKG_DATA = Path(__file__).resolve().parents[1] / "src" / "aicac_mcp" / "data"
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / ".github" / "actions" / "aicac-adoption" / "scripts"

# The one intended difference: the vendored validate.py finds schemas next to
# itself (data/schemas) instead of walking up to the repo's spec/v2.
VALIDATE_PATCH = (
    'REPO_ROOT = Path(__file__).resolve().parents[4]\n'
    'SCHEMA_DIR_V2 = REPO_ROOT / "spec" / "v2"\n',
    'SCHEMA_DIR_V2 = Path(__file__).resolve().parent.parent / "schemas"\n',
)

SOURCES = {
    "schemas": REPO_ROOT / "spec" / "v2",
    "skills": REPO_ROOT / ".claude" / "skills" / "aicac",
    "scripts": SCRIPTS,
}

pytestmark = pytest.mark.skipif(
    not SCRIPTS.is_dir(), reason="not running from a monorepo checkout"
)


def _vendored():
    for kind in SOURCES:
        for path in sorted((PKG_DATA / kind).iterdir()):
            if path.is_file():
                yield kind, path


@pytest.mark.parametrize(
    "kind,vendored", list(_vendored()), ids=lambda v: getattr(v, "name", v)
)
def test_vendored_copy_matches_source(kind, vendored):
    source = SOURCES[kind] / vendored.name
    assert source.is_file(), f"{vendored.name} has no source at {source}"
    expected = source.read_bytes()
    if kind == "scripts" and vendored.name == "validate.py":
        old, new = VALIDATE_PATCH
        assert expected.count(old.encode()) == 1, "validate.py patch anchor moved"
        expected = expected.replace(old.encode(), new.encode())
    assert vendored.read_bytes() == expected, (
        f"{vendored.relative_to(PKG_DATA)} differs from {source.relative_to(REPO_ROOT)}; "
        "re-copy it"
    )


def test_every_source_schema_and_skill_is_vendored():
    for kind in ("schemas", "skills"):
        want = {p.name for p in SOURCES[kind].iterdir() if p.is_file()}
        have = {p.name for p in (PKG_DATA / kind).iterdir() if p.is_file()}
        assert want == have, f"{kind}: missing {want - have}, extra {have - want}"
