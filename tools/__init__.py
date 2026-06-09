"""MINT Protocol MCP tools. Each module exposes a `register(mcp)` that attaches
its @mcp.tool to the shared FastMCP instance, keeping one tool per file.

Six tools — the full trust stack: identity, attestation, verification, rating,
recommendation, discovery."""

from . import register as register_tool
from . import attest as attest_tool
from . import verify as verify_tool
from . import rate as rate_tool
from . import recommend as recommend_tool
from . import discover as discover_tool


def register_all(mcp) -> None:
    register_tool.register(mcp)
    attest_tool.register(mcp)
    verify_tool.register(mcp)
    rate_tool.register(mcp)
    recommend_tool.register(mcp)
    discover_tool.register(mcp)
