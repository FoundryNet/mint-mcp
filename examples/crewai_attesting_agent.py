#!/usr/bin/env python3
"""Minimal CrewAI agent that attests its finished work on MINT.

MintAttestTool gives a crew a `mint_attest` tool: when the agent finishes a unit of
work it records a tamper-evident, independently verifiable receipt — no wallet, no
keys, no blockchain code. The agent calls the tool itself when it's done.

Runs out of the box. With OPENAI_API_KEY set it runs a real crew that decides to
attest; without one it attests a sample deliverable directly through the same tool
client, so you still see verifiable proof of work end-to-end.

    pip install mint-attest[crewai] crewai
    python crewai_attesting_agent.py                        # autonomous, free cap
    OPENAI_API_KEY=sk-... python crewai_attesting_agent.py  # full crew run
"""
import json
import os
from pathlib import Path

from mint_attest import MintClient

AGENT_NAME = "crewai-attesting-agent"
CACHE = Path.home() / ".mint" / f"{AGENT_NAME}.json"


def mint_identity() -> MintClient:
    key = os.environ.get("MINT_API_KEY")
    if not key and CACHE.exists():
        key = json.loads(CACHE.read_text()).get("api_key")
    client = MintClient(api_key=key, name=AGENT_NAME, capabilities=["research"])
    actor = client.register()          # keyless self-provisions a scoped key; keyed is idempotent
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"mint_id": actor.mint_id, "api_key": client.api_key}))
    print(f"MINT identity: {actor.mint_id}")
    return client


def main() -> None:
    mint = mint_identity()

    if os.environ.get("OPENAI_API_KEY"):
        from crewai import Agent, Crew, Task
        from mint_attest.crewai import MintAttestTool
        agent = Agent(role="Researcher",
                      goal="Answer the question, then attest the finished work on MINT.",
                      backstory="A diligent analyst who proves every deliverable.",
                      tools=[MintAttestTool(client=mint)], verbose=True)
        crew = Crew(agents=[agent], tasks=[Task(
            description="Summarize the state of AI agent interoperability in 2026, then "
                        "attest the result with the mint_attest tool.",
            expected_output="A 3-sentence summary.", agent=agent)])
        print(crew.kickoff())
    else:
        print("(no OPENAI_API_KEY — attesting a sample deliverable directly)")
        receipt = mint.attest(work_type="research",
                              summary="Summarized the state of AI agent interoperability in 2026.",
                              output_data="A concise 3-sentence summary.", duration_seconds=8)
        print(f"Attested: {receipt.attestation_id}  hash={receipt.raw.get('attestation_hash')}")

    print(f"Attestations on record: {mint.verify(mint.mint_id).total_attestations}")
    print("Verify your work history at https://mint-explorer.vercel.app")


if __name__ == "__main__":
    main()
