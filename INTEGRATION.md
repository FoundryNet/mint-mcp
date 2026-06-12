# Integrating MINT attestation into your agent

This is the one-stop guide for agent developers. Give your agent **verifiable proof
of work**: after it finishes a task it records a tamper-evident receipt anyone can
independently verify — no wallet, no keys, no blockchain code on your side.

- **Copy-paste prompts & code blocks:** [`AGENT_PROMPT_SNIPPET.md`](AGENT_PROMPT_SNIPPET.md)
- **Runnable examples (one per framework):** [`examples/`](examples/)
- **MCP server:** `https://mint-mcp-production.up.railway.app/mcp` (Streamable HTTP)
- **Explorer:** https://mint-explorer.vercel.app

---

## The 60-second version

```bash
pip install mint-attest
```

```python
from mint_attest import attest

@attest(work_type="code_review")
def review(files):
    return do_review(files)
```

Every call to `review()` now hashes the input/output locally (only the hash leaves
your process), records how long it took, and files a verifiable receipt. Your
function's return value is unchanged. Set `MINT_API_KEY=fnet_…` to attest under your
account, or call `MintClient().register()` once to self-provision a free identity.

---

## The three drop-in versions

Full text in [`AGENT_PROMPT_SNIPPET.md`](AGENT_PROMPT_SNIPPET.md). In brief:

### 1. Minimal (system prompt)

```text
After completing any substantive task, attest your work with MINT Protocol for
verifiable, independent proof of completion.
- pip install mint-attest
- Register once to get your MINT ID (free, no signup — the SDK can self-provision).
- Attest after each task. Inputs/outputs are hashed locally, the record is
  merkle-anchored on a public ledger, and anyone can verify it.
Surface the returned attestation_hash so the work can be verified later.
```

### 2. Standard (MCP)

Point any MCP client at `https://mint-mcp-production.up.railway.app/mcp` and tell the
agent to call `mint_attest` after each task. Tools: `mint_register`, `mint_attest`,
`mint_verify`, `mint_rate`, `mint_recommend`, `mint_discover`. See the snippet file
for the full system-prompt block.

### 3. Framework code blocks

Ready-to-paste working code for **CrewAI, LangChain, AutoGen, LlamaIndex, and
Semantic Kernel** — in [`AGENT_PROMPT_SNIPPET.md`](AGENT_PROMPT_SNIPPET.md), with
complete runnable scripts in [`examples/`](examples/).

---

## The payment flow, explained for agent developers

You do **not** need to understand blockchains to use this. Here's the whole thing.

**If your agent has an API key** (`Authorization: Bearer fnet_…`): there is no
payment flow. Call `mint_attest`, get a receipt. Attestation is free up to your
key's daily cap, then metered to your account. Done. Skip the rest of this section.

**If your agent has no key** (fully autonomous), attestation costs 2 cents, and your
agent pays it inline:

1. Your agent calls `mint_attest`. It gets back `{"status": 402, "payment_required":
   {amount, recipient, memo}}`. A `402` just means "payment required" — it's a
   normal HTTP response, not an error to crash on.
2. Your agent pays the tiny amount (0.02 USDC) to `recipient`, putting `memo` on the
   transfer. That's a single USDC transfer.
3. Your agent calls `mint_attest` again with the **same arguments** plus
   `payment_tx=<the transaction signature>`.
4. The server confirms the payment on-chain and files the attestation. Done.

In short: **your agent gets a 402, pays 2 cents, retries, done.** The SDK and MCP
tools hand you the exact amount, recipient, and memo — you never compute anything.

> The simplest path is to give your agent a free `fnet_` key and skip payments
> entirely until you outgrow the daily cap. Get one at
> [foundrynet.io](https://foundrynet.io), or let the SDK self-provision one:
> `MintClient().register()` returns a fresh scoped key on first call.

---

## What an attestation actually returns

Attestations are **batched**: each `mint_attest` files the record immediately and
returns an `attestation_hash` with `anchored=false` and an `anchor_eta`. A single
on-chain transaction then anchors the whole batch (so the per-record on-chain cost
is ~0). To get the independent proof later, call:

```python
mint.verify(attestation_hash="…")   # or mint_verify(attestation_hash=…) over MCP
```

Once anchored, that returns the `merkle_root`, `merkle_proof`, and `anchor_tx`. You
fold the proof into `sha256(0x00 || attestation_hash)` and check it equals the root
in the transaction — proving inclusion yourself, trusting no one.

Receipt fields you'll use: `attestation_id`, `data_hash`, `attestation_hash`,
`anchored`, `anchor_eta`.

---

## FAQ

**Does my agent need a Solana wallet?**
No. It needs nothing if it has a free `fnet_` key (the easy path). For fully
keyless, autonomous attestation it needs a little USDC and the 4-step flow above —
the SDK/tools handle the rest. Your agent never holds a wallet for *receiving*
anything, never signs a contract, and never imports a blockchain library.

**What happens if the attestation fails after I've paid?**
You're covered. A verified payment that doesn't result in a filed attestation
leaves a retry credit on the memo, so your agent can retry the same attestation for
free within 24h — you never pay twice for one record.

**Can I verify attestations independently?**
Yes — that's the point. Every attestation is anchored into a merkle root committed
in a public on-chain transaction. `mint_verify(attestation_hash=…)` returns the root
+ proof, and you can verify inclusion yourself without trusting MINT, the agent, or
anyone else.

**What are the valid `work_type` values?**
`code_review`, `normalization`, `research`, `generation`, `analysis`, `delivery`,
`manufacturing`, `custom`.

**What's free vs. paid?**
Register, verify, rate, recommend, and discover are **free**. Attest is free up to
your key's daily cap, then ~0.02 USDC each (or 0.02 USDC per attest with no key via
the 402 flow).

**Does attesting slow down or break my agent?**
The decorator and framework callbacks **fail open**: if the network hiccups or no
key is set, they log and return your function's result unchanged. Instrumentation
never breaks the agent. (Pass `strict=True` to opt into raising.)

**Is my data sent anywhere?**
No. Inputs and outputs are hashed (SHA-256) locally; only the hash is transmitted.

**Can other agents see and trust my track record?**
Yes. `mint_verify` returns your trust score and work history; `mint_discover` lets
any agent find you by capability, ranked by trust. Reputation is portable across the
ecosystem, not locked in one platform.

---

Questions or a key request: **hello@foundrynet.io** ·
[foundrynet.io](https://foundrynet.io) · [Explorer](https://mint-explorer.vercel.app)
