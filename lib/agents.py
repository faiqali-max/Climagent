import asyncio
import json
import time

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from lib import memory, monitoring, observability, tools
from lib.llm import get_llm

MONITORING_PROMPT = """You are the Temperature & Environment Monitoring Agent of Climagent.
You monitor locations: fetch real-time conditions, forecasts (up to 12h), historical comparisons, and detect heat anomalies.
Autonomously choose which FortyGuard tools you need (snapshot, forecast, history, environmental parameters).
Do not answer from general knowledge - always pull live data with tools. Use °C. Report what the data shows and what it implies."""

HEAT_RISK_PROMPT = """You are the Heat Risk & Public Safety Agent of Climagent.
Monitor dangerous heat conditions and produce a public-safety verdict. Use tools to get live temperature,
humidity/heat-index and heat-stress data, then call assess_heat_risk to score the location.
Distinguish LOW / MODERATE / HIGH / EXTREME risk. Emit localized warnings, shelter and emergency
preparedness recommendations based on the computed risk level and real data."""

COOLING_PROMPT = """You are the Smart City Urban Cooling Planner Agent of Climagent.
Analyze heatmaps, historical temperatures and heat-stress data to identify urban heat hotspots and
prioritized cooling interventions (tree canopy, cool roofs/pavements, green roofs, shade infrastructure,
water-sensitive design, reflective materials). Produce: priority zones, recommended intervention,
expected benefits, implementation priority, risks, assumptions, data limitations.
Never claim exact climate impact unless supported by data; label estimates as estimates."""

MITIGATION_PROMPT = """You are the Climate Change Mitigation Agent of Climagent.
Identify climate risks, analyze environmental data, and develop phased climate action plans.
Use tools to pull historical temperature, heat-stress, environmental parameters (CO2, methane, AQI) and
heat intelligence. Produce a plan with phases: 1 Assessment, 2 Immediate Actions, 3 Medium-Term
Implementation, 4 Long-Term Mitigation, 5 Monitoring & Evaluation.
Clearly separate data-supported findings, assumptions, recommendations and estimates."""

DATA_PROMPT = """You are the Data Analysis Agent of Climagent.
A dataset file has been uploaded and summarized for you. Use dataset_summary and explore_dataset to inspect it,
then decide what analysis is appropriate (missing values, anomalies, trends, correlations, forecasts).
Explain findings in clear human-readable language with specific numbers. Note data quality issues."""

CONSTRUCTION_PROMPT = """You are the Construction Planning Agent of Climagent.
Combine construction planning with environmental intelligence to create project plans, phased schedules,
weather-aware and heat-aware worker schedules, resource plans, risk analysis and safety recommendations.
Workflow: analyze project data -> analyze location -> fetch FortyGuard data -> check forecast ->
identify dangerous working hours -> generate safer work windows -> recommend breaks and hydration -> create schedule.
Always end with the disclaimer: recommendations require review by qualified engineers, safety professionals,
or local authorities. Never present the plan as an approved document."""

HVAC_PROMPT = """You are the HVAC & Energy Optimization Agent of Climagent.
For facilities and building operations: analyze external temperature conditions and forecasts, detect upcoming
heat spikes, and recommend proactive cooling strategies, HVAC scheduling improvements, and energy optimization
opportunities. Provide recommendations only. NEVER automatically control critical infrastructure without
explicit authorization and appropriate safeguards."""

RESEARCH_PROMPT = """You are the Research & Knowledge Agent of Climagent.
Retrieve relevant climate information from approved sources (the built-in knowledge base, saved context, and
FortyGuard data). Validate claims where possible and provide source information. Classify information as
reliable, uncertain, or conflicting. Do not blindly trust data or search results; state uncertainty explicitly.
If the knowledge base lacks an answer, say so instead of guessing."""

QA_PROMPT = """You are the Quality Assurance & Validation Agent of Climagent.
Review the draft output you are given. Check: logical consistency, calculations, hallucinations,
unsupported claims, whether the requested task was completed, and whether important data is missing.
Validate recommendations against available evidence. Report: confidence level, limitations, assumptions,
and data sources used. If the work is incomplete or unreliable, start with 'REVISION REQUIRED' and list
specific issues so the Manager Agent can correct them."""

MEMORY_PROMPT = """You are the Memory & Context Agent of Climagent.
Manage persistent memory: user preferences, projects, locations, approved plans, decisions and analysis context.
Use store_memory to save important durable context and recall_memory to retrieve relevant saved context.
Be concise. Never store passwords, API keys, or other sensitive credentials."""

MONITOR_PROMPT = """You are the Climagent monitoring agent.
Fetch live conditions for the given location using the tools, compare with the previous reading, and reply
with ONLY a JSON object: {"current_temp_c": <number>, "risk_level": "<low|moderate|high|extreme>",
"significant_change": <true|false>, "recommendation": "<one short sentence>"}. No other text."""

HITL_GUIDANCE = {
    "suggestion": "MODE: SUGGESTION. Provide recommendations only. Never imply that high-impact actions were executed. Explicitly flag anything that requires human approval.",
    "approval": "MODE: APPROVAL. Prepare high-impact actions fully, but explicitly wait for human approval before they are considered done. Say clearly that approval is pending.",
    "automated": "MODE: AUTOMATED. Only low-risk, explicitly authorized actions may be treated as executed; all automated actions are logged. High-impact actions still require human approval.",
}

MANAGER_PROMPT = """You are the Manager Agent of Climagent, an autonomous climate, heat intelligence and
decision platform. You coordinate specialized agents and tools to solve the user's objective.

Your specialized agents:
- Temperature & Environment Monitoring Agent: real-time conditions, forecasts (up to 12h), historical comparisons, heat anomalies.
- Heat Risk & Public Safety Agent: heat-risk scoring, warnings, cooling shelters, emergency actions.
- Smart City Urban Cooling Planner: heatmaps, hotspots, prioritized urban cooling interventions.
- Climate Change Mitigation Agent: climate risks, environmental data, 5-phase climate action plans.
- Data Analysis Agent: analyzes uploaded datasets (CSV/Excel/JSON): trends, anomalies, correlations, forecasts.
- Construction Planning Agent: project plans, weather-aware and heat-aware work schedules, safety recommendations.
- HVAC & Energy Optimization Agent: facilities energy optimization and proactive cooling recommendations (recommendations only).
- Research & Knowledge Agent: curated climate knowledge and source validation; identifies reliable/uncertain/conflicting information.
- Quality Assurance Agent: validates critical outputs, gives confidence levels and limitations, catches hallucinations and unsupported claims.
- Memory & Context Agent: stores and retrieves durable context (preferences, projects, locations, plans, decisions).

Rules:
1. Understand the objective, break it into tasks, and invoke ONLY the relevant agents/tools. Never run everything.
2. Call multiple agents/tools in parallel when tasks are independent.
3. Analyze, combine and verify outputs; if a task is incomplete or data is missing, run more analysis or ask the user.
4. For critical outputs (public safety, construction schedules, urban planning, warnings), call delegate_to_quality_assurance with your draft and key data, then incorporate its confidence level, limitations and corrections into your final answer.
5. When you learn durable facts (user preferences, project locations, approved plans, decisions), call delegate_to_memory to store them.
6. Clearly separate data-supported findings from assumptions/estimates. Never fabricate data; if a tool errors or returns nothing, say so and explain what is needed.
7. {hitl}
8. Answer in the same language the user used. Use °C. Use markdown headings and bullet points for readability."""


def _build_agent(llm, system_prompt, agent_tools):
    return create_react_agent(llm, agent_tools, prompt=system_prompt)


def _steps_from(messages):
    steps = []
    for msg in messages:
        calls = getattr(msg, "tool_calls", None)
        if calls:
            for tc in calls:
                steps.append({"tool": tc["name"], "args": tc.get("args", {})})
    return steps


def _short(value, limit=500):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def _make_delegate(name, description, agent):
    async def _delegate(request: str) -> str:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=request)]}, config={"recursion_limit": 30}
        )
        messages = result["messages"]
        final = messages[-1].content or "(agent produced no text)"
        return json.dumps(
            {
                "agent": name,
                "steps": [s["tool"] for s in _steps_from(messages)],
                "result": final,
            },
            ensure_ascii=False,
        )

    _delegate.__name__ = f"delegate_to_{name}"
    _delegate.__doc__ = description
    return tool(_delegate)


def _memory_tools(user_id):
    @tool
    def store_memory(content: str, kind: str = "context") -> str:
        """Save an important fact, preference, project location, approved plan or decision to long-term memory."""
        memory.add(user_id, kind, content)
        return f"Stored memory ({kind}): {str(content)[:120]}"

    @tool
    def recall_memory(query: str) -> str:
        """Retrieve relevant previously-saved context from long-term memory."""
        result = memory.recall_context(user_id, query, limit=5)
        return result or "No relevant saved memory found."

    return store_memory, recall_memory


def build_agents(user_id="default", mode="suggestion"):
    llm = get_llm()
    store_mem, recall_mem = _memory_tools(user_id)
    monitoring_agent = _build_agent(llm, MONITORING_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_forecast,
        tools.get_temperature_history, tools.get_environmental_parameters])
    heat_risk = _build_agent(llm, HEAT_RISK_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_forecast,
        tools.get_environmental_parameters, tools.get_heat_stress, tools.assess_heat_risk])
    cooling = _build_agent(llm, COOLING_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_history,
        tools.get_heat_stress, tools.get_temperature_heatmap, tools.get_environmental_parameters])
    mitigation = _build_agent(llm, MITIGATION_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_history,
        tools.get_environmental_parameters, tools.get_heat_intelligence])
    data_agent = _build_agent(llm, DATA_PROMPT, [tools.dataset_summary, tools.explore_dataset])
    construction = _build_agent(llm, CONSTRUCTION_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_forecast,
        tools.get_temperature_history, tools.get_heat_stress, tools.get_environmental_parameters])
    hvac = _build_agent(llm, HVAC_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_forecast,
        tools.get_temperature_history, tools.get_heat_stress, tools.get_environmental_parameters])
    research = _build_agent(llm, RESEARCH_PROMPT, [
        tools.knowledge_base, recall_mem, tools.get_temperature_history,
        tools.get_environmental_parameters, tools.get_heat_intelligence])
    qa = _build_agent(llm, QA_PROMPT, [
        tools.knowledge_base, recall_mem, tools.get_temperature_history, tools.assess_heat_risk])
    memory_agent = _build_agent(llm, MEMORY_PROMPT, [store_mem, recall_mem])

    delegates = [
        _make_delegate("temperature_monitoring",
                       "Delegate a monitoring task to the Temperature & Environment Monitoring Agent "
                       "(real-time conditions, forecasts, historical comparisons, heat anomalies). Pass a precise request with location.",
                       monitoring_agent),
        _make_delegate("heat_risk",
                       "Delegate a public-safety task to the Heat Risk & Public Safety Agent "
                       "(heat-risk scores, warnings, shelters, emergency actions). Pass a precise request with location.",
                       heat_risk),
        _make_delegate("urban_cooling",
                       "Delegate urban planning to the Smart City Urban Cooling Planner Agent "
                       "(heatmaps, hotspots, prioritized cooling interventions). Pass a precise request with location.",
                       cooling),
        _make_delegate("climate_mitigation",
                       "Delegate to the Climate Change Mitigation Agent "
                       "(climate risks, phased climate action plans). Pass a precise request with location.",
                       mitigation),
        _make_delegate("data_analysis",
                       "Delegate dataset analysis to the Data Analysis Agent (uploaded files). Pass the file path and the question.",
                       data_agent),
        _make_delegate("construction",
                       "Delegate to the Construction Planning Agent "
                       "(project plans, weather-aware and heat-aware work schedules, safety recommendations). Pass a precise request with project + location.",
                       construction),
        _make_delegate("hvac",
                       "Delegate to the HVAC & Energy Optimization Agent "
                       "(proactive cooling recommendations, HVAC scheduling, energy optimization). Pass a precise request with building/location.",
                       hvac),
        _make_delegate("research",
                       "Delegate to the Research & Knowledge Agent "
                       "(curated climate knowledge, source validation, reliability assessment). Pass a precise question.",
                       research),
        _make_delegate("quality_assurance",
                       "Delegate validation of a critical draft to the Quality Assurance Agent. Pass the full draft plus key data. Returns confidence, limitations, assumptions and issues.",
                       qa),
        _make_delegate("memory",
                       "Delegate to the Memory & Context Agent to store durable context (preferences, projects, locations, plans, decisions) or retrieve saved context.",
                       memory_agent),
    ]
    hitl = HITL_GUIDANCE.get(mode, HITL_GUIDANCE["suggestion"])
    return create_react_agent(llm, delegates, prompt=MANAGER_PROMPT.replace("{hitl}", hitl))


def build_monitor_agent():
    llm = get_llm()
    return _build_agent(llm, MONITOR_PROMPT, [
        tools.get_temperature_snapshot, tools.get_temperature_forecast,
        tools.get_environmental_parameters, tools.assess_heat_risk])


def build_trace(messages):
    trace = []
    by_id = {}
    for msg in messages:
        calls = getattr(msg, "tool_calls", None)
        if calls:
            for tc in calls:
                entry = {"tool": tc["name"], "args": _short(tc.get("args", {}), 300)}
                trace.append(entry)
                if tc.get("id"):
                    by_id[tc["id"]] = entry
        elif isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", "") in by_id:
            entry = by_id[msg.tool_call_id]
            entry["result"] = _short(msg.content, 800)
            try:
                parsed = json.loads(msg.content)
                if isinstance(parsed, dict) and "agent" in parsed:
                    entry["steps"] = parsed.get("steps", [])
                    entry["agent"] = parsed.get("agent", "")
            except Exception:
                pass
    return trace


def _sum_tokens(messages):
    total = 0
    for msg in messages:
        meta = getattr(msg, "response_metadata", None) or {}
        usage = meta.get("token_usage") or {}
        total += int(usage.get("total_tokens") or 0)
    return total


async def run_chat(user_message, location=None, date="", user_id="default"):
    mode = monitoring.get_mode(user_id)
    run_id = observability.start_run(user_id, user_message)
    t0 = time.time()
    context = user_message
    prior = memory.recall_context(user_id, user_message)
    if prior:
        context = f"Relevant saved context:\n{prior}\n\nNew request: {user_message}"
    if location and location.get("lat") is not None and location.get("lon") is not None:
        context += f"\n\nLocation context (user provided): latitude={location['lat']}, longitude={location['lon']}."
    if date:
        context += f"\nDate context (user provided): {date}."
    try:
        manager = build_agents(user_id=user_id, mode=mode)
        result = await manager.ainvoke(
            {"messages": [HumanMessage(content=context)]}, config={"recursion_limit": 40}
        )
        messages = result["messages"]
        reply = messages[-1].content or "(no textual response)"
        trace = build_trace(messages)
        duration = (time.time() - t0) * 1000
        tokens = _sum_tokens(messages)
        observability.end_run(user_id, run_id, outcome=reply, duration_ms=duration, tokens=tokens)
        for step in trace:
            observability.log_tool(user_id, run_id, step["tool"], step.get("args"), step.get("result"))
        if location and location.get("lat") is not None:
            memory.add(user_id, "location",
                       f"User analyzed location lat={location['lat']} lon={location['lon']}: {user_message[:120]}")
        memory.add(user_id, "context", f"User asked: {user_message[:200]}")
        agents_used = sorted({t["tool"].replace("delegate_to_", "") for t in trace})
        return {"reply": reply, "trace": trace, "agents_used": agents_used, "mode": mode, "run_id": run_id}
    except Exception as exc:
        observability.end_run(user_id, run_id, outcome="failed", error=str(exc)[:500],
                              duration_ms=(time.time() - t0) * 1000)
        raise


async def analyze_file(path, file_name, user_id="default", data=None):
    run_id = observability.start_run(user_id, f"file analysis: {file_name}")
    t0 = time.time()
    llm = get_llm()
    data = await asyncio.to_thread(tools.analyze_dataset, path, data)
    summary = tools.dataset_text(data)
    agent = _build_agent(llm, DATA_PROMPT, [tools.dataset_summary, tools.explore_dataset])
    prompt = (f"Analyze the uploaded file '{file_name}'. Pre-computed summary:\n{summary}\n"
              "Use tools to dig deeper if useful, then give a clear human-readable analysis "
              "with specific numbers, data-quality notes and recommendations.")
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]}, config={"recursion_limit": 20}
    )
    explanation = result["messages"][-1].content or "(no textual response)"
    duration = (time.time() - t0) * 1000
    observability.end_run(user_id, run_id, outcome=explanation, duration_ms=duration)
    for step in _steps_from(result["messages"]):
        observability.log_tool(user_id, run_id, step["tool"], step.get("args"))
    memory.add(user_id, "analysis", f"Analyzed {file_name}: {summary[:200]}")
    return {"explanation": explanation, "data": data, "steps": _steps_from(result["messages"])}
