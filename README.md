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
> protocol. It rebuilds nothing — identity reuses the Forge API, settlement and
> trust reuse the existing MINT relay. Agents are the users; there is no web UI.

## The three tools

| Tool | What it does | Price |
|------|--------------|-------|
| `mint_register` | Give an actor a persistent `mint_id` + Solana wallet. Idempotent. | **Free** — identity is never gated |
| `mint_attest`   | Anchor a tamper-evident record of completed work on Solana; updates trust. | **0.02 USDC** (x402 or Forge billing key) |
| `mint_verify`   | Query an actor's identity, trust score, and verified work history. | **Free** — reputation is never gated |

The network grows on free identity + free verification; revenue comes from
attestation volume.

## How it maps onto existing infrastructure

- **`mint_register` → Forge `POST /v1/identify`.** An actor is mapped onto the
  `(oem, model, serial)` identity triple Forge already understands:
  `oem = actor_type`, `model = name`, `serial = uuid5(actor_type, name, operator)`
  (stable, so registration is idempotent and per-operator-scoped). Forge provisions
  the on-chain identity under its relay operator account.
- **`mint_attest` → MINT relay `POST /settle`.** Computes
  `data_hash = sha256(canonical attestation payload)` as the off-chain proof, then
  settles on Solana mainnet (`record_job → update_trust → settle_job`). If
  `MINT_RELAY_KEY` is unset it falls back to Forge `POST /v1/settle`, which holds
  the relay operator credentials internally — so attest anchors on-chain either way.
- **`mint_verify` → MINT relay `GET /history/{mint_id}`.** Trust score, total
  attestations, earnings, and recent on-chain attestations (each with a Solscan
  `verify_url`). Semantic labels (actor type/name/work-type breakdown) come from an
  in-process registry and may be `null` for actors first seen on another instance —
  the on-chain record is still fully verifiable.

## Configuration (env)

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `FORGE_API_KEY` | for register | — | `fnet_` internal service key for `/v1/identify` |
| `MINT_RELAY_KEY` | for attest/verify | — | `mint_` relay **operator** key — must be the operator Forge provisions under |
| `MINT_RELAY_URL` | no | `https://mint-relay-production.up.railway.app` | |
| `FORGE_API_URL` | no | `https://forge.foundrynet.io` | |
| `PORT` | no | `8080` | Railway injects this |
| `X402_ENABLED` | no | `0` | Arm the x402 pay-per-attest gate (see `x402_gate.py`) |
| `X402_PRICE_USDC` | no | `0.02` | Per-attest price under x402 |
| `CDP_API_KEY` | iff x402 | — | Coinbase CDP facilitator key (mainnet) |
| `SOLANA_WALLET` | no | `nFvAMGr…na1s` | base58 pay-to for x402 settlement |

> `MINT_RELAY_KEY` was **not** in the original build brief but is required for the
> direct relay path. Without it, `mint_register` still works and `mint_attest`
> falls back to Forge; `mint_verify` returns a clear `not_configured`. Wire it to
> Forge's internal relay operator key to light up the full loop.

## Run locally

```bash
cd ~/mint-protocol-mcp
pip install -r requirements.txt
export FORGE_API_KEY=fnet_...          # for register
export MINT_RELAY_KEY=mint_...         # for attest/verify (optional tonight)
python server.py                       # SSE on :8080
```

Smoke-test without a client:

```bash
curl -s localhost:8080/health | jq
curl -s localhost:8080/.well-known/agent-card.json | jq
```

Connect Claude Desktop / Cursor / Claude Code via `mcp-remote`:

```bash
claude mcp add mint-protocol -- npx -y mcp-remote http://localhost:8080/sse
```

## Deploy

Railway service **`mint-mcp`** in the `insightful-gratitude` project. SSE at
`/sse`, health at `/health`, eventual vanity host `mint.foundrynet.io`. Set
`FORGE_API_KEY` and `MINT_RELAY_KEY` in the service variables before traffic.

## Layout

```
server.py          FastAPI + SSE MCP server; health + discovery routes
tools/
  register.py      mint_register
  attest.py        mint_attest
  verify.py        mint_verify
forge_client.py    Forge API client (identity)
mint_client.py     MINT relay client (settle / history / trust)
actor_registry.py  best-effort mint_id → actor label cache (no DB)
x402_gate.py       x402 pay-per-attest middleware (INERT unless X402_ENABLED)
config.py          env-driven config
http_util.py       shared never-raises HTTP helper
```
