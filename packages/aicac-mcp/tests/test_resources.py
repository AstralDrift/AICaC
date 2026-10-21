"""Resources are a fixed list of exact URIs; nothing else can be read."""

import pytest
from mcp import MCPError
from mcp.client import Client

from aicac_mcp import server, tools

pytestmark = pytest.mark.anyio

SCHEMAS = sorted(p.name for p in (tools.DATA / "schemas").glob("*.schema.json"))
RECIPES = sorted(p.stem for p in (tools.DATA / "skills").glob("*.md") if p.name != "SKILL.md")
EXPECTED = {f"aicac://spec/v2/{n}" for n in SCHEMAS} | {f"aicac://skill/{n}" for n in RECIPES}


@pytest.fixture
async def client():
    async with Client(server.app) as c:
        yield c


async def test_lists_every_schema_and_recipe(client):
    listed = {str(r.uri) for r in (await client.list_resources()).resources}
    assert len(SCHEMAS) == 6 and len(RECIPES) == 5
    assert listed == EXPECTED
    assert (await client.list_resource_templates()).resource_templates == []


@pytest.mark.parametrize("uri", sorted(EXPECTED))
async def test_every_listed_uri_reads(client, uri):
    contents = (await client.read_resource(uri)).contents
    assert len(contents) == 1 and contents[0].text.strip()


@pytest.mark.parametrize("uri", [
    "aicac://spec/v2/../../server.py",
    "aicac://spec/v2/../scripts/validate.py",
    "aicac://spec/v2/%2e%2e/%2e%2e/server.py",
    "aicac://skill/../scripts/validate",
    "aicac://skill/../../tools",
    "aicac://skill/SKILL",
    "aicac://skill/router.md",
    "aicac://spec/v2//etc/passwd",
    "aicac://spec/v2/context",
    "file:///etc/passwd",
])
async def test_unlisted_uri_is_rejected(client, uri):
    with pytest.raises(MCPError, match="Unknown resource"):
        await client.read_resource(uri)
