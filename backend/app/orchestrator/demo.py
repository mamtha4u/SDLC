"""Demo simulation: walks the crew through a scripted run so the live UI can be exercised before the
real agents exist (Phase 0/1). Clearly labelled as a simulation in every event; makes no AWS or LLM calls."""
from __future__ import annotations

import asyncio
import random

from app.orchestrator.crew import CREW, FLOW
from app.orchestrator.runner import JobContext, handler

SCRIPT: dict[str, list[str]] = {
    "intake": ["Reading the requirement", "Asking about the SQS type and DLQ", "Playing back the understanding",
               "Requirement signed off"],
    "ba": ["Extracting source fields", "Mapping to target schema", "Writing validation rules"],
    "ta": ["Drafting the HLD", "Resolving AWS icons via draw.io MCP", "Rendering the architecture diagram",
           "Vision-checking the diagram against the LLD"],
    "tp": ["Generating CloudFormation", "Computing change set", "Awaiting deploy approval", "Connectivity test"],
    "de": ["Writing the Lambda handler", "Writing pytest cases", "pytest: 24 passed"],
    "qa": ["Sending test requests", "Checking SQS messages against the mapping", "Negative cases: DLQ redrive"],
}
NAMES = {a["key"]: a["persona"] for a in CREW}


@handler("demo.simulation")
async def run_demo(ctx: JobContext) -> None:
    speed = float(ctx.payload.get("speed", 1.0))
    start = int(ctx.checkpoint.get("agent_index", 0))
    if start == 0:
        for key in FLOW:
            await ctx.set_agent(key, "waiting", "Waiting for upstream")
        await ctx.set_project(status="running", progress=0.0)
        await ctx.set_agent("cto", "working", "Planning the run (simulation)")
        await ctx.emit("activity", f"{NAMES['cto']} started a simulation run", agent="cto")

    for i in range(start, len(FLOW)):
        key = FLOW[i]
        await ctx.gate()
        await ctx.set_agent(key, "working", SCRIPT[key][0])
        await ctx.emit("activity", f"{NAMES[key]} started", agent=key)
        for step in SCRIPT[key][1:]:
            await asyncio.sleep(random.uniform(1.2, 2.4) / speed)
            await ctx.gate()
            if "Awaiting" in step:
                await ctx.set_agent(key, "needs_approval", step)
                await ctx.emit("activity", f"{NAMES[key]} requested approval (auto-approved in simulation)", agent=key)
                await asyncio.sleep(2.5 / speed)
                await ctx.set_agent(key, "working", "Approved — deploying")
                continue
            tokens = random.randint(800, 4000)
            await ctx.set_agent(key, "working", step, tokens_out=tokens)
        await ctx.set_agent(key, "done", f"{NAMES[key]} finished")
        await ctx.emit("activity", f"{NAMES[key]} finished", agent=key)
        await ctx.set_project(progress=round((i + 1) / len(FLOW), 3), last_activity=f"{NAMES[key]} finished")
        await ctx.save_checkpoint(agent_index=i + 1)

    await ctx.set_agent("cto", "done", "Simulation complete")
    await ctx.set_project(status="completed", progress=1.0, current_agent=None)
    await ctx.emit("activity", "Simulation complete — all agents done", agent="cto")
