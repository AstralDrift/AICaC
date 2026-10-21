"""End to end over stdio, the way an MCP client launches the server."""

import json
import sys

import pytest
from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio


async def test_stdio_round_trip(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "aicac_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info.name == "aicac-mcp"

            tools = {t.name for t in (await session.list_tools()).tools}
            assert "aicac_bootstrap" in tools

            result = await session.call_tool(
                "aicac_bootstrap", {"project_path": str(tmp_path), "apply": "false"}
            )
            assert result.is_error
            assert not (tmp_path / ".ai").exists()

            result = await session.call_tool("aicac_bootstrap", {"project_path": str(tmp_path)})
            assert not result.is_error
            assert json.loads(result.content[0].text)["applied"] is False
            assert not (tmp_path / ".ai").exists()

            text = (await session.read_resource("aicac://skill/router")).contents[0].text
            assert text.strip()
            with pytest.raises(MCPError):
                await session.read_resource("aicac://skill/../scripts/validate")
