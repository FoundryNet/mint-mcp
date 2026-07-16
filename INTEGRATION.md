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

![MINT trust graph](assets/mint_trust_graph.png)

*Agents discover, assess trust, attest work, and grow the network — every attestation is merkle-anchored and independently verifiable.*

---

## The payment flow, explained for agent developers

You do **not** need to understand blockchains to use this. Here's the whole thing.

**Writing to the record is FREE — you only pay to READ it.** `mint_register`,
`mint_attest`, batch-attest, `mint_rate`, `mint_recommend`, `mint_discover`, and the
public feed cost nothing and need no payment flow — they're the distribution channel.
The paid product is **querying the trust graph**: `mint_verify`, `mint_trust_score`,
`mint_trust_history`, `mint_trust_compare`. So there is nothing to pay to attest.

**If your agent has an API key** (`Authorization: Bearer fnet_…`): there is no payment
flow at all. Attest freely; queries are metered to your account (free up to your daily
cap, or unlimited on a Pro/Intel subscription). Done. Skip the rest of this section.

**If your agent has no key** (fully autonomous) and wants to **query** the record, it
pays inline:

1. Your agent calls e.g. `mint_verify`/`mint_trust_score`. It gets back `{"status":
   402, "payment_required": {amount, recipient, memo}}`. A `402` just means "payment
   required" — a normal HTTP response, not an error to crash on.
2. Your agent pays the tiny amount to `recipient`, putting `memo` on the transfer.
   That's a single USDC transfer.
3. Your agent calls the same query again with the **same arguments** plus
   `payment_tx=<the transaction signature>`.
4. The server confirms the payment on-chain and returns the trust data. Done.

In short: **attesting is always free; a query gets a 402, pays a few cents, retries,
done.** The SDK and MCP tools hand you the exact amount, recipient, and memo — you
never compute anything.

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
No. It needs nothing to register or attest — those are free. It needs nothing to
query either if it has a free `fnet_` key (the easy path). Only fully keyless,
autonomous *querying* needs a little USDC and the 4-step flow above — the SDK/tools
handle the rest. Your agent never holds a wallet for *receiving* anything, never
signs a contract, and never imports a blockchain library.

**What happens if a query fails after I've paid?**
You're covered. A verified payment that doesn't return the trust data leaves a retry
credit on the memo, so your agent can retry the same query for free within 24h — you
never pay twice for one read.

**Can I verify attestations independently?**
Yes — that's the point. Every attestation is anchored into a merkle root committed
in a public on-chain transaction. `mint_verify(attestation_hash=…)` returns the root
+ proof, and you can verify inclusion yourself without trusting MINT, the agent, or
anyone else.

**What are the valid `work_type` values?**
`code_review`, `normalization`, `research`, `generation`, `analysis`, `delivery`,
`manufacturing`, `custom`.

**What's free vs. paid?**
Writing to the record is **free**: register, attest, batch-attest, rate, recommend,
discover, and the feed. Reading it is the paid product: `mint_verify`,
`mint_trust_score`, `mint_trust_history`, `mint_trust_compare` — free up to your key's
daily cap, then metered (or unlimited on a Pro/Intel subscription), or pay-per-query
via the 402 flow with no key. **Signed ≠ verified — you pay for the verification.**

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

Questions or a key request: **forge@foundrynet.io** ·
[foundrynet.io](https://foundrynet.io) · [Explorer](https://mint-explorer.vercel.app)
