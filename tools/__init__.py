"""MINT Protocol MCP tools. Each module exposes a `register(mcp)` that attaches
its @mcp.tool to the shared FastMCP instance, keeping one tool per file."""

from . import register as register_tool
from . import attest as attest_tool
from . import verify as verify_tool


def register_all(mcp) -> None:
    register_tool.register(mcp)
    attest_tool.register(mcp)
    verify_tool.register(mcp)
