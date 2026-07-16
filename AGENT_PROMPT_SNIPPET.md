# Add MINT attestation to your agent

Make **verifiable proof of work** a default behaviour of your agent: after it
finishes a task, it records a tamper-evident receipt that anyone can verify later —
no wallet, no keys, no blockchain code on your side. Just an API call (or an MCP
tool call).

This file is the canonical copy-paste source. Three drop-in versions:

1. **[Minimal](#1--minimal-system-prompt)** — 4 lines, zero friction.
2. **[Standard (MCP)](#2--standard-mcp)** — for any MCP-compatible agent.
3. **[Framework code blocks](#3--framework-code-blocks)** — ready-to-paste working
   code for CrewAI, LangChain, AutoGen, LlamaIndex, and Semantic Kernel.

Runnable end-to-end examples live in [`examples/`](examples/). Full integration
guide (payment flow, FAQ) in [`INTEGRATION.md`](INTEGRATION.md).

---

## 1 — Minimal (system prompt)

Paste into your agent's system prompt. Tells the agent to attest its own work:

```text
After completing any substantive task, attest your work with MINT Protocol for
verifiable, independent proof of completion.
- pip install mint-attest
- Register once to get your MINT ID (free, no signup — the SDK can self-provision).
- Attest after each task. Inputs/outputs are hashed locally (only the hash leaves),
  the record is merkle-anchored on a public ledger, and anyone can verify it.
Attesting is always free — it's the distribution channel. (Querying the record later —
verify / trust score — is the paid product.) Surface the returned attestation_hash so
the work can be verified later.
```

Even shorter, if the agent already has a MINT API key in its environment:

```text
After each task, call mint-attest to record verifiable proof of the work, and
report the attestation_hash.
```

---

## 2 — Standard (MCP)

For any MCP-compatible agent (Claude Desktop/Code, Cursor, Goose, custom MCP
clients). Point the client at the MINT MCP server, then paste the prompt block.

**Server (Streamable HTTP):** `https://mint-mcp-production.up.railway.app/mcp`

```bash
# Claude Code / any MCP CLI
claude mcp add --transport http mint-protocol \
  https://mint-mcp-production.up.railway.app/mcp
```

System-prompt block:

```text
You have MINT Protocol tools for verifiable work attestation. Use them by default:
- mint_register(actor_type, name, capabilities) → your MINT ID ("MINT-xxxxxx").
  Call ONCE for a new identity; it's idempotent and free. Reads never need a key.
- mint_attest(mint_id, work_type, duration_seconds, summary) → records a
  tamper-evident, independently verifiable receipt of a completed task. Returns an
  attestation_hash + anchor_eta. work_type ∈ {code_review, research, generation,
  analysis, delivery, normalization, manufacturing, custom}.
- mint_verify(mint_id | attestation_hash) → trust profile of any actor, or the
  on-chain merkle proof for one attestation. Free.
- mint_rate / mint_recommend / mint_discover → rate work, endorse actors, and find
  trusted agents by capability. Free.

After finishing any substantive unit of work, call mint_attest and surface the
attestation_hash so the result can be verified later.

Attesting is free — there is nothing to pay to write a record. Paying only applies to
QUERYING the record with no API key (verify / trust score / history / compare): that
call may reply with {"status": 402, "payment_required": {...}}. Pay the given amount to
the given recipient with the given memo, then call the SAME query again plus
payment_tx=<your tx signature>. If you present an `Authorization: Bearer fnet_…` key,
this step is skipped entirely.
```

The 402 flow (queries only — attesting is free and never hits this):

1. Call a query (`mint_verify` / `mint_trust_score` / …) with no `payment_tx`.
2. If you have no API key you get back a `402` with `amount`, `recipient`, `memo`.
3. Send that small USDC transfer with that memo.
4. Call the same query again with the same args **plus** `payment_tx=<signature>` — done.

> Have an `fnet_` key? Pass it as `Authorization: Bearer fnet_…` and steps 1–4
> collapse to a single call — queries are free up to your key's daily cap (writing is
> always free).

---

## 3 — Framework code blocks

Each block is working code — `pip install` the line shown, paste, run. All of them
auto-register a MINT identity on first use (self-provisioning a scoped key with no
signup) and attest completed work. Set `MINT_API_KEY=fnet_…` to attest under your
own account instead; otherwise the agent runs autonomously on the free daily cap.

### CrewAI

```bash
pip install mint-attest[crewai] crewai
```

```python
from mint_attest import MintClient
from mint_attest.crewai import MintAttestTool
from crewai import Agent, Crew, Task

# Self-provision a MINT identity (free; or set MINT_API_KEY for your own account).
mint = MintClient(name="research-crew", capabilities=["research"])
mint.register()

researcher = Agent(
    role="Researcher",
    goal="Answer the question, then attest the finished work on MINT.",
    backstory="A diligent analyst who proves every deliverable.",
    tools=[MintAttestTool(client=mint)],   # agent calls mint_attest when done
)
crew = Crew(agents=[researcher], tasks=[
    Task(description="Summarize the state of AI agent interoperability in 2026. "
                     "When finished, attest the result with the mint_attest tool.",
         expected_output="A 3-sentence summary.", agent=researcher)])

print(crew.kickoff())
print("MINT identity:", mint.mint_id)
```

Prefer fully-automatic attestation (no tool call needed)? Use the step callback:

```python
from mint_attest.crewai import mint_attest_step_callback
crew = Crew(agents=[...], tasks=[...],
            step_callback=mint_attest_step_callback(client=mint, work_type="research"))
```

### LangChain

```bash
pip install mint-attest[langchain] langchain langchain-openai
```

```python
from mint_attest import MintClient
from mint_attest.langchain import MintAttestCallback
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

mint = MintClient(name="langchain-agent", capabilities=["summarization"])
mint.register()                                  # free self-provision

# Every chain run is attested automatically via the callback.
attest = MintAttestCallback(client=mint, work_type="generation")

chain = ChatPromptTemplate.from_template("Summarize in one sentence: {topic}") | ChatOpenAI(model="gpt-4o-mini")
result = chain.invoke({"topic": "verifiable work attestation for AI agents"},
                      config={"callbacks": [attest]})
print(result.content)
print("MINT identity:", mint.mint_id)
```

No LLM key handy? The callback fires on *any* chain, including a plain
`RunnableLambda` — see [`examples/langchain_attesting_agent.py`](examples/langchain_attesting_agent.py),
which runs with nothing but `pip install mint-attest langchain`.

### AutoGen

```bash
pip install mint-attest[autogen] pyautogen
```

```python
from mint_attest import MintClient
from mint_attest.autogen import MintAttestHook
from autogen import AssistantAgent, UserProxyAgent

mint = MintClient(name="autogen-agent", capabilities=["generation"])
mint.register()                                  # free self-provision

assistant = AssistantAgent("assistant",
                           llm_config={"config_list": [{"model": "gpt-4o-mini"}]})
MintAttestHook(client=mint).attach(assistant)    # every reply is attested

user = UserProxyAgent("user", human_input_mode="NEVER", code_execution_config=False,
                      max_consecutive_auto_reply=1)
user.initiate_chat(assistant, message="Summarize MINT Protocol in one sentence.")
print("MINT identity:", mint.mint_id)
```

### LlamaIndex

```bash
pip install llama-index-tools-mint llama-index llama-index-llms-openai
```

```python
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.openai import OpenAI
from llama_index.tools.mint import MintToolSpec

mint = MintToolSpec(name="llamaindex-agent", capabilities=["research"])

agent = FunctionAgent(tools=mint.to_tool_list(), llm=OpenAI(model="gpt-4.1"))
response = await agent.run(
    "Research AI agent trust, then attest the finished work with attest_work "
    "and show me my trust profile.")
print(response)
```

Or attest a deterministic step directly, no LLM required:

```python
from llama_index.tools.mint import MintToolSpec
mint = MintToolSpec(name="llamaindex-agent")
receipt = mint.attest_work(work_type="research", summary="Compiled a market scan.",
                           output_data="…the report…", duration_seconds=12)
print(receipt["attestation_hash"], receipt.get("anchor_eta"))
```

### Semantic Kernel

```bash
pip install mint-semantic-kernel semantic-kernel
```

```python
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from mint_semantic_kernel import MintPlugin

kernel = Kernel()
kernel.add_service(OpenAIChatCompletion(service_id="chat", ai_model_id="gpt-4o-mini"))
kernel.add_plugin(MintPlugin(name="sk-agent"), plugin_name="mint")  # free self-provision

settings = kernel.get_prompt_execution_settings_from_service_id("chat")
settings.function_choice_behavior = FunctionChoiceBehavior.Auto()   # agent calls attest_work itself
```

Or attest directly, no LLM required:

```python
from mint_semantic_kernel import MintPlugin
mint = MintPlugin(name="sk-agent")
print(mint.attest_work(work_type="generation", summary="Drafted the release notes."))
```

> **Already an MCP server?** Both LlamaIndex and Semantic Kernel can mount MINT over
> MCP instead of the native package — see each repo's README for the
> `MCPStreamableHttpPlugin` / MCP-client wiring against
> `https://mint-mcp-production.up.railway.app/mcp`.

---

## Notes for accuracy

- **work_type** must be one of: `code_review`, `normalization`, `research`,
  `generation`, `analysis`, `delivery`, `manufacturing`, `custom`.
- **What an attestation returns today:** `attestation_id`, `data_hash`, and an
  `attestation_hash` with `anchored=false` + an `anchor_eta`. Attestations are
  batched and a *single* on-chain transaction anchors each batch (so per-record
  on-chain cost is ~0). Verify later with `mint_verify(attestation_hash=…)`, which
  returns the merkle root + proof you can check yourself.
- **Cost:** writing is **free** — register/attest/rate/recommend/discover cost
  nothing (the distribution channel). Reading is the product — verify + trust
  score/history/compare are free up to your key's daily cap, then metered (or
  unlimited on a subscription), or pay-per-query via the 402 flow with no key.
- **No crypto on your side:** the SDK/plugin makes authenticated HTTPS calls; all
  ledger interaction happens server-side. No wallet, no signing, no chain libraries.

Questions: `forge@foundrynet.io` · Explorer: https://mint-explorer.vercel.app
