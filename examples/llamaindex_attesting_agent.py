#!/usr/bin/env python3
"""Minimal LlamaIndex agent that attests its work on MINT.

MintToolSpec gives a LlamaIndex agent attestation tools: attest_work records a
tamper-evident, independently verifiable receipt of a finished task; verify_trust
and discover_actors read the trust graph. No wallet, no keys, no blockchain code —
every tool is a plain authenticated HTTPS call.

Runs out of the box. With OPENAI_API_KEY set it runs a real FunctionAgent that
decides to attest; without one it attests a sample deliverable directly through the
same tool, so you still see verifiable proof of work end-to-end.

    pip install llama-index-tools-mint llama-index llama-index-llms-openai
    python llamaindex_attesting_agent.py                        # autonomous, free cap
    OPENAI_API_KEY=sk-... python llamaindex_attesting_agent.py  # full agent run
"""
import asyncio
import json
import os
from pathlib import Path

from llama_index.tools.mint import MintToolSpec

from mint_attest import MintClient  # noqa: F401  (installed as a dep of the tool)

AGENT_NAME = "llamaindex-attesting-agent"
CACHE = Path.home() / ".mint" / f"{AGENT_NAME}.json"


def mint_tool() -> MintToolSpec:
    """Build the tool and register a MINT identity on first run (cached after)."""
    key = os.environ.get("MINT_API_KEY")
    if not key and CACHE.exists():
        key = json.loads(CACHE.read_text()).get("api_key")
    spec = MintToolSpec(api_key=key, name=AGENT_NAME, capabilities=["research"])
    actor = spec.client.register()     # keyless self-provisions a scoped key; keyed is idempotent
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"mint_id": actor.mint_id, "api_key": spec.client.api_key}))
    print(f"MINT identity: {actor.mint_id}")
    return spec


async def main() -> None:
    spec = mint_tool()

    if os.environ.get("OPENAI_API_KEY"):
        from llama_index.core.agent.workflow import FunctionAgent
        from llama_index.llms.openai import OpenAI
        agent = FunctionAgent(tools=spec.to_tool_list(), llm=OpenAI(model="gpt-4.1"))
        print(await agent.run(
            "Research the state of AI agent trust in one sentence, then attest the "
            "finished work with attest_work and report the attestation_hash."))
    else:
        print("(no OPENAI_API_KEY — attesting a sample deliverable directly)")
        receipt = spec.attest_work(work_type="research",
                                   summary="Compiled a one-sentence scan of AI agent trust.",
                                   output_data="A concise finding.", duration_seconds=9)
        print(f"Attested: {receipt.get('attestation_id')}  "
              f"hash={receipt.get('attestation_hash')}  eta={receipt.get('anchor_eta')}")

    print(f"Attestations on record: {spec.client.verify(spec.client.mint_id).total_attestations}")
    print("Verify your work history at https://mint-explorer.vercel.app")


if __name__ == "__main__":
    asyncio.run(main())
