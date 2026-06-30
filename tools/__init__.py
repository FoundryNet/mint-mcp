"""MINT Protocol MCP tools. Each module exposes a `register(mcp)` that attaches
its @mcp.tool to the shared FastMCP instance, keeping one tool per file.

Sixteen tools:
  - Trust stack (11):
      FREE (write/discovery): identity, attestation, batch attestation, rating,
        recommendation, discovery, live feed.
      PAID (read the trust graph): verify ($0.005), trust_score ($0.01),
        trust_history ($0.25), trust_compare ($0.05).
    Pivot 2026-06-30 — attest FREE, verify PAID: writing grows the graph (the
    distribution channel); reading it is the product (read_gate.py).
  - FoundryNet on-chain (5): work cells (create / join / settle) + parametric
    insurance (create / settle), built against the devnet foundry_net program."""

from . import register as register_tool
from . import attest as attest_tool
from . import batch_attest as batch_attest_tool
from . import verify as verify_tool
from . import trust_score as trust_score_tool
from . import trust_history as trust_history_tool
from . import trust_compare as trust_compare_tool
from . import feed as feed_tool
from . import rate as rate_tool
from . import recommend as recommend_tool
from . import discover as discover_tool
from . import cell_create as cell_create_tool
from . import cell_join as cell_join_tool
from . import cell_settle as cell_settle_tool
from . import policy_create as policy_create_tool
from . import policy_settle as policy_settle_tool


def register_all(mcp) -> None:
    # FREE — write + discovery (the distribution channel)
    register_tool.register(mcp)
    attest_tool.register(mcp)
    batch_attest_tool.register(mcp)
    feed_tool.register(mcp)
    rate_tool.register(mcp)
    recommend_tool.register(mcp)
    discover_tool.register(mcp)
    # PAID — read the trust graph (the product; read_gate.py)
    verify_tool.register(mcp)
    trust_score_tool.register(mcp)
    trust_history_tool.register(mcp)
    trust_compare_tool.register(mcp)
    # FoundryNet work cells + parametric insurance (devnet foundry_net program)
    cell_create_tool.register(mcp)
    cell_join_tool.register(mcp)
    cell_settle_tool.register(mcp)
    policy_create_tool.register(mcp)
    policy_settle_tool.register(mcp)
