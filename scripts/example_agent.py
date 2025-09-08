"""
example_agent.py

End-to-end, single-file example showing:
- Multi-agent collaboration: agents-as-tools AND handoffs
- Hosted WebSearch tool
- Event streaming for progress UIs
- Input/Output guardrails with tripwires
- Tracing configuration and custom spans
- Session memory and typed outputs

Prereqs:
  pip install openai-agents pydantic
  export OPENAI_API_KEY=...

Notes:
- Uses built-in WebSearchTool (Responses-model hosted tool).
- Uses Runner.run_streamed() for UI-friendly progress events.
"""

from __future__ import annotations
import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agents import (
    Agent,
    AgentBase,
    AgentUpdatedStreamEvent,
    ItemHelpers,
    ModelSettings,
    Runner,
    RunConfig,
    RunContextWrapper,
    SQLiteSession,
    StreamEvent,
    TResponseInputItem,
    WebSearchTool,
    function_tool,
    # Guardrails
    GuardrailFunctionOutput,
    input_guardrail,
    output_guardrail,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    # Tracing
    trace,
    custom_span,
)

# ----------------------------
# Local context (never sent to LLM)
# ----------------------------
@dataclass
class AppCtx:
    user_id: str
    prefs: Dict[str, Any] = field(default_factory=lambda: {"tone": "concise", "units": "SI"})
    domain_docs: Dict[str, str] = field(
        default_factory=lambda: {
            "battery_safety": "Keep cells 20–30°C in formation. Never exceed 60°C surface temp.",
            "gd_tolerancing": "Use datums; MMC modifiers sparingly; verify with CMM.",
        }
    )

# ----------------------------
# Typed outputs
# ----------------------------
class Answer(BaseModel):
    answer: str
    sources: List[str] = Field(default_factory=list)

class TriagedTask(BaseModel):
    route: str
    reason: str

# ----------------------------
# Tools
# ----------------------------
@function_tool
def get_pref(w: RunContextWrapper[AppCtx], key: str) -> str:
    """Return a preference from local context."""
    return str(w.context.prefs.get(key, ""))

@function_tool
def fetch_internal(w: RunContextWrapper[AppCtx], query: str) -> str:
    """Search internal docs and return a matching snippet if found."""
    q = query.lower()
    for k, v in w.context.domain_docs.items():
        if q in k or q in v.lower():
            return f"[{k}] {v}"
    return "no match"

@function_tool
def add(a: int, b: int) -> int:
    """Return a+b."""
    return a + b

# ----------------------------
# Dynamic system instructions
# ----------------------------
def dyn_instructions(w: RunContextWrapper[AppCtx], agent: AgentBase) -> str:
    tone = w.context.prefs.get("tone", "concise")
    units = w.context.prefs.get("units", "SI")
    return (
        f"You are a helpful assistant.\n"
        f"- Prefer a {tone} style.\n"
        f"- Use {units} units when relevant.\n"
        "- You may call tools to fetch user prefs, internal docs, or web search.\n"
        "- When possible, emit output in the Answer schema."
    )

# ----------------------------
# Specialist agents
# ----------------------------
# Research agent: uses web + internal docs. Emits Answer.
research_agent = Agent[AppCtx](
    name="Researcher",
    instructions="You gather up-to-date facts. Cite sources briefly.",
    tools=[WebSearchTool(max_num_results=5), fetch_internal],
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0.2),
    output_type=Answer,
)

# Math agent: simple deterministic helper.
math_agent = Agent(
    name="Math",
    instructions="You perform small calculations step by step and return the final integer only.",
    tools=[add],
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0),
)

# Writer agent: shapes final response. Pulls prefs via local context.
writer_agent = Agent[AppCtx](
    name="Writer",
    instructions=(
        "You write clear, accurate answers for engineers. "
        "You may call get_pref to respect tone. "
        "Return the final in the Answer schema with an explicit 'sources' list if given."
    ),
    tools=[get_pref],
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0.3),
    output_type=Answer,
)

# ----------------------------
# Orchestrator agent (central brain)
# - Uses agents-as-tools for collaboration
# - Also supports full handoff for ownership transfer
# ----------------------------
# Agents-as-tools (hub-and-spoke collaboration)
research_tool = research_agent.as_tool(
    tool_name="call_researcher",
    tool_description="Gather facts from the web and internal docs. Return Answer.",
)
math_tool = math_agent.as_tool(
    tool_name="call_math",
    tool_description="Do arithmetic if needed.",
)
writer_tool = writer_agent.as_tool(
    tool_name="call_writer",
    tool_description="Write the final Answer respecting prefs.",
)

# Handoffs (delegation/ownership transfer)
from agents import handoff
handoff_to_writer = handoff(writer_agent)

orchestrator = Agent[AppCtx](
    name="Orchestrator",
    instructions=(
        "You are a project manager. Plan briefly, then either:\n"
        "1) Collaborate by calling tools (research/math/writer) and assemble a final Answer yourself, or\n"
        "2) If heavy rewriting is needed, hand off to the Writer.\n"
        "Always ensure Answer.answer is the final text and Answer.sources lists citations when used."
    ),
    tools=[research_tool, math_tool, writer_tool],
    handoffs=[handoff_to_writer],
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0.2),
    output_type=Answer,
)

# ----------------------------
# Guardrails
#   - Input guardrail: block homework-like requests
#   - Output guardrail: block answers that lack sources after web use
# ----------------------------
class HWCheck(BaseModel):
    is_homework: bool
    reason: str

guardrail_classifier = Agent(
    name="Guardrail classifier",
    instructions="Determine if the user is asking to do homework. Be strict. Return JSON.",
    output_type=HWCheck,
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0),
)

@input_guardrail
async def no_homework(w: RunContextWrapper[AppCtx], agent: AgentBase, input: str | List[TResponseInputItem]) -> GuardrailFunctionOutput:
    res = await Runner.run(guardrail_classifier, input, context=w.context)
    return GuardrailFunctionOutput(output_info=res.final_output, tripwire_triggered=res.final_output.is_homework)

class OutputCheck(BaseModel):
    used_web: bool
    has_sources: bool
    reasoning: str

output_checker = Agent(
    name="Output checker",
    instructions=(
        "Given the final assistant message, say if it appears to rely on recent web data. "
        "If yes, require a non-empty sources list."
    ),
    output_type=OutputCheck,
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0),
)

@output_guardrail
async def require_sources_if_web(w: RunContextWrapper[AppCtx], agent: AgentBase, output: Answer) -> GuardrailFunctionOutput:
    msg = f"ANSWER: {output.answer}\nSOURCES: {', '.join(output.sources) if output.sources else '(none)'}"
    res = await Runner.run(output_checker, msg, context=w.context)
    trip = res.final_output.used_web and not res.final_output.has_sources
    return GuardrailFunctionOutput(output_info=res.final_output, tripwire_triggered=trip)

# Attach guardrails to the public-entry agent
public_entry = Agent[AppCtx](
    name="Public Entry",
    instructions=dyn_instructions,
    tools=[get_pref],  # minimal; orchestrator is behind it
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0.2),
    handoffs=[handoff(orchestrator)],  # immediately route to orchestrator after framing
    input_guardrails=[no_homework],
    output_guardrails=[require_sources_if_web],
    output_type=Answer,
)

# ----------------------------
# Streaming helper
# ----------------------------
async def run_with_streaming(agent: Agent[AppCtx], user_input: str, ctx: AppCtx, session: Optional[SQLiteSession] = None) -> Answer:
    """
    Start a streamed run and print high-level progress events.
    Return the final Answer.
    """
    # Optional: tighten tracing for this run
    run_config = RunConfig(tracing_disabled=False, trace_include_sensitive_data=False)

    streamed = Runner.run_streamed(agent, input=user_input, context=ctx, session=session, run_config=run_config)

    print("=== Run started ===")
    async for ev in streamed.stream_events():
        if ev.type == "raw_response_event":
            # omit token-level printing in this demo to keep stdout clean
            continue
        elif ev.type == "agent_updated_stream_event":
            upd: AgentUpdatedStreamEvent = ev
            print(f"[agent] now={upd.new_agent.name}")
        elif ev.type == "run_item_stream_event":
            it = ev.item
            if it.type == "tool_call_item":
                print(f"[tool] call -> {it.tool_name}")
            elif it.type == "tool_call_output_item":
                print(f"[tool] output len={len(it.output)}")
            elif it.type == "message_output_item":
                text = ItemHelpers.text_message_output(it)
                if text:
                    short = text[:120].replace("\n", " ")
                    print(f"[msg] {short}{'...' if len(text)>120 else ''}")

    print("=== Run finished ===")
    result = await streamed.get_final_result()
    return result.final_output

# ----------------------------
# Traced workflow
# ----------------------------
async def traced_session_demo():
    ctx = AppCtx(user_id="u-42", prefs={"tone": "precise", "units": "SI"})
    session = SQLiteSession("thread-007", "multi_agent_history.db")

    with trace("Multi-agent Q&A", metadata={"user": ctx.user_id}) as tr:
        # Custom span: useful to mark UI stages or external calls
        with custom_span("preflight", metadata={"stage": "init"}):
            pass

        try:
            ans = await run_with_streaming(
                public_entry,
                "Compare LFP vs NMC cycle-life at fast charge using recent sources. Give a short, engineer-ready answer.",
                ctx,
                session=session,
            )
            print("\nFINAL:", ans.model_dump_json(indent=2))
        except InputGuardrailTripwireTriggered:
            print("Blocked by input guardrail.")
        except OutputGuardrailTripwireTriggered:
            print("Blocked by output guardrail (missing sources after web use).")

        with custom_span("postprocess", metadata={"stage": "done"}):
            pass

# ----------------------------
# Entrypoint
# ----------------------------
if __name__ == "__main__":
    # Optional: set OPENAI_AGENTS_DISABLE_TRACING=1 to disable default tracing.
    # For demo purposes, just run once.
    asyncio.run(traced_session_demo())
```0