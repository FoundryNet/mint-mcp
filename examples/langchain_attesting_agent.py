#!/usr/bin/env python3
"""Minimal LangChain agent that attests its work on MINT after each run.

Verifiable proof of work for any LangChain chain: a MintAttestCallback fires on
every chain run and records a tamper-evident, independently verifiable receipt —
no wallet, no keys, no blockchain code. Just add the callback.

Runs out of the box (no LLM key needed): the "task" here is a plain RunnableLambda
so you can see attestation working with nothing but mint-attest + langchain. Swap
in an LLM chain and the callback behaves identically.

    pip install mint-attest langchain
    python langchain_attesting_agent.py            # autonomous, free daily cap
    MINT_API_KEY=fnet_... python langchain_attesting_agent.py   # your account
"""
import json
import os
from pathlib import Path

from langchain_core.runnables import RunnableLambda

from mint_attest import MintClient
from mint_attest.langchain import MintAttestCallback

AGENT_NAME = "langchain-attesting-agent"
CACHE = Path.home() / ".mint" / f"{AGENT_NAME}.json"


def mint_identity() -> MintClient:
    """Register a MINT identity on first run, then reuse the cached key."""
    key = os.environ.get("MINT_API_KEY")
    if not key and CACHE.exists():
        key = json.loads(CACHE.read_text()).get("api_key")
    client = MintClient(api_key=key, name=AGENT_NAME, capabilities=["summarization"])
    actor = client.register()          # keyless self-provisions a scoped key; keyed is idempotent
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"mint_id": actor.mint_id, "api_key": client.api_key}))
    print(f"MINT identity: {actor.mint_id}")
    return client


def main() -> None:
    mint = mint_identity()
    attest = MintAttestCallback(client=mint, work_type="generation")

    # One simple task. Any chain works — this one needs no LLM so it just runs.
    chain = RunnableLambda(lambda x: {"summary": f"Summary of {x['topic']}: "
                                                  "verifiable proof of work for AI agents."})
    result = chain.invoke({"topic": "MINT Protocol"}, config={"callbacks": [attest]})
    print("Task output:", result["summary"])

    # The callback already attested the run. Show the latest receipt.
    profile = mint.verify(mint.mint_id)
    print(f"Attestations on record: {profile.total_attestations}")
    print("Verify your work history at https://mint-explorer.vercel.app")


if __name__ == "__main__":
    main()
