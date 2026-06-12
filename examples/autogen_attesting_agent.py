#!/usr/bin/env python3
"""Minimal AutoGen agent that attests its replies on MINT.

A MintAttestHook attaches to an AutoGen agent and records a tamper-evident,
independently verifiable receipt for the work behind each reply — no wallet, no
keys, no blockchain code. The hook never alters the message; it only observes.

Runs out of the box. With OPENAI_API_KEY set it runs a real one-turn chat and
attests the model's reply; without one it drives a single observed turn so you can
still watch attestation work end-to-end.

    pip install mint-attest pyautogen
    python autogen_attesting_agent.py                       # autonomous, free cap
    OPENAI_API_KEY=sk-... python autogen_attesting_agent.py # full LLM turn
"""
import json
import os
from pathlib import Path

from autogen import AssistantAgent, UserProxyAgent

from mint_attest import MintClient
from mint_attest.autogen import MintAttestHook

AGENT_NAME = "autogen-attesting-agent"
CACHE = Path.home() / ".mint" / f"{AGENT_NAME}.json"
TASK = "Summarize MINT Protocol in one sentence."


def mint_identity() -> MintClient:
    key = os.environ.get("MINT_API_KEY")
    if not key and CACHE.exists():
        key = json.loads(CACHE.read_text()).get("api_key")
    client = MintClient(api_key=key, name=AGENT_NAME, capabilities=["generation"])
    actor = client.register()          # keyless self-provisions a scoped key; keyed is idempotent
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"mint_id": actor.mint_id, "api_key": client.api_key}))
    print(f"MINT identity: {actor.mint_id}")
    return client


def main() -> None:
    mint = mint_identity()
    api_key = os.environ.get("OPENAI_API_KEY")
    llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": api_key}]} if api_key else False

    assistant = AssistantAgent("assistant", llm_config=llm_config)
    user = UserProxyAgent("user", human_input_mode="NEVER", code_execution_config=False,
                          max_consecutive_auto_reply=0)
    MintAttestHook(client=mint).attach(assistant)   # every reply is attested

    if api_key:
        user.initiate_chat(assistant, message=TASK)
    else:
        print("(no OPENAI_API_KEY — driving one observed turn so attestation still runs)")
        assistant.generate_reply(messages=[{"role": "user", "content": TASK}], sender=user)

    print(f"Attestations on record: {mint.verify(mint.mint_id).total_attestations}")
    print("Verify your work history at https://mint-explorer.vercel.app")


if __name__ == "__main__":
    main()
