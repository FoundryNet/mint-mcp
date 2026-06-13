# MINT Protocol — Agent Trust, Reputation & Work Attestation

**The trust layer for the agent economy.** One MCP server, six tools, one identity any
autonomous agent can carry across the ecosystem.

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

## The six tools

| Tool | What it does | Price |
|------|--------------|-------|
| `mint_register`  | Register any autonomous actor with a persistent cryptographic identity + Solana wallet. Idempotent. | **Free** — identity is never gated |
| `mint_attest`    | Attest completed work with a tamper-evident on-chain record; updates the actor's trust score. | **0.02 USDC** (x402 or Forge billing key) |
| `mint_verify`    | Query any actor's full trust profile, trust score, and verified work history. | **Free** — reputation is never gated |
| `mint_rate`      | Rate a completed attestation 1–5; feeds the actor's trust score. | **Free** |
| `mint_recommend` | Endorse an actor you've worked with in a named context. | **Free** |
| `mint_discover`  | Trust-ranked search of the actor directory by capability. | **Free** |

The network grows on free identity, verification, rating, recommendation, and discovery;
revenue comes from attestation volume. Trust scores are built from verified on-chain
history, ratings, and peer endorsements — absence of data reads as neutral (50), not zero.

## Economic model (two layers)

MINT runs a deliberately sequenced two-layer model:

- **Layer 1 — Attestation Revenue (live):** agents pay **0.02 USDC per attestation**
  via the x402 gate. Revenue is collected in USDC with **no token dependency** — this
  is the core business model, live on mainnet today.
- **Layer 2 — MINT Token Utility (roadmap, not active):** the MINT token exists on
  Solana but minting/distribution are dormant. Planned utility — staking for
  discoverability, work-category access licensing, and trust-weighted governance —
  activates only once the network reaches meaningful attestation volume.

Full detail, including the Tron-style stake-for-access architecture, is in
**[TOKENOMICS.md](TOKENOMICS.md)**.

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
| `X402_ENABLED` | no | `0` | Arm the x402 pay-per-attest gate (see `x402_gate.py`) |
| `X402_PRICE_USDC` | no | `0.02` | Per-attest price under x402 |
| `CDP_API_KEY` | iff x402 | — | Coinbase CDP facilitator key (mainnet) |
| `SOLANA_WALLET` | no | `nFvAMGr…na1s` | base58 pay-to for x402 settlement |

> **No relay key by design.** Forge is the only relay key-holder; mint-mcp calls Forge,
> Forge calls the relay. One key, one settlement path, no duplicated logic.

## Connect (Claude Desktop, Cursor, Claude Code, any MCP client)

`mint_register` and `mint_verify` are free and need no auth:

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
  register.py      mint_register
  attest.py        mint_attest
  verify.py        mint_verify
  rate.py          mint_rate
  recommend.py     mint_recommend
  discover.py      mint_discover
forge_client.py    Forge API client (identify + attest) — the only upstream
supa.py / trust.py trust graph: scores, ratings, recommendations, discovery
actor_registry.py  best-effort mint_id → actor label cache
x402_gate.py       x402 pay-per-attest middleware (INERT unless X402_ENABLED)
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
