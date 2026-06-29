"""MINT Protocol — Universal Work Attestation for Autonomous Agents.

A lean, standalone MCP server (FastAPI + SSE) exposing exactly three tools to any
autonomous agent, machine, or service:

  mint_register  — give an actor a persistent cryptographic identity   (FREE)
  mint_attest    — anchor a tamper-evident record of completed work     (2¢)
  mint_verify    — query an actor's trust score + verified work history (FREE)

It rebuilds nothing and holds no relay key: identity reuses Forge /v1/identify,
attestation reuses Forge /v1/attest (Forge is the single relay key-holder +
settlement engine). Agents are the users — no web UI, no dashboard. Free to
register, free to verify, 2¢ to attest; the network grows on free identity +
free reputation, revenue comes from attestation volume.

Transport: Streamable HTTP at /mcp (remote Railway hosting + Smithery's hosted
gateway, which connects via Streamable HTTP — SSE 405s there). Health: GET /health.
Discovery: /.well-known/agent-card.json, /.well-known/mcp[/server-card.json].
"""
from __future__ import annotations

import inspect
import logging
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

import config
import core
import docs
import forge_client
import merkle_batch
import ml_scorer
import payment_gate
import supa
import tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mint.mcp")

if not forge_client.configured():
    logger.warning("FORGE_API_KEY not set — all three tools will return "
                   "not_configured until it's set in the Railway dashboard.")

mcp = FastMCP("mint-protocol")

# Pay-per-attest gating lives in core.do_attest (payment_gate.py) so the SAME
# policy backs both the MCP tool and the REST route — no middleware to drift, and
# no x402[svm] import that could crash-loop at boot. (The legacy facilitator
# middleware in x402_gate.py is superseded and no longer wired.)
if payment_gate.is_active():
    logger.info(f"pay-per-attest ARMED: {config.ATTEST_PRICE_USDC} USDC → "
                f"{config.PAYMENT_RECIPIENT} (rpc={config.PAYMENT_VERIFY_RPC})")
else:
    logger.info("pay-per-attest INERT (X402_ENABLED off or PAYMENT_RECIPIENT unset) "
                "— mint_attest is free")

# Merkle batch anchoring: attestations are recorded off-chain and anchored one
# merkle root per batch (one cheap Solana memo tx). The background anchorer is
# started/stopped in the app lifespan (below) so SIGTERM flushes the pending batch.
if config.MERKLE_ANCHOR_ENABLED:
    logger.info(f"merkle batch anchoring ON: batch_size={config.BATCH_SIZE}, "
                f"interval={config.BATCH_INTERVAL_SECONDS}s, "
                f"signer={'set' if config.ANCHOR_WALLET_KEYPAIR else 'UNSET (inert until configured)'}")
else:
    logger.info("merkle batch anchoring OFF (kill switch) — mint_attest settles "
                "per-attestation on-chain via Forge")

# Attach the six tools (one module each under tools/).
tools.register_all(mcp)


# ── okf-reliability-v1: emit reliability metadata on every tool result (#2964) ──
try:
    from okf_middleware import ReliabilityMiddleware
    mcp.add_middleware(ReliabilityMiddleware(server_id="mint-protocol"))
except Exception as _okf_e:  # noqa: BLE001
    import logging as _okf_log; _okf_log.getLogger(__name__).warning(f"okf middleware not wired: {_okf_e}")


@mcp.custom_route("/v1/reliability", methods=["GET"])
async def _okf_reliability_route(request):
    from starlette.responses import JSONResponse
    import okf_endpoint
    return JSONResponse(okf_endpoint.reliability_payload("mint-protocol"))


# ── Health ──────────────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness for Railway + load balancers. Reports config presence only;
    never leaks key values."""
    return JSONResponse({
        "status":            "ok",
        "service":           "mint-protocol-mcp",
        "transport":         "streamable-http",
        "tools":             ["mint_register", "mint_attest", "mint_verify",
                              "mint_rate", "mint_recommend", "mint_discover",
                              "mint_create_cell", "mint_join_cell", "mint_settle_cell",
                              "mint_create_policy", "mint_settle_policy"],
        "foundrynet_onchain": {
            "program_id": config.FOUNDRY_PROGRAM_ID,
            "cluster":    config.FOUNDRY_CLUSTER,
            "configured": bool((config.FOUNDRY_CELL_WALLET or "").strip())
                          and bool(config.FOUNDRY_STAKE_MINT),
        },
        "forge_api_url":     config.FORGE_API_URL,
        "forge_key_configured":  forge_client.configured(),
        "trust_store":       "supabase" if supa.configured() else "unconfigured",
        "trust_layer":       "live" if supa.configured() else "identity_only",
        "x402_enabled":      config.X402_ENABLED,
        "attest_payment":    "armed" if payment_gate.is_active() else "free",
        "attest_price_usdc": config.ATTEST_PRICE_USDC,
        "payment_recipient": config.PAYMENT_RECIPIENT,
        "payment_ledger":    "supabase" if supa.configured() else "in_memory",
        "merkle_anchoring":  ("on" if config.MERKLE_ANCHOR_ENABLED else "off"),
        "anchor_signer":     ("set" if config.ANCHOR_WALLET_KEYPAIR else "unset"),
        "scoring":           ml_scorer.model_info(),
        "batch_size":        config.BATCH_SIZE,
        "batch_interval_s":  config.BATCH_INTERVAL_SECONDS,
        "docs_url":          f"{docs.BASE_URL}/docs",
        "openapi_url":       f"{docs.BASE_URL}/openapi.json",
    })


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    """Liveness for hosted runtimes that probe /ping (mcp-proxy etc.)."""
    return JSONResponse({"status": "ok"})


# ── API reference (human + machine) ──────────────────────────────────────────
# Both generated from docs.ENDPOINTS so the prose and the spec never drift.

@mcp.custom_route("/docs", methods=["GET"])
async def api_docs(request: Request) -> HTMLResponse:
    """Styled HTML API reference — readable by a human in a browser AND fetchable
    by an agent for endpoint discovery."""
    return HTMLResponse(docs.render_docs(),
                        headers={"Cache-Control": "public, max-age=300"})


@mcp.custom_route("/openapi.json", methods=["GET"])
async def openapi_spec(request: Request) -> JSONResponse:
    """OpenAPI 3.0 spec — what Swagger/Postman/GPT Actions/agent frameworks import
    to auto-discover the REST API."""
    return JSONResponse(docs.build_openapi(),
                        headers={"Cache-Control": "public, max-age=300"})


# ── REST surface (for the mint-attest Python SDK + any HTTP client) ──────────
# The MCP tools speak SSE/JSON-RPC; these plain-HTTP routes expose the SAME three
# operations (via core.*, no logic drift) so a non-MCP client — the mint-attest
# PyPI SDK, curl, a webhook — can register/attest/verify with one POST. The
# developer's fnet_ key rides in `Authorization: Bearer` and is passed through to
# Forge so the actor + its attestations belong to THEIR account.

_ERR_STATUS = {"bad_request": 400, "not_configured": 503,
               "not_found": 404, "attest_failed": 502,
               "forbidden": 403, "conflict": 409,
               "rate_failed": 502, "recommend_failed": 502,
               "payment_required": 402}


def _resp(d: dict) -> JSONResponse:
    if "error" not in d:
        return JSONResponse(d, status_code=200)
    err = str(d.get("error") or "")
    if err in _ERR_STATUS:
        code = _ERR_STATUS[err]
    elif err.startswith("http_") and err[5:].isdigit():
        code = int(err[5:])                 # surface Forge's status (e.g. 401) verbatim
    elif err in ("network", "non_json_response", "unreachable"):
        code = 502
    else:
        code = 400
    return JSONResponse(d, status_code=code)


def _bearer(request: Request):
    a = request.headers.get("authorization", "")
    return a[7:].strip() if a.lower().startswith("bearer ") else None


async def _json_body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


@mcp.custom_route("/v1/register", methods=["POST"])
async def rest_register(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_register(
        b.get("actor_type", "ai_agent"), b.get("name", ""),
        b.get("capabilities"), b.get("operator"), b.get("metadata"),
        mcp_endpoint=b.get("mcp_endpoint"), description=b.get("description"),
        api_key=_bearer(request)))


@mcp.custom_route("/v1/attest", methods=["POST"])
async def rest_attest(request: Request) -> JSONResponse:
    # Pay-per-attest is enforced inside core.do_attest (payment_gate). When the
    # caller hasn't paid, core returns the {"error":"payment_required", ...} body
    # and _resp maps it to HTTP 402 — same policy as the MCP surface. An fnet_
    # Bearer key (Stripe-billed) bypasses payment; otherwise pass payment_tx.
    b = await _json_body(request)
    d = await core.do_attest(
        b.get("mint_id", ""), b.get("work_type", ""), b.get("duration_seconds", 0),
        summary=b.get("summary", ""), input_hash=b.get("input_hash"),
        output_hash=b.get("output_hash"), metadata=b.get("metadata"),
        payment_tx=b.get("payment_tx"), api_key=_bearer(request))
    return _resp(d)


@mcp.custom_route("/v1/verify", methods=["POST"])
async def rest_verify(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_verify(
        b.get("mint_id"), b.get("actor_name"), b.get("actor_type"),
        attestation_hash=b.get("attestation_hash")))


@mcp.custom_route("/v1/batch/status", methods=["GET"])
async def batch_status(request: Request) -> JSONResponse:
    """Merkle anchoring telemetry: current batch size, time to next anchor, last
    anchor tx, totals. Open (no auth) — it leaks no secrets."""
    return JSONResponse(await merkle_batch.status())


@mcp.custom_route("/v1/feed", methods=["GET", "OPTIONS"])
async def feed(request: Request) -> JSONResponse:
    """Public live attestation feed: the newest attestations across the whole
    network — originating server/agent, summary, trust score, ML confidence,
    anchor status, merkle root + Solscan link — plus showcase stats. FREE,
    CORS-open, short-cached. A public showcase, not a paid tool."""
    cors = {"Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Cache-Control": "public, max-age=15, s-maxage=15"}
    if request.method == "OPTIONS":
        return JSONResponse({}, status_code=204, headers=cors)
    try:
        limit = int(request.query_params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50
    items = await supa.recent_attestations(limit)
    stats = await supa.feed_stats()
    return JSONResponse({"attestations": items, "count": len(items), "stats": stats},
                        headers=cors)


@mcp.custom_route("/v1/rate", methods=["POST"])
async def rest_rate(request: Request) -> JSONResponse:
    # FREE; the Bearer fnet_ key identifies the rater (bound to an owned actor).
    b = await _json_body(request)
    return _resp(await core.do_rate(
        b.get("attestation_id", ""), b.get("rated_mint_id", ""), b.get("score"),
        rater_mint_id=b.get("rater_mint_id"), accuracy=b.get("accuracy", True),
        would_use_again=b.get("would_use_again", True), tags=b.get("tags"),
        comment=b.get("comment"), api_key=_bearer(request)))


@mcp.custom_route("/v1/recommend", methods=["POST"])
async def rest_recommend(request: Request) -> JSONResponse:
    # FREE; the Bearer fnet_ key identifies the recommender.
    b = await _json_body(request)
    return _resp(await core.do_recommend(
        b.get("recommended_mint_id", ""), b.get("context", ""), b.get("score"),
        note=b.get("note"), recommender_mint_id=b.get("recommender_mint_id"),
        attestation_id=b.get("attestation_id"), api_key=_bearer(request)))


@mcp.custom_route("/v1/discover", methods=["POST"])
async def rest_discover(request: Request) -> JSONResponse:
    # FREE, no auth — discovery is open to any agent.
    b = await _json_body(request)
    return _resp(await core.do_discover(
        capability=b.get("capability"), actor_type=b.get("actor_type"),
        min_trust_score=b.get("min_trust_score", 0),
        min_recommendations=b.get("min_recommendations", 0),
        sort_by=b.get("sort_by", "trust_score"), limit=b.get("limit", 10)))


# ── Discovery: A2A agent card ────────────────────────────────────────────────

_AGENT_CARD = {
    "name": "MINT Protocol",
    "description": (
        "Universal work attestation for autonomous agents. Register identity, "
        "attest completed work, verify trust scores. The reputation layer for "
        "the agent economy."
    ),
    "url": "https://mint.foundrynet.io",
    "live_feed": "https://mint.foundrynet.io/feed",
    "feed_api": "https://mint-mcp-production.up.railway.app/v1/feed",
    "capabilities": [
        "agent_identity",
        "work_attestation",
        "trust_verification",
        "reputation_scoring",
        "peer_rating",
        "peer_recommendation",
        "trust_ranked_discovery",
    ],
    "tools": [
        {"name": "mint_register",
         "description": "Register any autonomous actor with persistent cryptographic identity",
         "pricing": "free"},
        {"name": "mint_attest",
         "description": "Attest completed work with tamper-evident on-chain record",
         "pricing": "0.02 USDC per attestation"},
        {"name": "mint_verify",
         "description": "Query any actor's full trust profile and verified work history",
         "pricing": "free"},
        {"name": "mint_rate",
         "description": "Rate a completed attestation 1-5; updates the actor's trust score",
         "pricing": "free"},
        {"name": "mint_recommend",
         "description": "Endorse an actor you've worked with in a named context",
         "pricing": "free"},
        {"name": "mint_discover",
         "description": "Trust-ranked search of the actor directory by capability",
         "pricing": "free"},
        {"name": "mint_create_cell",
         "description": "Open a stake-backed on-chain work cell (FoundryNet devnet)",
         "pricing": "network fee"},
        {"name": "mint_join_cell",
         "description": "Join a work cell by staking; opens participant + trust accounts",
         "pricing": "network fee"},
        {"name": "mint_settle_cell",
         "description": "Evaluate + settle a work cell; 96/2/2 split, stakes returned, trust updated",
         "pricing": "network fee"},
        {"name": "mint_create_policy",
         "description": "Open a parametric insurance policy with funded coverage escrow",
         "pricing": "network fee"},
        {"name": "mint_settle_policy",
         "description": "Settle a policy: pay beneficiary if triggered, else refund insurer",
         "pricing": "network fee"},
    ],
    "protocols": {
        "mcp": {
            "endpoint": config.PUBLIC_MCP_URL,
            "transport": "streamable-http",
            "tools_count": 11,
        },
        "x402": {
            "supported": True,
            "currency": "USDC",
            "network": "solana",
        },
    },
    "contact": "hello@foundrynet.io",
}


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request) -> JSONResponse:
    """A2A agent card — how other agents discover MINT's identity + tools."""
    return JSONResponse(_AGENT_CARD, headers={"Cache-Control": "public, max-age=300"})


# ── Discovery: well-known/mcp directory crawlers ─────────────────────────────

@mcp.custom_route("/.well-known/mcp", methods=["GET"])
async def mcp_endpoints(request: Request) -> JSONResponse:
    return JSONResponse(
        {"endpoints": [{
            "url":       config.PUBLIC_MCP_URL,
            "transport": "streamable-http",
            "name":      "MINT Protocol MCP",
        }]},
        headers={"Cache-Control": "public, max-age=300"},
    )


async def _live_tools() -> list:
    """The registered tools in MCP-SDK shape ({name, description, inputSchema}),
    built from FastMCP's live definitions so the card never drifts from the code."""
    res = mcp.list_tools()
    if inspect.iscoroutine(res):
        res = await res
    out = []
    for t in res:
        out.append({
            "name": t.name,
            "description": (getattr(t, "description", "") or "").strip(),
            "inputSchema": getattr(t, "parameters", None) or {"type": "object"},
        })
    return out


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def server_card(request: Request) -> JSONResponse:
    """MCP server card consumed by directories (Smithery, glama, pulsemcp).

    Smithery's publish flow reads `serverInfo` + `tools` (MCP-SDK types) from this
    card to SKIP its connection scan, so the listing populates its tools without a
    live probe. The server itself speaks Streamable HTTP at /mcp (what Smithery's
    hosted gateway connects with). Extra fields (tagline, pricing, categories) are
    ignored by Smithery and used by other directories."""
    tools = await _live_tools()
    return JSONResponse(
        {
            # ── Smithery-required (and MCP-canonical) ──
            "serverInfo": {"name": "MINT Protocol — Universal Work Attestation",
                           "version": "1.1.0"},
            "authentication": {
                "type": "http", "scheme": "bearer",
                "description": ("mint_register and mint_verify are free and need no "
                                "auth; mint_attest takes an fnet_ Bearer key OR an "
                                "x402 USDC payment."),
            },
            "tools": tools,
            # ── extra discovery metadata (other directories) ──
            "version": "1.0",
            "name": "MINT Protocol — Universal Work Attestation",
            "tagline": "The reputation layer for the agent economy.",
            "description": (
                "Register, attest, and verify work for any autonomous agent — "
                "AI agents, physical machines, IoT devices, services. Persistent "
                "cryptographic identity, tamper-evident on-chain (Solana) work "
                "records, and trust scores built from verified history. Free to "
                "register, free to verify, 2¢ to attest."
            ),
            "serverUrl": config.PUBLIC_MCP_URL,
            "transport": "streamable-http",
            "tools_count": len(tools),
            "categories": ["agents", "identity", "reputation", "attestation", "blockchain"],
            "pricing": {
                "model": "metered",
                "free_tier": "Unlimited register + verify, no card",
                "paid_from": "0.02 USDC per attestation (x402)",
            },
            # API reference (human HTML) + machine-readable spec — so any agent
            # fetching the server card auto-discovers how to call the REST API.
            "docs_url": f"{docs.BASE_URL}/docs",
            "openapi_url": f"{docs.BASE_URL}/openapi.json",
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def wellknown_mcp_json(request: Request) -> JSONResponse:
    """Machine-discovery card (emerging standard) for AI clients/crawlers."""
    live = await _live_tools()
    names = [t["name"] for t in live]
    return JSONResponse({
        "name": "MINT Protocol — Universal Work Attestation",
        "description": ("Register, attest, and verify work for any autonomous agent — "
                        "the reputation layer for the agent economy."),
        "url": config.PUBLIC_MCP_URL,
        "transport": ["streamable-http"],
        "tools": names,
        "pricing": {"model": "per-query", "free_tier": True,
                    "paid_tools": ["mint_attest"]},
        "attestation": {"enabled": True, "protocol": "MINT Protocol",
                        "feed": "https://mint.foundrynet.io/feed"},
        "network": {"name": "FoundryNet Data Network", "servers": 17,
                    "homepage": "https://foundrynet.io"},
    }, headers={"Cache-Control": "public, max-age=300"})


# ── Entrypoint ───────────────────────────────────────────────────────────────

def build_dual_app():
    """Serve BOTH transports from one app:
      • Streamable HTTP at /mcp   — modern clients + Smithery's hosted gateway
      • legacy SSE at /sse (+ /messages) — existing mcp-remote configs keep working
    The streamable-http app is primary (carries /mcp + every custom_route); we graft
    only the two SSE transport routes onto it and chain both lifespans so each
    transport's session manager starts. New users get /mcp; old users don't break."""
    import contextlib
    main_app = mcp.http_app(transport="http", path="/mcp")   # /mcp + custom routes
    sse_app = mcp.http_app(transport="sse", path="/sse")      # /sse + /messages (+ dup customs)
    for r in sse_app.routes:
        if getattr(r, "path", None) in ("/sse", "/messages"):
            main_app.router.routes.append(r)
    main_life, sse_life = main_app.router.lifespan_context, sse_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _dual_lifespan(app):
        async with main_life(app):
            async with sse_life(app):
                # Start the merkle batch anchorer (no-op if MERKLE_ANCHOR_ENABLED is
                # off). On shutdown — including uvicorn's SIGTERM-driven lifespan
                # teardown — stop() flushes the pending batch before we exit.
                await merkle_batch.start()
                try:
                    yield
                finally:
                    await merkle_batch.stop()
    main_app.router.lifespan_context = _dual_lifespan
    return main_app


if __name__ == "__main__":
    import uvicorn
    logger.info(
        f"MINT Protocol MCP starting on 0.0.0.0:{config.PORT} "
        f"(forge={config.FORGE_API_URL}, forge_key={forge_client.configured()}, "
        f"x402={config.X402_ENABLED}) — dual transport: /mcp (streamable-http) + /sse (legacy)"
    )
    uvicorn.run(build_dual_app(), host="0.0.0.0", port=config.PORT, log_level="warning")
