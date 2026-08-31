import asyncio
import json
import os
import re

import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

import fortyguard as fg

DEFAULT_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")


class AgentConfigError(Exception):
    pass


PLACEHOLDERS = {"", "your-llm-api-key", "your-fortyguard-api-key", "your-langsmith-api-key",
                "your-api-key", "changeme", "your-key", "key"}


def is_configured(name):
    value = os.getenv(name, "").strip()
    return bool(value) and value.lower() not in PLACEHOLDERS


def get_llm(temperature=0.2):
    if not is_configured("LLM_API_KEY"):
        raise AgentConfigError(
            "LLM_API_KEY is not configured. Add it to the .env file (any OpenAI-compatible "
            "provider works, e.g. OpenAI, DeepSeek, Groq)."
        )
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    return ChatOpenAI(model=DEFAULT_MODEL, api_key=os.getenv("LLM_API_KEY", "").strip(),
                      base_url=base_url, temperature=temperature)


def _float_env(name, default):
    value = os.getenv(name, "").strip()
    return float(value) if value else default


RISK_THRESHOLDS = {
    "low": _float_env("HEAT_RISK_LOW", 27.0),
    "moderate": _float_env("HEAT_RISK_MODERATE", 31.0),
    "high": _float_env("HEAT_RISK_HIGH", 35.0),
    "extreme": _float_env("HEAT_RISK_EXTREME", 40.0),
}


def heat_index(temperature_c, humidity):
    t = float(temperature_c)
    rh = float(humidity)
    if t < 27 or rh < 40:
        return t
    hi = (-8.784695 + 1.61139411 * t + 2.338549 * rh - 0.14611605 * t * rh
          - 1.2308094e-2 * t * t - 1.6424828e-2 * rh * rh
          + 2.211732e-3 * t * t * rh + 7.2546e-4 * t * rh * rh
          - 3.582e-6 * t * t * rh * rh)
    return hi


@tool
def assess_heat_risk(temperature_c: float, humidity: float = None) -> str:
    """Classify heat risk for a temperature (and optional humidity %). Returns risk level,
    a 0-100 score and recommended public-safety actions. Thresholds are administrator-configurable."""
    rh = humidity if humidity is not None else 0.0
    feels = heat_index(temperature_c, rh)
    levels = [("extreme", RISK_THRESHOLDS["extreme"]), ("high", RISK_THRESHOLDS["high"]),
              ("moderate", RISK_THRESHOLDS["moderate"]), ("low", RISK_THRESHOLDS["low"])]
    level = "low"
    for name, th in levels:
        if feels >= th:
            level = name
            break
    bounds = {
        "low": (0, RISK_THRESHOLDS["low"]),
        "moderate": (RISK_THRESHOLDS["low"], RISK_THRESHOLDS["moderate"]),
        "high": (RISK_THRESHOLDS["moderate"], RISK_THRESHOLDS["high"]),
        "extreme": (RISK_THRESHOLDS["high"], RISK_THRESHOLDS["extreme"] + 25),
    }
    lo, hi = bounds[level]
    span = max(0.1, hi - lo)
    score = min(100.0, max(0.0, 40.0 + (feels - lo) / span * 60.0))
    actions = {
        "low": "No special precautions required.",
        "moderate": "Provide water, shade and rest breaks for outdoor workers; monitor vulnerable people.",
        "high": "Limit strenuous outdoor work, schedule work in cooler hours, activate cooling shelters, issue heat advisories.",
        "extreme": "Halt or drastically reduce outdoor work, open emergency cooling shelters, issue extreme-heat warnings, check vulnerable residents.",
    }
    return json.dumps(
        {
            "air_temperature_c": round(float(temperature_c), 1),
            "humidity_percent": rh,
            "feels_like_c": round(feels, 1),
            "risk_level": level,
            "risk_score": round(score, 1),
            "recommended_actions": actions[level],
            "thresholds_c": RISK_THRESHOLDS,
        },
        ensure_ascii=False,
    )


def _run_safe(fn, *args):
    try:
        return fn(*args)
    except fg.FortyGuardError as exc:
        return f"FortyGuard error: {exc}"
    except Exception as exc:
        return f"Tool error: {exc}"


@tool
def get_current_temperature(latitude: float, longitude: float) -> str:
    """Fetch the current (near-real-time) air temperature snapshot for a location via FortyGuard."""
    return _run_safe(fg.current_temperature, latitude, longitude)


@tool
def get_forecast_temperature(latitude: float, longitude: float, hours_ahead: int = 6) -> str:
    """Fetch a temperature forecast for a location, up to 12 hours ahead (FortyGuard limit)."""
    return _run_safe(fg.forecast_temperature, latitude, longitude, hours_ahead)


@tool
def get_historical_temperatures(latitude: float, longitude: float, start_date: str, end_date: str = "") -> str:
    """Fetch historical temperature statistics for a date range (YYYY-MM-DD). Up to 1 month."""
    return _run_safe(fg.historical_temperatures, latitude, longitude, start_date, end_date)


@tool
def get_heat_exceedance(latitude: float, longitude: float, start_date: str, end_date: str = "",
                        threshold: float = 30.0, direction: str = "above") -> str:
    """Hours above (or below) a temperature threshold over a date range - heat-stress analysis."""
    return _run_safe(fg.heat_exceedance, latitude, longitude, start_date, end_date, threshold, direction)


@tool
def get_env_parameters(latitude: float, longitude: float, temperature: float,
                       date: str = "", time: str = "") -> str:
    """Environmental parameters for a location: heat index, wet-bulb temperature, humidity, AQI, CO2, methane, solar irradiance."""
    return _run_safe(fg.env_params, latitude, longitude, temperature, date, time)


@tool
def get_heat_intelligence(latitude: float, longitude: float, temperature: float,
                          date: str, categories: str = "geographic,environmental,urban") -> str:
    """Multi-dimensional Heat Intelligence Report. categories: geographic, environmental, urban, events, anthropogenic."""
    return _run_safe(fg.heat_intelligence, latitude, longitude, temperature, date, categories)


@tool
def generate_heatmap(latitude: float, longitude: float, start_date: str, end_date: str = "",
                     analytic_type: str = "tcm", threshold: float = 30.0,
                     direction: str = "above", granularity: int = 100) -> str:
    """Generate a heatmap over the local area around a point. analytic_type: tcm (temperature),
    exceedance (hours over threshold), persistence (longest continuous run), time_of_measure."""
    return _run_safe(fg.generate_heatmap, latitude, longitude, start_date, end_date, analytic_type,
                     threshold, direction, granularity)


def _read_table(path):
    ext = path.lower().rsplit(".", 1)[-1]
    if ext == "csv":
        try:
            df = pd.read_csv(path)
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="latin-1")
    elif ext in ("xlsx", "xls"):
        df = pd.read_excel(path)
    elif ext == "json":
        df = pd.read_json(path)
    else:
        raise ValueError("Unsupported file type. Upload a CSV, Excel, or JSON file.")
    if len(df) > 200000:
        df = df.sample(200000, random_state=1)
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]):
            sample = df[col].dropna()
            if not sample.empty and isinstance(sample.iloc[0], str) and re.match(r"\d{4}[-/]\d{1,2}", sample.iloc[0]):
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except Exception:
                    pass
    return df


def _analyze_dataset(path):
    df = _read_table(path)
    numeric = list(df.select_dtypes(include=[np.number]).columns)
    datetime_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    object_cols = [c for c in df.columns if c not in numeric and c not in datetime_cols]
    data = {
        "shape": list(df.shape),
        "file_columns": [{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns],
        "missing": {str(c): int(df[c].isna().sum()) for c in df.columns},
        "numeric_stats": {},
        "correlations": None,
        "histograms": {},
        "series": [],
        "categorical": {},
        "anomalies": {},
        "head": df.head(8).astype(str).where(df.notna(), "").values.tolist(),
    }
    for col in numeric[:12]:
        series = df[col]
        if series.nunique() <= 1:
            continue
        data["numeric_stats"][str(col)] = {
            "mean": round(float(series.mean()), 3),
            "std": round(float(series.std()), 3),
            "min": round(float(series.min()), 3),
            "25%": round(float(series.quantile(0.25)), 3),
            "50%": round(float(series.median()), 3),
            "75%": round(float(series.quantile(0.75)), 3),
            "max": round(float(series.max()), 3),
        }
        counts, bins = np.histogram(series.dropna(), bins=30)
        data["histograms"][str(col)] = {
            "labels": [round(float((bins[i] + bins[i + 1]) / 2), 2) for i in range(len(bins) - 1)],
            "values": [int(v) for v in counts],
        }
        mean, std = float(series.mean()), float(series.std())
        z = ((series - mean) / std).abs() if std and std > 0 else pd.Series(0, index=series.index)
        anom = series.index[z > 3]
        if len(anom) > 0:
            data["anomalies"][str(col)] = [int(i) for i in anom[:100]]
    if len(numeric) > 1:
        corr = df[numeric[:15]].corr().round(3).fillna(0)
        data["correlations"] = {
            "columns": [str(c) for c in corr.columns],
            "matrix": corr.values.tolist(),
        }
    for col in object_cols:
        counts = df[col].value_counts().head(20)
        if len(counts) > 0:
            data["categorical"][str(col)] = {
                "labels": [str(k) for k in counts.index],
                "values": [int(v) for v in counts.values],
            }
    if datetime_cols:
        dt_col = datetime_cols[0]
        for col in numeric[:6]:
            series = pd.DataFrame({"t": df[dt_col], "v": df[col]}).dropna().sort_values("t")
            series = series.tail(500)
            data["series"].append(
                {
                    "label": f"{col} over time",
                    "labels": [str(x)[:16] for x in series["t"]],
                    "values": [round(float(v), 3) for v in series["v"]],
                }
            )
    return data


def _dataset_text(data):
    lines = [f"Dataset shape: {data['shape'][0]} rows x {data['shape'][1]} columns."]
    for col in data["file_columns"]:
        missing = data["missing"].get(col["name"], 0)
        lines.append(f"- {col['name']} ({col['dtype']}), missing={missing}")
    if data["numeric_stats"]:
        lines.append("Key numeric stats:")
        for name, st in list(data["numeric_stats"].items())[:8]:
            lines.append(f"  - {name}: mean={st['mean']}, min={st['min']}, max={st['max']}, median={st['50%']}")
    if data["correlations"]:
        strong = []
        cols = data["correlations"]["columns"]
        m = data["correlations"]["matrix"]
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                v = m[i][j]
                if abs(v) > 0.6:
                    strong.append(f"{cols[i]} vs {cols[j]} ({v})")
        lines.append("Strong correlations: " + ("; ".join(strong) if strong else "none above 0.6"))
    for col, count in data["anomalies"].items():
        lines.append(f"Anomalies detected in {col}: {len(count)} rows (|z|>3)")
    if data["series"]:
        lines.append(f"Time-series columns detected: {', '.join(s['label'] for s in data['series'][:3])}")
    return "\n".join(lines)


@tool
def dataset_summary(file_path: str) -> str:
    """Summarize an uploaded dataset file (CSV/Excel/JSON) - shape, columns, types, missing values, head sample."""
    return _run_safe(lambda p: _dataset_text(_analyze_dataset(p)), file_path)


@tool
def explore_dataset(file_path: str) -> str:
    """Full statistical analysis of an uploaded dataset: stats, correlations, histograms, time series, anomalies, categorical counts."""
    return _run_safe(
        lambda p: json.dumps(_analyze_dataset(p), ensure_ascii=False, default=str)[:8000], file_path
    )


def _build_agent(llm, system_prompt, tools):
    return create_react_agent(llm, tools, prompt=system_prompt)


MONITORING_PROMPT = """You are the Temperature & Environment Monitoring Agent of Climagent.
You monitor locations: fetch real-time conditions, forecasts (up to 12h), historical comparisons, and detect heat anomalies.
Autonomously choose which FortyGuard tools you need (snapshot, forecast, historical, exceedance, env parameters).
Do not answer from general knowledge - always pull live data with tools. Use °C. Report what the data shows and what it implies."""

HEAT_RISK_PROMPT = """You are the Heat Risk & Public Safety Agent of Climagent.
Monitor dangerous heat conditions and produce a public-safety verdict. Use tools to get live temperature,
humidity/heat-index and heat-stress (exceedance) data, then call assess_heat_risk to score the location.
Distinguish LOW / MODERATE / HIGH / EXTREME risk. Emit localized warnings, shelter and emergency
preparedness recommendations based on the computed risk level and real data."""

COOLING_PROMPT = """You are the Smart City Urban Cooling Planner Agent of Climagent.
Analyze heatmaps, historical temperatures and exceedance data to identify urban heat hotspots and
prioritized cooling interventions (tree canopy, cool roofs/pavements, green roofs, shade infrastructure,
water-sensitive design, reflective materials). Produce: priority zones, recommended intervention,
expected benefits, implementation priority, risks, assumptions, data limitations.
Never claim exact climate impact unless supported by data; label estimates as estimates."""

MITIGATION_PROMPT = """You are the Climate Change Mitigation Agent of Climagent.
Identify climate risks, analyze environmental data, and develop phased climate action plans.
Use tools to pull historical temperature, exceedance, environmental parameters (CO2, methane, AQI) and
heat intelligence. Produce a plan with phases: 1 Assessment, 2 Immediate Actions, 3 Medium-Term
Implementation, 4 Long-Term Mitigation, 5 Monitoring & Evaluation.
Clearly separate data-supported findings, assumptions, recommendations and estimates."""

DATA_PROMPT = """You are the Data Analysis Agent of Climagent.
A dataset file has been uploaded and summarized for you. Use dataset_summary and explore_dataset to inspect it,
then decide what analysis is appropriate (missing values, anomalies, trends, correlations, forecasts).
Explain findings in clear human-readable language with specific numbers. Note data quality issues."""

MANAGER_PROMPT = """You are the Manager Agent of Climagent, an autonomous climate, heat intelligence and
decision platform. You coordinate specialized agents and tools to solve the user's objective.

Your team:
- Temperature & Environment Monitoring Agent: real-time conditions, forecasts, historical comparisons, heat anomalies.
- Heat Risk & Public Safety Agent: heat-risk scoring, warnings, cooling shelters, emergency actions.
- Smart City Urban Cooling Planner: heatmaps, hotspots, prioritized urban cooling interventions.
- Climate Change Mitigation Agent: climate risks, environmental data, 5-phase climate action plans.
- Data Analysis Agent: analyzes uploaded datasets (CSV/Excel/JSON): trends, anomalies, correlations, forecasts.

Rules:
1. Understand the objective, break it into tasks, and invoke ONLY the relevant agents/tools. Never run everything.
2. Call multiple agents/tools in parallel when the tasks are independent.
3. Analyze, combine and verify the outputs; if a task is incomplete or data is missing, run more analysis or ask the user.
4. Clearly separate data-supported findings from assumptions/estimates.
5. If a decision has significant real-world impact (public-safety warnings, large expenditures, major interventions), explicitly flag that human approval is required.
6. Never fabricate data. If a tool errors or returns nothing, say so and explain what is needed.
7. Answer in the same language the user used. Use °C. Use markdown headings and bullets for readability."""


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


def build_agents(llm=None):
    llm = llm or get_llm()
    monitoring = _build_agent(llm, MONITORING_PROMPT, [get_current_temperature, get_forecast_temperature,
                                                       get_historical_temperatures, get_env_parameters])
    heat_risk = _build_agent(llm, HEAT_RISK_PROMPT, [get_current_temperature, get_forecast_temperature,
                                                     get_env_parameters, get_heat_exceedance, assess_heat_risk])
    cooling = _build_agent(llm, COOLING_PROMPT, [get_current_temperature, get_historical_temperatures,
                                                 get_heat_exceedance, generate_heatmap, get_env_parameters])
    mitigation = _build_agent(llm, MITIGATION_PROMPT, [get_current_temperature, get_historical_temperatures,
                                                       get_env_parameters, get_heat_intelligence])
    data_agent = _build_agent(llm, DATA_PROMPT, [dataset_summary, explore_dataset])
    delegates = [
        _make_delegate("temperature_monitoring",
                       "Delegate a monitoring task to the Temperature & Environment Monitoring Agent "
                       "(real-time conditions, forecasts, historical comparisons, heat anomalies). Pass a precise request with location.",
                       monitoring),
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
    ]
    manager = create_react_agent(llm, delegates, prompt=MANAGER_PROMPT)
    return manager


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


async def run_chat(user_message, location=None, date=""):
    manager = build_agents()
    context = user_message
    if location and location.get("lat") is not None and location.get("lon") is not None:
        context += f"\n\nLocation context (user provided): latitude={location['lat']}, longitude={location['lon']}."
    if date:
        context += f"\nDate context (user provided): {date}."
    result = await manager.ainvoke(
        {"messages": [HumanMessage(content=context)]}, config={"recursion_limit": 40}
    )
    messages = result["messages"]
    reply = messages[-1].content or "(no textual response)"
    trace = build_trace(messages)
    agents_used = sorted({t["tool"].replace("delegate_to_", "") for t in trace})
    return {"reply": reply, "trace": trace, "agents_used": agents_used}


async def analyze_file(path, file_name):
    llm = get_llm()
    data = await asyncio.to_thread(_analyze_dataset, path)
    summary = _dataset_text(data)
    agent = _build_agent(llm, DATA_PROMPT, [dataset_summary, explore_dataset])
    prompt = (f"Analyze the uploaded file '{file_name}'. Pre-computed summary:\n{summary}\n"
              "Use tools to dig deeper if useful, then give a clear human-readable analysis "
              "with specific numbers, data-quality notes and recommendations.")
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]}, config={"recursion_limit": 20}
    )
    explanation = result["messages"][-1].content or "(no textual response)"
    return {"explanation": explanation, "data": data, "steps": _steps_from(result["messages"])}
