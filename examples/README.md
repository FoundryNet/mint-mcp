# MINT attesting-agent examples

Minimal, runnable agents that record **verifiable proof of work** on MINT after
completing a task — one per framework. Each one:

- registers a MINT identity on first run and caches it (`~/.mint/<agent>.json`),
- does one simple task,
- attests the result, and
- prints the attestation and your running work count.

No wallet, no keys, no blockchain code. Set `MINT_API_KEY=fnet_…` to attest under
your own account; otherwise each agent **self-provisions a free scoped identity** and
runs on the free daily cap — so they work out of the box with no signup.

| Example | Install | Runs with no LLM key? |
|---------|---------|-----------------------|
| [`langchain_attesting_agent.py`](langchain_attesting_agent.py) | `pip install mint-attest langchain` | ✅ yes |
| [`autogen_attesting_agent.py`](autogen_attesting_agent.py) | `pip install mint-attest pyautogen` | ✅ yes (set `OPENAI_API_KEY` for a real chat turn) |
| [`crewai_attesting_agent.py`](crewai_attesting_agent.py) | `pip install mint-attest[crewai] crewai` | ✅ yes (set `OPENAI_API_KEY` for a real crew) |
| [`llamaindex_attesting_agent.py`](llamaindex_attesting_agent.py) | `pip install llama-index-tools-mint llama-index llama-index-llms-openai` | ✅ yes (set `OPENAI_API_KEY` for a real agent) |

```bash
# e.g.
pip install mint-attest langchain
python langchain_attesting_agent.py
```

Each prints a `MINT identity: MINT-xxxxxx` and an attestation; check your agent's
work history at [mint-explorer.vercel.app](https://mint-explorer.vercel.app).

See [`../AGENT_PROMPT_SNIPPET.md`](../AGENT_PROMPT_SNIPPET.md) for copy-paste prompt
and code blocks, and [`../INTEGRATION.md`](../INTEGRATION.md) for the full guide
(payment flow, FAQ). Semantic Kernel users: see
[`mint-semantic-kernel`](https://github.com/FoundryNet/mint-semantic-kernel).
