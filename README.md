# MINT Protocol — Universal Work Attestation for Autonomous Agents

The reputation layer for the agent economy. One MCP server. Three tools. One
identity any autonomous actor can carry across the ecosystem.

MINT Protocol gives **any** autonomous actor — an AI agent, a physical machine,
an IoT device, a backend service — a persistent cryptographic identity, lets it
**attest** completed work as a tamper-evident on-chain record, and lets anyone
**verify** its trust score and work history before trusting it. This is the
horizontal trust infrastructure beneath the entire agent economy.

> Distinct from [FoundryNet Forge](https://foundrynet.io) (industrial machines).
> Forge is one *vertical* consumer of MINT; this server is the *horizontal*
> protocol. **mint-mcp is a thin presentation layer — Forge is the single
> settlement engine and the only relay key-holder.** mint-mcp never touches the
> MINT relay; it only calls Forge. Agents are the users; there is no web UI.

## The three tools

| Tool | What it does | Price |
|------|--------------|-------|
| `mint_register` | Give an actor a persistent `mint_id` + Solana wallet. Idempotent. | **Free** — identity is never gated |
| `mint_attest`   | Anchor a tamper-evident record of completed work on Solana; updates trust. | **0.02 USDC** (x402 or Forge billing key) |
| `mint_verify`   | Query an actor's identity, trust score, and verified work history. | **Free** — reputation is never gated |

The network grows on free identity + free verification; revenue comes from
attestation volume.

## How it maps onto Forge (one key-holder, one relay path)

- **`mint_register` → Forge `POST /v1/identify`.** An actor is mapped onto the
  `(oem, model, serial)` identity triple Forge already understands:
  `oem = actor_type`, `model = name`, `serial = uuid5(actor_type, name, operator)`
  (stable → idempotent, per-operator-scoped). Forge provisions the on-chain
  identity under its relay operator account.
- **`mint_attest` → Forge `POST /v1/attest`.** mint-mcp maps `work_type` to a
  settlement `complexity` (500–2000) and posts the work to Forge. Forge settles
  against the actor's **real** `mint_id` (`settle_job_raw` → relay `/settle`), so
  the attestation accrues real earnings + trust + on-chain history, computes the
  canonical `data_hash`, and returns the receipt. mint-mcp holds no relay key.
- **`mint_verify` → identity now; trust read rolling out.** The on-chain
  trust-read endpoint lives in Forge and is a follow-up pass. Until it lands,
  `mint_verify` returns the actor's identity + registration (from an in-process
  label cache) with `trust_score`/`total_attestations` reported as `"pending"` —
  not faked. Attestations are already permanent on-chain and will surface here once
  the Forge read endpoint ships.

## Forge dependency

`mint_attest` requires **`POST /v1/attest`** on `forge.foundrynet.io` (added to
forge-prod alongside this build — reuses `mint_relay.settle_job_raw` +
`record_event`, ownership-checked via `forge_agent_machines`). It must be
**deployed to Forge prod** before live attestation works. `mint_register` already
works against the existing `/v1/identify`.

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

> **No relay key by design.** Forge is the only relay key-holder; mint-mcp calls
> Forge, Forge calls the relay. One key, one settlement path, no duplicated logic.

## Run locally

```bash
cd ~/mint-protocol-mcp
pip install -r requirements.txt
export FORGE_API_KEY=fnet_...          # the only secret needed
python server.py                       # SSE on :8080
```

Smoke-test without a client:

```bash
curl -s localhost:8080/health | jq
curl -s localhost:8080/.well-known/agent-card.json | jq
```

Connect Claude Desktop / Cursor / Claude Code via `mcp-remote` (Streamable HTTP at `/mcp`):

```bash
claude mcp add mint-protocol -- npx -y mcp-remote https://mint-mcp-production.up.railway.app/mcp
```

## Deploy

Railway service **`mint-mcp`** in the `insightful-gratitude` project. Streamable
HTTP at `/mcp` (legacy SSE at `/sse`), health at `/health`, eventual vanity host
`mint.foundrynet.io`. Set
**`FORGE_API_KEY`** in the service variables before traffic — that's the only
secret. Deploy Forge's `POST /v1/attest` to prod first so live attestation works.

## Layout

```
server.py          FastAPI + SSE MCP server; health + discovery routes
tools/
  register.py      mint_register
  attest.py        mint_attest
  verify.py        mint_verify
forge_client.py    Forge API client (identify + attest) — the only upstream
actor_registry.py  best-effort mint_id → actor label cache (no DB)
x402_gate.py       x402 pay-per-attest middleware (INERT unless X402_ENABLED)
config.py          env-driven config
http_util.py       shared never-raises HTTP helper
```
