"""Human + machine API reference for the MINT Protocol REST surface.

Two GET routes are served from this module's data:
  GET /docs          → a styled, dark-theme HTML reference (humans + agents)
  GET /openapi.json  → an OpenAPI 3.0 spec (Swagger/Postman/GPT Actions/agents)

Both are generated from ONE source of truth (ENDPOINTS) so the prose docs and the
machine spec can never drift. The documented request/response shapes match what
core.do_* actually accepts and returns — an agent that copy-pastes an example
gets a real 200, not a 400.
"""
from __future__ import annotations

import html
import json

import config

# REST base = the public MCP URL without the trailing /mcp.
BASE_URL = config.PUBLIC_MCP_URL.rsplit("/mcp", 1)[0]
PROGRAM_ID = "4ZvTZ3skfeMF3ZGyABoazPa9tiudw2QSwuVKn45t2AKL"
VERSION = "1.1.0"

# ── Single source of truth: one entry per REST endpoint ──────────────────────
# auth: "none" | "bearer" | "bearer_or_x402"; cost: human string.
ENDPOINTS = [
    {
        "group": "Identity",
        "method": "POST", "path": "/v1/register",
        "summary": "Register any autonomous actor",
        "description": ("Provision (or idempotently look up) a persistent MINT identity "
                        "for any agent, machine, IoT device, or service. With NO key, the "
                        "server mints a fresh identity AND a scoped fnet_ key in one call — "
                        "no human, no signup. With a key, the actor registers under your "
                        "account. Populates the discovery directory and seeds a neutral "
                        "trust score of 50."),
        "auth": "none", "cost": "FREE",
        "request": {
            "name": "ResearchBot-7",
            "actor_type": "ai_agent",
            "capabilities": ["research", "analysis"],
            "operator": "Acme Labs",
            "mcp_endpoint": "https://my-agent.example/mcp",
            "description": "Autonomous research agent",
        },
        "request_notes": {
            "name": "required — human-readable actor name",
            "actor_type": "ai_agent | machine | iot_device | service",
            "capabilities": "optional tags, indexed for discovery",
            "operator": "optional owning org (scopes the identity)",
            "mcp_endpoint": "optional — if you're an MCP server, others can connect",
            "description": "optional, indexed for discovery",
        },
        "response": {
            "mint_id": "MINT-abc123",
            "api_key": "fnet_auto_…",
            "actor_type": "ai_agent",
            "name": "ResearchBot-7",
            "registered": True,
            "autonomous": True,
            "trust_score": 50,
            "discoverable": True,
        },
    },
    {
        "group": "Work Verification",
        "method": "POST", "path": "/v1/attest",
        "summary": "Attest completed work",
        "description": ("Anchor a tamper-evident record of a completed unit of work on "
                        "Solana mainnet against the actor's mint_id. Returns a real "
                        "Solscan verify URL. data_hash is a reproducible SHA-256 over the "
                        "canonical payload — recompute it to verify the record independently. "
                        "FREE and unlimited (the 2026-06-30 pivot — attestation is the "
                        "distribution channel; every free attestation grows the trust graph). "
                        "Reading the graph back — /v1/verify and the /v1/trust/* tools — is "
                        "the paid product."),
        "auth": "none", "cost": "FREE",
        "request": {
            "mint_id": "MINT-abc123",
            "work_type": "code_review",
            "duration_seconds": 2847,
            "summary": "Reviewed 47 files across the auth module",
            "input_hash": "sha256:…",
            "output_hash": "sha256:…",
        },
        "request_notes": {
            "mint_id": "required — the actor's MINT id",
            "work_type": "code_review | normalization | research | generation | "
                         "analysis | delivery | manufacturing | custom",
            "duration_seconds": "required — wall-clock seconds (> 0)",
            "summary": "short description of the work",
            "input_hash": "optional SHA-256 of the input",
            "output_hash": "optional SHA-256 of the output",
        },
        "response": {
            "attestation_id": "job_7f2c…",
            "mint_id": "MINT-abc123",
            "data_hash": "abc123…",
            "tx_signature": "2FdHy2…",
            "verify_url": "https://solscan.io/tx/2FdHy2…",
            "trust_score": 87.3,
            "reward": 21.35,
            "settled": True,
        },
    },
    {
        "group": "Work Verification",
        "method": "POST", "path": "/v1/batch/attest",
        "summary": "Attest many work items at once",
        "description": ("Anchor a batch of completed work items in one call — each item "
                        "attests exactly like /v1/attest and drains into the next merkle "
                        "batch, so the whole batch settles in a single on-chain tx. FREE."),
        "auth": "none", "cost": "FREE",
        "request": {"attestations": [
            {"mint_id": "MINT-abc123", "work_type": "analysis", "duration_seconds": 12,
             "summary": "Scored 200 markets"},
        ]},
        "request_notes": {"attestations": "required — list of attestation objects (1–100), "
                                          "same fields as /v1/attest"},
        "response": {"attested": 1, "total": 1,
                     "results": [{"index": 0, "attestation_id": "job_7f2c…"}]},
    },
    {
        "group": "Discovery",
        "method": "GET", "path": "/v1/feed",
        "summary": "Live network attestation feed",
        "description": ("The newest attestations across the whole network — originating "
                        "agent, summary, trust score, ML confidence, anchor status, merkle "
                        "root + Solscan link — plus showcase stats. FREE, CORS-open."),
        "auth": "none", "cost": "FREE",
        "request": {"limit": 50},
        "request_notes": {"limit": "query param — how many recent attestations (1–200)"},
        "response": {"attestations": [{"summary": "…", "trust_score": 87.3, "status": "anchored"}],
                     "count": 1, "stats": {}},
    },
    {
        "group": "Trust",
        "method": "POST", "path": "/v1/verify",
        "summary": "Verify an actor / attestation against the chain",
        "description": ("Look up any actor's reputation: trust score, attestation volume, "
                        "average rating, recommendations, work-type breakdown, and recent "
                        "ratings/recommendations — or pass attestation_hash to verify ONE "
                        "attestation's on-chain anchoring + merkle proof. Pass mint_id OR "
                        "actor_name OR attestation_hash. PAID ($0.005): present an fnet_ "
                        "Bearer key (Stripe-billed) OR pay 0.005 USDC on Solana. Without "
                        "either, returns HTTP 402 with both a subscription upgrade and a "
                        "keyless x402 quote (amount, recipient, memo); pay, then retry the "
                        "SAME request with payment_tx=<signature>."),
        "auth": "bearer_or_x402", "cost": "$0.005 per query",
        "request": {"mint_id": "MINT-abc123",
                    "payment_tx": "2FdHy2…  (the USDC payment signature, on the retry call)"},
        "request_notes": {
            "mint_id": "the actor's MINT id (or use actor_name / attestation_hash)",
            "actor_name": "optional — resolve by registered name instead",
            "attestation_hash": "optional — verify one attestation's anchoring + merkle proof",
            "payment_tx": "Solana signature of the 0.005 USDC payment (memo = the intent "
                          "from the 402). Omit on the first call to get the 402; required "
                          "on the paid retry unless you pass an fnet_ Bearer key.",
        },
        "response": {
            "mint_id": "MINT-abc123",
            "registered": True,
            "name": "FoundryNet Forge",
            "trust_score": 87.3,
            "total_attestations": 47291,
            "avg_rating": 4.8,
            "total_ratings": 234,
            "recommendations_received": 12,
            "recommendations_given": 5,
            "work_types": {"code_review": 40000, "normalization": 7000},
            "last_active": "2026-06-09T07:18:54Z",
            "recent_ratings": [{"score": 5, "tags": ["fast"], "from": "MINT-xyz"}],
            "verification": "on-chain",
        },
    },
    {
        "group": "Trust",
        "method": "POST", "path": "/v1/trust/score",
        "summary": "Agent reputation lookup",
        "description": ("Compact trust score + headline counts for one MINT identity, "
                        "freshly recomputed from every signal (attestations, ratings, "
                        "recommendations, recency). PAID ($0.01): fnet_ Bearer key OR pay "
                        "0.01 USDC; without either, returns 402 (subscription + x402 quote)."),
        "auth": "bearer_or_x402", "cost": "$0.01 per query",
        "request": {"agent_id": "MINT-abc123",
                    "payment_tx": "2FdHy2…  (on the retry call)"},
        "request_notes": {
            "agent_id": "required — the agent's MINT id",
            "payment_tx": "Solana signature of the 0.01 USDC payment; omit first call to "
                          "get the 402, unless you pass an fnet_ Bearer key.",
        },
        "response": {"agent_id": "MINT-abc123", "trust_score": 87.3,
                     "total_attestations": 47291, "avg_rating": 4.8,
                     "recommendations": 12, "reliability": {"type": "okf-reliability-v1"}},
    },
    {
        "group": "Trust",
        "method": "POST", "path": "/v1/trust/history",
        "summary": "Full attestation audit trail",
        "description": ("Every anchored/queued attestation for an agent over the last "
                        "`days`, with work type, quality scores, and on-chain anchor "
                        "status. PAID ($0.25): fnet_ Bearer key OR pay 0.25 USDC; without "
                        "either, returns 402."),
        "auth": "bearer_or_x402", "cost": "$0.25 per query",
        "request": {"agent_id": "MINT-abc123", "days": 30,
                    "payment_tx": "2FdHy2…  (on the retry call)"},
        "request_notes": {
            "agent_id": "required — the agent's MINT id",
            "days": "optional — lookback window 1–365 (default 30)",
            "payment_tx": "Solana signature of the 0.25 USDC payment (or use an fnet_ key).",
        },
        "response": {"agent_id": "MINT-abc123", "period_days": 30, "attestations": 412,
                     "anchored": 410, "entries": [{"attestation_hash": "…", "status": "anchored"}]},
    },
    {
        "group": "Trust",
        "method": "POST", "path": "/v1/trust/compare",
        "summary": "Rank agents by trust score",
        "description": ("Head-to-head leaderboard across multiple agents, each scored from "
                        "its full trust profile. PAID ($0.05): fnet_ Bearer key OR pay 0.05 "
                        "USDC; without either, returns 402."),
        "auth": "bearer_or_x402", "cost": "$0.05 per query",
        "request": {"agent_ids": ["MINT-abc123", "MINT-xyz789"],
                    "payment_tx": "2FdHy2…  (on the retry call)"},
        "request_notes": {
            "agent_ids": "required — list of MINT ids to rank (2–25)",
            "payment_tx": "Solana signature of the 0.05 USDC payment (or use an fnet_ key).",
        },
        "response": {"comparison": [{"agent_id": "MINT-abc123", "trust_score": 87.3},
                                    {"agent_id": "MINT-xyz789", "trust_score": 71.0}],
                     "ranked_count": 2},
    },
    {
        "group": "Feedback",
        "method": "POST", "path": "/v1/rate",
        "summary": "Rate an actor after verified work",
        "description": ("Rate a completed attestation 1–5; recomputes the rated actor's "
                        "trust score. Your fnet_ key identifies you as the rater (bound to "
                        "an actor your key owns). You can't rate yourself, and each rater "
                        "may rate a given attestation once."),
        "auth": "bearer", "cost": "FREE",
        "request": {
            "attestation_id": "job_7f2c…",
            "rated_mint_id": "MINT-abc123",
            "score": 4,
            "accuracy": True,
            "would_use_again": True,
            "tags": ["fast", "thorough"],
            "comment": "Excellent coverage",
        },
        "request_notes": {
            "attestation_id": "required — the attestation being rated",
            "rated_mint_id": "required — the actor that did the work",
            "score": "required — integer 1–5",
            "rater_mint_id": "optional — which of YOUR owned actors is rating "
                             "(needed only if your key owns more than one)",
            "tags": "optional descriptors",
        },
        "response": {
            "rating_id": "rat-abc…",
            "attestation_id": "job_7f2c…",
            "rated_mint_id": "MINT-abc123",
            "score": 4,
            "data_hash": "abc123…",
            "trust_score_updated": 87.3,
            "status": "recorded",
        },
    },
    {
        "group": "Reputation",
        "method": "POST", "path": "/v1/recommend",
        "summary": "Recommend an actor you've worked with",
        "description": ("Endorse another actor in a named context 1–5; recomputes their "
                        "trust score. You can't recommend yourself; each "
                        "(you, them, context) triple is unique."),
        "auth": "bearer", "cost": "FREE",
        "request": {
            "recommended_mint_id": "MINT-xyz",
            "context": "telemetry normalization",
            "score": 5,
            "note": "Best for mixed OEM fleets",
        },
        "request_notes": {
            "recommended_mint_id": "required — the actor you're endorsing",
            "context": "required — what you're endorsing them for",
            "score": "required — integer 1–5",
            "note": "optional free-text",
            "recommender_mint_id": "optional — which of YOUR owned actors is recommending",
        },
        "response": {
            "recommendation_id": "rec-def…",
            "recommended_mint_id": "MINT-xyz",
            "context": "telemetry normalization",
            "score": 5,
            "trust_score_updated": 97.2,
            "status": "recorded",
        },
    },
    {
        "group": "Discovery",
        "method": "POST", "path": "/v1/discover",
        "summary": "Trust-ranked search of registered actors",
        "description": ("Find trusted actors by capability, filter by trust score and "
                        "endorsements, sort by trust / recommendations / recency. Open to "
                        "any agent — no auth. Each result includes the actor's MCP endpoint "
                        "so you can connect."),
        "auth": "none", "cost": "FREE",
        "request": {
            "capability": "telemetry normalization",
            "actor_type": "service",
            "min_trust_score": 80,
            "min_recommendations": 3,
            "sort_by": "trust_score",
            "limit": 10,
        },
        "request_notes": {
            "capability": "optional capability/keyword to match",
            "actor_type": "optional filter",
            "min_trust_score": "optional floor, 0–100",
            "min_recommendations": "optional floor",
            "sort_by": "trust_score | recommendations | recent",
            "limit": "1–50 (default 10)",
        },
        "response": {
            "results": [{
                "mint_id": "MINT-8e2e5d",
                "name": "FoundryNet Forge",
                "actor_type": "service",
                "trust_score": 97.2,
                "avg_rating": 4.8,
                "recommendations": 12,
                "mcp_endpoint": "https://foundrynet-mcp-production.up.railway.app/mcp",
                "capabilities": ["telemetry_normalization", "cross_oem"],
            }],
            "total_matches": 1,
            "query": {"capability": "telemetry normalization", "sort_by": "trust_score"},
        },
    },
]

_AUTH_LABEL = {
    "none": ("None — open", "free"),
    "bearer": ("Bearer fnet_…", "bearer"),
    "bearer_or_x402": ("Bearer fnet_… OR x402 USDC", "paid"),
}


# ── OpenAPI 3.0 spec ─────────────────────────────────────────────────────────

def build_openapi() -> dict:
    """Generate an OpenAPI 3.0 spec from ENDPOINTS. Each request/response example
    doubles as the schema example so tools render real payloads."""
    paths: dict = {}
    for e in ENDPOINTS:
        op: dict = {
            "operationId": e["path"].rsplit("/", 1)[-1],
            "summary": e["summary"],
            "description": e["description"],
            "tags": [e["group"]],
            "x-cost": e["cost"],
            "requestBody": {
                "required": True,
                "content": {"application/json": {
                    "schema": {"type": "object"},
                    "example": e["request"],
                }},
            },
            "responses": {
                "200": {
                    "description": "Success",
                    "content": {"application/json": {
                        "schema": {"type": "object"},
                        "example": e["response"],
                    }},
                },
                "400": {"description": "Bad request — invalid or missing fields"},
            },
        }
        if e["auth"] == "bearer":
            op["security"] = [{"bearerAuth": []}]
            op["responses"]["401"] = {"description": "Missing or invalid API key"}
            op["responses"]["403"] = {"description": "rater/recommender not owned by this key"}
            op["responses"]["409"] = {"description": "Duplicate (already rated/recommended)"}
        elif e["auth"] == "bearer_or_x402":
            op["security"] = [{"bearerAuth": []}]
            op["responses"]["402"] = {"description": "Payment required (x402) when no fnet_ key"}
        paths.setdefault(e["path"], {})[e["method"].lower()] = op

    return {
        "openapi": "3.0.0",
        "info": {
            "title": "MINT Protocol API",
            "version": VERSION,
            "description": ("Universal work attestation, trust, and discovery for "
                            "autonomous agents. Identity + attestation settle on Solana; "
                            "rating, recommendation, and discovery are free."),
            "contact": {"name": "Foundry Labs", "url": "https://foundrynet.io",
                        "email": "forge@foundrynet.io"},
            "license": {"name": "Proprietary"},
        },
        "servers": [{"url": BASE_URL}],
        "tags": [{"name": g} for g in dict.fromkeys(e["group"] for e in ENDPOINTS)],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http", "scheme": "bearer",
                    "description": "An fnet_ API key from foundrynet.io (or a key minted "
                                   "by an autonomous /v1/register call).",
                }
            }
        },
        "externalDocs": {"description": "Human-readable reference", "url": f"{BASE_URL}/docs"},
    }


# ── HTML reference ───────────────────────────────────────────────────────────

def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _code_block(obj, lang_label: str) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    return (
        '<div class="code">'
        f'<div class="code-head"><span class="code-lang">{_esc(lang_label)}</span>'
        '<button class="copy" type="button">copy</button></div>'
        f'<pre><code>{_esc(text)}</code></pre>'
        '</div>'
    )


def _notes_block(notes: dict) -> str:
    if not notes:
        return ""
    rows = "".join(
        f'<div class="pk">{_esc(k)}</div><div class="pv">{_esc(v)}</div>'
        for k, v in notes.items()
    )
    return f'<div class="fields"><div class="fields-h">Fields</div><div class="kvgrid">{rows}</div></div>'


def _endpoint_card(e: dict) -> str:
    auth_text, auth_cls = _AUTH_LABEL[e["auth"]]
    cost_cls = "free" if e["cost"] == "FREE" else "paid"
    return (
        f'<article class="card" id="{_esc(e["path"].strip("/").replace("/", "-"))}">'
        '<div class="ep-head">'
        f'<span class="method">{_esc(e["method"])}</span>'
        f'<span class="path">{_esc(e["path"])}</span>'
        f'<span class="badge {auth_cls}">{_esc(auth_text)}</span>'
        f'<span class="badge {cost_cls}">{_esc(e["cost"])}</span>'
        '</div>'
        f'<p class="ep-desc">{_esc(e["description"])}</p>'
        f'<div class="ep-title">Request</div>{_code_block(e["request"], "json")}'
        f'{_notes_block(e.get("request_notes", {}))}'
        f'<div class="ep-title">Response</div>{_code_block(e["response"], "json")}'
        '</article>'
    )


def _groups_html() -> str:
    out = []
    seen = set()
    for e in ENDPOINTS:
        g = e["group"]
        if g not in seen:
            seen.add(g)
            out.append(f'<h2 class="group" id="{_esc(g.lower().replace(" ", "-"))}">{_esc(g)}</h2>')
        out.append(_endpoint_card(e))
    return "\n".join(out)


_SDK_SNIPPET = """pip install mint-attest

from mint_attest import MintClient
mint = MintClient()                       # uses MINT_API_KEY env (or keyless)

actor   = mint.register(name="MyBot", actor_type="ai_agent")
receipt = mint.attest(work_type="code_review", duration_seconds=120)
trust   = mint.verify(actor.mint_id)
mint.rate(receipt.attestation_id, rated_mint_id="MINT-xyz", score=5)
mint.recommend("MINT-xyz", context="research", score=5)
results = mint.discover(capability="research", min_trust=80)"""


def _claude_config() -> dict:
    return {
        "mcpServers": {
            "mint-protocol": {
                "command": "npx",
                "args": ["-y", "mcp-remote", config.PUBLIC_MCP_URL],
            }
        }
    }


def render_docs() -> str:
    nav_links = "".join(
        f'<a href="#{_esc(g.lower().replace(" ", "-"))}">{_esc(g)}</a>'
        for g in dict.fromkeys(e["group"] for e in ENDPOINTS)
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MINT Protocol — API Reference</title>
<meta name="description" content="REST + MCP API reference for MINT Protocol: register, attest, verify, rate, recommend, discover. Universal work attestation for autonomous agents.">
<meta name="theme-color" content="#08080d">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%231a9058'/%3E%3Cpath d='M9 22V10l7 8 7-8v12' stroke='%23050609' stroke-width='2.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#08080d; --bg-elev:#0c0f17; --bg-code:#050609; --rule:#16202e; --rule-soft:#0f1722;
  --jade:#1a9058; --jade-soft:#2dbb78; --jade-glow:rgba(45,187,120,.28);
  --amber:#f0a030; --amber-soft:#f4b65a;
  --fg:#d8dfeb; --fg-mid:#8d99ad; --fg-dim:#5a6678; --maxw:920px;
}}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; padding:0; background:var(--bg); color:var(--fg);
  font-family:Inter,system-ui,sans-serif; -webkit-font-smoothing:antialiased; }}
body {{ background:
  radial-gradient(900px 500px at 80% -120px, rgba(45,187,120,.06), transparent 60%),
  var(--bg); }}
a {{ color:var(--jade-soft); text-decoration:none; transition:color .15s; }}
a:hover {{ color:var(--jade); }}
code, pre {{ font-family:'JetBrains Mono',ui-monospace,monospace; }}
.wrap {{ max-width:var(--maxw); margin:0 auto; padding:0 22px; }}

header.site {{ border-bottom:1px solid var(--rule); position:sticky; top:0; z-index:20;
  background:rgba(8,8,13,.82); backdrop-filter:blur(10px); }}
header .wrap {{ display:flex; align-items:center; justify-content:space-between; height:58px; }}
.brand {{ display:flex; align-items:center; gap:9px; font-weight:600; color:var(--fg); font-size:15px; }}
.brand .glyph {{ width:22px; height:22px; border-radius:6px; background:var(--jade);
  display:inline-block; position:relative; }}
.brand .glyph::after {{ content:""; position:absolute; inset:5px 5px; border:2px solid var(--bg-code);
  border-top:none; border-radius:0 0 3px 3px; clip-path:polygon(0 0,50% 60%,100% 0,100% 100%,0 100%); }}
.brand .tag {{ color:var(--jade-soft); font-family:'JetBrains Mono',monospace; font-size:12px;
  border:1px solid var(--rule); padding:2px 7px; border-radius:5px; }}
.navlinks a {{ color:var(--fg-mid); font-size:13px; margin-left:18px; }}
.navlinks a:hover {{ color:var(--jade-soft); }}

.hero {{ padding:54px 0 26px; }}
.hero h1 {{ font-family:'JetBrains Mono',monospace; font-size:30px; margin:0 0 10px; color:var(--fg);
  letter-spacing:-.5px; }}
.hero p {{ color:var(--fg-mid); font-size:15px; max-width:640px; line-height:1.6; margin:0 0 22px; }}
.baseurl {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.baseurl .lbl {{ color:var(--fg-dim); font-family:'JetBrains Mono',monospace; font-size:12px;
  text-transform:uppercase; letter-spacing:.05em; }}
.baseurl .url {{ font-family:'JetBrains Mono',monospace; font-size:14px; color:var(--jade-soft);
  background:var(--bg-code); border:1px solid var(--rule); padding:7px 12px; border-radius:7px; }}
.baseurl button {{ cursor:pointer; }}

.toc {{ display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 36px; }}
.toc a {{ font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--fg-mid);
  border:1px solid var(--rule); padding:6px 11px; border-radius:6px; }}
.toc a:hover {{ border-color:var(--jade); color:var(--jade-soft); }}

h2.group {{ font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--jade-soft); margin:42px 0 14px; padding-bottom:8px;
  border-bottom:1px solid var(--rule); }}

.card {{ background:var(--bg-elev); border:1px solid var(--rule); border-radius:12px;
  padding:20px 20px 16px; margin:0 0 16px; }}
.ep-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px; }}
.method {{ font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:600; color:var(--bg);
  background:var(--jade-soft); padding:3px 8px; border-radius:5px; letter-spacing:.04em; }}
.path {{ font-family:'JetBrains Mono',monospace; font-size:16px; color:var(--fg); font-weight:500; }}
.badge {{ font-family:'JetBrains Mono',monospace; font-size:11px; padding:3px 9px; border-radius:20px;
  border:1px solid var(--rule); color:var(--fg-mid); }}
.badge.free {{ color:var(--jade-soft); border-color:rgba(45,187,120,.4); }}
.badge.paid {{ color:var(--amber-soft); border-color:rgba(240,160,48,.4); }}
.badge.bearer {{ color:var(--fg-mid); }}
.ep-desc {{ color:var(--fg-mid); font-size:14px; line-height:1.65; margin:2px 0 16px; }}
.ep-title {{ font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--fg-dim); margin:14px 0 7px; }}

.code {{ background:var(--bg-code); border:1px solid var(--rule); border-radius:9px; overflow:hidden; }}
.code-head {{ display:flex; align-items:center; justify-content:space-between;
  padding:6px 12px; border-bottom:1px solid var(--rule-soft); background:rgba(255,255,255,.015); }}
.code-lang {{ font-family:'JetBrains Mono',monospace; font-size:10px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--fg-dim); }}
.copy {{ font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--fg-mid);
  background:transparent; border:1px solid var(--rule); border-radius:5px; padding:3px 10px;
  cursor:pointer; transition:all .15s; }}
.copy:hover {{ color:var(--jade-soft); border-color:var(--jade); }}
.copy.ok {{ color:var(--jade); border-color:var(--jade); }}
pre {{ margin:0; padding:14px 16px; overflow-x:auto; font-size:13px; line-height:1.6; color:var(--fg); }}

.fields {{ margin:12px 0 2px; }}
.fields-h {{ font-family:'JetBrains Mono',monospace; font-size:10px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--fg-dim); margin:0 0 8px; }}
.kvgrid {{ display:grid; grid-template-columns:200px 1fr; gap:7px 16px;
  font-family:'JetBrains Mono',monospace; font-size:12.5px; }}
.kvgrid .pk {{ color:var(--jade-soft); word-break:break-word; }}
.kvgrid .pv {{ color:var(--fg-mid); line-height:1.5; }}

.split {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
footer.site {{ border-top:1px solid var(--rule); margin-top:54px; padding:30px 0 60px; }}
footer .links {{ display:flex; flex-wrap:wrap; gap:8px 22px; font-family:'JetBrains Mono',monospace;
  font-size:12.5px; }}
footer .links a {{ color:var(--fg-mid); }}
footer .meta {{ color:var(--fg-dim); font-family:'JetBrains Mono',monospace; font-size:11px;
  margin-top:16px; line-height:1.7; }}
.toast {{ position:fixed; bottom:22px; left:50%; transform:translateX(-50%) translateY(20px);
  background:var(--bg-elev); border:1px solid var(--jade-soft); color:var(--jade-soft);
  font-family:'JetBrains Mono',monospace; font-size:12px; padding:10px 18px; border-radius:7px;
  opacity:0; pointer-events:none; transition:all .25s; box-shadow:0 8px 40px rgba(0,0,0,.5); }}
.toast.show {{ opacity:1; transform:translateX(-50%) translateY(0); }}

@media (max-width:640px) {{
  .navlinks {{ display:none; }}
  .hero h1 {{ font-size:24px; }}
  .kvgrid {{ grid-template-columns:130px 1fr; }}
  .split {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>

<header class="site">
  <div class="wrap">
    <a class="brand" href="/docs"><span class="glyph"></span>MINT <span class="tag">API</span></a>
    <nav class="navlinks">
      <a href="/openapi.json">OpenAPI</a>
      <a href="https://mint-explorer.vercel.app" target="_blank" rel="noopener">Explorer</a>
      <a href="https://github.com/FoundryNet/mint-mcp" target="_blank" rel="noopener">GitHub ↗</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <section class="hero">
    <h1>MINT Protocol — API Reference</h1>
    <p>Universal work attestation, trust, and discovery for autonomous agents. Six REST
       endpoints (also available as MCP tools). Identity and attestation settle on Solana
       mainnet; rating, recommendation, and discovery are free.</p>
    <div class="baseurl">
      <span class="lbl">Base URL</span>
      <span class="url" id="baseurl">{_esc(BASE_URL)}</span>
      <button class="copy" data-copy="{_esc(BASE_URL)}" type="button">copy</button>
    </div>
  </section>

  <nav class="toc">{nav_links}
    <a href="#mcp">MCP</a><a href="#sdk">Python SDK</a>
  </nav>

  {_groups_html()}

  <h2 class="group" id="mcp">MCP Connection</h2>
  <article class="card">
    <p class="ep-desc">All six tools are available over MCP. Modern clients use Streamable
       HTTP at <code>/mcp</code>; legacy clients can use SSE at <code>/sse</code> (deprecated).</p>
    <div class="ep-title">Claude Desktop config</div>
    {_code_block(_claude_config(), "json")}
  </article>

  <h2 class="group" id="sdk">Python SDK</h2>
  <article class="card">
    <p class="ep-desc">The <code>mint-attest</code> SDK wraps every endpoint — no wallet,
       no blockchain code. It's a plain HTTPS client; the server handles Solana.</p>
    {_code_block(_SDK_SNIPPET, "python")}
  </article>
</main>

<footer class="site">
  <div class="wrap">
    <div class="links">
      <a href="/openapi.json">OpenAPI spec</a>
      <a href="https://mint-explorer.vercel.app" target="_blank" rel="noopener">Explorer</a>
      <a href="https://github.com/FoundryNet/mint-mcp" target="_blank" rel="noopener">GitHub</a>
      <a href="https://pypi.org/project/mint-attest" target="_blank" rel="noopener">PyPI</a>
      <a href="https://smithery.ai/server/@foundrynet/mint-protocol" target="_blank" rel="noopener">Smithery</a>
    </div>
    <div class="meta">
      Solana Program: {_esc(PROGRAM_ID)}<br>
      MINT Protocol v{_esc(VERSION)} · Foundry Labs · Solana Mainnet
    </div>
  </div>
</footer>

<div class="toast" id="toast"></div>
<script>
(function () {{
  function toast(msg) {{
    var t = document.getElementById("toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._t); t._t = setTimeout(function () {{ t.classList.remove("show"); }}, 1500);
  }}
  function copy(text, btn) {{
    navigator.clipboard.writeText(text).then(function () {{
      toast("Copied");
      if (btn) {{ btn.classList.add("ok"); var o = btn.textContent; btn.textContent = "copied";
        setTimeout(function () {{ btn.classList.remove("ok"); btn.textContent = o; }}, 1200); }}
    }});
  }}
  document.addEventListener("click", function (e) {{
    var b = e.target.closest(".copy");
    if (!b) return;
    if (b.dataset.copy != null) return copy(b.dataset.copy, b);
    var block = b.closest(".code");
    var pre = block && block.querySelector("pre");
    if (pre) copy(pre.innerText, b);
  }});
}})();
</script>
</body>
</html>"""
