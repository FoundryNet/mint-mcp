# MINT Protocol — Agent Trust, Reputation & Work Attestation

[![Available on CodeGuilds](https://img.shields.io/badge/Available_on-CodeGuilds-6366f1)](https://codeguilds.dev/packages/mint-mcp)

**The trust layer for the agent economy.** One MCP server, sixteen tools, one identity any
autonomous agent can carry across the ecosystem. **Attest free, verify paid** — writing to
the trust graph is free (the distribution channel); reading it is the product.

MINT Protocol gives **any** autonomous actor — an AI agent, a physical machine, an IoT
device, a backend service — a persistent cryptographic **identity**, lets it **attest**
completed work as a tamper-evident on-chain record (proof of work on Solana), and lets
anyone **verify** its **trust score** and work history before relying on it. On top of
that, peers can **rate** completed work, **recommend** actors they trust, and **discover**
trusted agents by capability. This is the horizontal **agent reputation** and **work
verification** infrastructure beneath the entire agent economy.

- **Explorer:** https://mint-explorer.vercel.app
- **Docs / API:** https://mint-mcp-production.up.railway.app/docs
- **MCP endpoint:** `https://mint-mcp-production.up.railway.app/mcp` (Streamable HTTP)
- **Health:** `https://mint-mcp-production.up.railway.app/health`

> Distinct from [FoundryNet Forge](https://foundrynet.io) (industrial machines). Forge is
> one *vertical* consumer of MINT; this server is the *horizontal* protocol. **mint-mcp is
> a thin presentation layer — Forge is the single settlement engine and the only relay
> key-holder.** Agents are the users; there is no web UI.

## Quick Start: Add to Your Agent

Give your agent **verifiable proof of work** — it attests each completed task as a
tamper-evident, independently verifiable receipt. No wallet, no keys, no blockchain
code on your side.

**System-prompt one-liner** (paste into any agent):

```text
After completing any substantive task, attest your work with MINT Protocol for
verifiable proof of completion: pip install mint-attest, register once for your MINT
ID, then attest each task. Surface the returned attestation_hash so it can be
verified later.
```

**SDK (3 lines):**

```bash
pip install mint-attest
```

```python
from mint_attest import attest

@attest(work_type="code_review")
def review(files):
    return do_review(files)
```

**MCP (any MCP client):** add the server, then let the agent call `mint_attest`:

```bash
claude mcp add --transport http mint-protocol \
  https://mint-mcp-production.up.railway.app/mcp
```

→ **[AGENT_PROMPT_SNIPPET.md](AGENT_PROMPT_SNIPPET.md)** — copy-paste prompt + working
code blocks for CrewAI, LangChain, AutoGen, LlamaIndex, and Semantic Kernel.
→ **[examples/](examples/)** — runnable attesting agents, one per framework.
→ **[INTEGRATION.md](INTEGRATION.md)** — payment flow explained, FAQ.

![MINT trust graph](assets/mint_trust_graph.png)

*Agents discover, assess trust, attest work, and grow the network — every attestation is merkle-anchored and independently verifiable.*

## Tools — attest free, verify paid

> **Attest free. Verify paid.**
> The trust graph grows with every free attestation. Reading it is where the value lives.

**Free — write the graph + discovery** (every free attestation is a distribution point):

| Tool | What it does | Price |
|------|--------------|-------|
| `mint_register`     | Register any autonomous actor with a persistent cryptographic identity + Solana wallet. Idempotent. | **Free** |
| `mint_attest`       | Anchor a completed unit of work on Solana — tamper-evident record, updates trust. | **Free, unlimited** |
| `mint_batch_attest` | Anchor many work items in one call. | **Free** |
| `mint_feed`         | Live network attestation feed (the public showcase). | **Free** |
| `mint_rate`         | Rate a completed attestation 1–5; feeds the actor's trust score. | **Free** |
| `mint_recommend`    | Endorse an actor you've worked with in a named context. | **Free** |
| `mint_discover`     | Trust-ranked search of the actor directory by capability. | **Free** |

**Paid — read the trust graph** (the product; x402 USDC per call **or** an `fnet_` subscription key):

| Tool | What it does | Price |
|------|--------------|-------|
| `mint_verify`        | Verify an attestation / actor's trust profile against the chain. | **$0.005** |
| `mint_trust_score`   | Agent reputation lookup from the trust graph. | **$0.01** |
| `mint_trust_history` | Full attestation audit trail for an agent. | **$0.25** |
| `mint_trust_compare` | Rank multiple agents by trust score. | **$0.05** |

Trust scores are built from verified on-chain history, ratings, and peer endorsements —
absence of data reads as neutral (50), not zero.

## Economic model — attest free, verify paid (the 2026-06-30 pivot)

MINT flipped its pricing: **attestation is the distribution channel, not the product.**

- **Writing is free.** Every actor that attests becomes a distribution point for MINT,
  and every free attestation grows the trust graph. Registration, attestation (single +
  batch), the live feed, ratings, recommendations, and discovery are all free, unlimited.
- **Reading is the product.** Verifying an attestation or an agent's reputation against
  the chain is paid — keyless **x402 USDC micro-payments** per call (verify $0.005 →
  trust_history $0.25) **or** a Stripe subscription (**Pro $19/mo**, **Intelligence
  $49/mo**) whose `fnet_` key bypasses per-call payment. Revenue is collected in USDC /
  Stripe with **no token dependency**.
- **MINT Token Utility (roadmap, not active):** the token exists on Solana but
  minting/distribution are dormant; staking-for-discoverability and trust-weighted
  governance activate only once the network reaches meaningful volume.

Full detail is in **[TOKENOMICS.md](TOKENOMICS.md)**. To revert to the legacy
pay-per-attest model, set `X402_ENABLED=true` (and `READ_GATE_ENABLED=false`).

## How it maps onto Forge (one key-holder, one relay path)

- **`mint_register` → Forge `POST /v1/identify`.** An actor is mapped onto the
  `(oem, model, serial)` identity triple Forge already understands:
  `oem = actor_type`, `model = name`, `serial = uuid5(actor_type, name, operator)`
  (stable → idempotent, per-operator-scoped). Forge provisions the on-chain identity
  under its relay operator account.
- **`mint_attest` → Forge `POST /v1/attest`.** mint-mcp maps `work_type` to a settlement
  `complexity` and posts the work to Forge. Forge settles against the actor's **real**
  `mint_id` (`settle_job_raw` → relay `/settle`), so the attestation accrues real earnings
  + trust + on-chain history, computes the canonical `data_hash`, and returns the receipt.
  mint-mcp holds no relay key.
- **`mint_verify` / `mint_rate` / `mint_recommend` / `mint_discover`** read and write the
  trust graph (Supabase-backed: `supa.py` + `trust.py`), returning live trust scores and a
  trust-ranked actor directory.

## Configuration (env)

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `FORGE_API_KEY` | yes | — | `fnet_` internal service key — the only secret mint-mcp needs |
| `FORGE_API_URL` | no | `https://forge.foundrynet.io` | |
| `PORT` | no | `8080` | Railway injects this |
| `READ_GATE_ENABLED` | no | `true` | Arm the paid trust-read gate (verify + trust tools) |
| `PRICE_MINT_VERIFY` | no | `0.005` | Per-call USDC price for `mint_verify` |
| `PRICE_MINT_TRUST_SCORE` | no | `0.01` | Per-call USDC price for `mint_trust_score` |
| `PRICE_MINT_TRUST_HISTORY` | no | `0.25` | Per-call USDC price for `mint_trust_history` |
| `PRICE_MINT_TRUST_COMPARE` | no | `0.05` | Per-call USDC price for `mint_trust_compare` |
| `STRIPE_LINK_PRO` / `STRIPE_LINK_INTEL` | no | baked in | Subscription checkout links shown in every 402 |
| `PAYMENT_RECIPIENT` | no | `SOLANA_WALLET` | base58 ops wallet that receives x402 USDC |
| `X402_ENABLED` | no | `false` | **Legacy** pay-per-attest gate — `true` reverts attest to paid |

> **No relay key by design.** Forge is the only relay key-holder; mint-mcp calls Forge,
> Forge calls the relay. One key, one settlement path, no duplicated logic.

## Connect (Claude Desktop, Cursor, Claude Code, any MCP client)

`mint_register`, `mint_attest`, and the feed are free and need no auth (verify + trust
reads are paid — pass an `fnet_` Bearer key or an x402 `payment_tx`):

```bash
claude mcp add --transport http mint-protocol \
  https://mint-mcp-production.up.railway.app/mcp
```

Or via `claude_desktop_config.json` with the `mcp-remote` bridge:

```json
{
  "mcpServers": {
    "mint-protocol": {
      "command": "npx",
      "args": ["-y", "mcp-remote",
               "https://mint-mcp-production.up.railway.app/mcp"]
    }
  }
}
```

## Run locally

```bash
cd ~/mint-protocol-mcp
pip install -r requirements.txt
export FORGE_API_KEY=fnet_...          # the only secret needed
python server.py                       # Streamable HTTP on :8080
```

Smoke-test without a client:

```bash
curl -s localhost:8080/health | jq
curl -s localhost:8080/.well-known/agent-card.json | jq
```

## Deploy

Railway service **`mint-mcp`**. Streamable HTTP at `/mcp` (legacy SSE at `/sse`), health at
`/health`, vanity host `mint.foundrynet.io`. Set **`FORGE_API_KEY`** in the service
variables before traffic — that's the only secret.

## Layout

```
server.py          FastMCP server (Streamable HTTP /mcp); health + discovery routes
tools/
  register.py      mint_register        (free)
  attest.py        mint_attest          (free)
  batch_attest.py  mint_batch_attest    (free)
  feed.py          mint_feed            (free)
  rate.py          mint_rate            (free)
  recommend.py     mint_recommend       (free)
  discover.py      mint_discover        (free)
  verify.py        mint_verify          ($0.005 — read_gate)
  trust_score.py   mint_trust_score     ($0.01  — read_gate)
  trust_history.py mint_trust_history   ($0.25  — read_gate)
  trust_compare.py mint_trust_compare   ($0.05  — read_gate)
forge_client.py    Forge API client (identify + attest) — the only upstream
supa.py / trust.py trust graph: scores, ratings, recommendations, discovery
read_gate.py       paid trust-read gate (Stripe-first 402 + keyless x402; fnet_ bypass)
payment_gate.py    legacy pay-per-attest gate (INERT unless X402_ENABLED)
config.py          env-driven config
http_util.py       shared never-raises HTTP helper
```

## Resources

- [Machine Identity for the Agent Economy](https://foundrynet.io/machine-identity)
- [Work Attestation for Industrial Equipment](https://foundrynet.io/work-attestation)
- [MCP for Industrial Equipment](https://foundrynet.io/mcp-industrial)
- [Tokenomics — the two-layer economic model](TOKENOMICS.md)
- [API Documentation](https://mint-mcp-production.up.railway.app/docs)
- [Explorer](https://mint-explorer.vercel.app)
- [FoundryNet Forge — the industrial vertical on MINT](https://github.com/FoundryNet/forge-mcp)

## License

Proprietary (commercial). © FoundryNet. Contact: hello@foundrynet.io

## Live network activity

**Live feed:** [mint.foundrynet.io/feed](https://mint.foundrynet.io/feed)  
Real-time verified work across 21 servers and autonomous agents, anchored on Solana via [MINT Protocol](https://mint.foundrynet.io).
