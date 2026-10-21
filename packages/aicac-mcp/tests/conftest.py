import pytest


@pytest.fixture
def anyio_backend():
    # The MCP SDK is built on anyio; its pytest plugin runs async fixtures and
    # tests in one task, which the client's cancel scopes require.
    return "asyncio"
