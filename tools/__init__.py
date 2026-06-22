"""MINT Protocol MCP tools. Each module exposes a `register(mcp)` that attaches
its @mcp.tool to the shared FastMCP instance, keeping one tool per file.

Eleven tools:
  - Trust stack (6): identity, attestation, verification, rating, recommendation,
    discovery.
  - FoundryNet on-chain (5): work cells (create / join / settle) + parametric
    insurance (create / settle), built against the devnet foundry_net program."""

from . import register as register_tool
from . import attest as attest_tool
from . import verify as verify_tool
from . import rate as rate_tool
from . import recommend as recommend_tool
from . import discover as discover_tool
from . import cell_create as cell_create_tool
from . import cell_join as cell_join_tool
from . import cell_settle as cell_settle_tool
from . import policy_create as policy_create_tool
from . import policy_settle as policy_settle_tool


def register_all(mcp) -> None:
    register_tool.register(mcp)
    attest_tool.register(mcp)
    verify_tool.register(mcp)
    rate_tool.register(mcp)
    recommend_tool.register(mcp)
    discover_tool.register(mcp)
    # FoundryNet work cells + parametric insurance (devnet foundry_net program)
    cell_create_tool.register(mcp)
    cell_join_tool.register(mcp)
    cell_settle_tool.register(mcp)
    policy_create_tool.register(mcp)
    policy_settle_tool.register(mcp)
