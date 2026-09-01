import json
import os
import re

import numpy as np
import pandas as pd
from langchain_core.tools import tool

from lib import db, fortyguard as fg


def _float_env(name, default):
    value = os.getenv(name, "").strip()
    return float(value) if value else default


RISK_THRESHOLDS = {
    "low": _float_env("HEAT_RISK_LOW", 27.0),
    "moderate": _float_env("HEAT_RISK_MODERATE", 31.0),
    "high": _float_env("HEAT_RISK_HIGH", 35.0),
    "extreme": _float_env("HEAT_RISK_EXTREME", 40.0),
}


def effective_thresholds():
    """Risk thresholds with admin-configured overrides applied on top of env defaults."""
    merged = dict(RISK_THRESHOLDS)
    overrides = None
    try:
        overrides = db.get_setting("risk_thresholds")
        if overrides:
            overrides = json.loads(overrides)
    except Exception:
        overrides = None
    if isinstance(overrides, dict):
        for key in merged:
            try:
                merged[key] = float(overrides[key])
            except (KeyError, TypeError, ValueError):
                pass
    return merged


def heat_index(temperature_c, humidity):
    t = float(temperature_c)
    rh = float(humidity)
    if t < 27 or rh < 40:
        return t
    return -8.784695 + 1.61139411 * t + 2.338549 * rh - 0.14611605 * t * rh \
        - 1.2308094e-2 * t * t - 1.6424828e-2 * rh * rh \
        + 2.211732e-3 * t * t * rh + 7.2546e-4 * t * rh * rh \
        - 3.582e-6 * t * t * rh * rh


@tool
def assess_heat_risk(temperature_c: float, humidity: float = None) -> str:
    """Classify heat risk for a temperature (and optional humidity %). Returns risk level,
    a 0-100 score and recommended public-safety actions. Thresholds are administrator-configurable."""
    rh = humidity if humidity is not None else 0.0
    feels = heat_index(temperature_c, rh)
    thresholds = effective_thresholds()
    levels = [("extreme", thresholds["extreme"]), ("high", thresholds["high"]),
              ("moderate", thresholds["moderate"]), ("low", thresholds["low"])]
    level = "low"
    for name, th in levels:
        if feels >= th:
            level = name
            break
    bounds = {
        "low": (0, thresholds["low"]),
        "moderate": (thresholds["low"], thresholds["moderate"]),
        "high": (thresholds["moderate"], thresholds["high"]),
        "extreme": (thresholds["high"], thresholds["extreme"] + 25),
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
            "thresholds_c": thresholds,
        },
        ensure_ascii=False,
    )


def _run_safe(fn, *args, _tool_name=""):
    """Run a FortyGuard data fetch and, when the Google Gemini gateway is configured,
    pass the returned FortyGuard payload to Gemini for a plain-language interpretation.
    This implements the 'FortyGuard response -> Google LLM' flow. Falls back to the
    raw payload (or a clear error) when Gemini is not available."""
    try:
        raw = fn(*args)
    except fg.FortyGuardError as exc:
        return f"FortyGuard error: {exc}"
    except Exception as exc:
        return f"Tool error: {exc}"
    if not isinstance(raw, str):
        return raw
    try:
        from lib import google_gateway as gg
    except Exception:
        return raw
    if not gg.is_enabled():
        return raw
    try:
        interpretation = gg.interpret_fortyguard(raw, question=_tool_name)
    except Exception:
        return raw
    return f"FortyGuard data:\n{raw}\n\nGoogle Gemini interpretation:\n{interpretation}"


@tool
def get_temperature_snapshot(latitude: float, longitude: float) -> str:
    """Fetch the current (near-real-time) air temperature snapshot for a location via FortyGuard."""
    return _run_safe(fg.current_temperature, latitude, longitude)


@tool
def get_temperature_forecast(latitude: float, longitude: float, hours_ahead: int = 6) -> str:
    """Fetch a temperature forecast for a location, up to 12 hours ahead (FortyGuard limit)."""
    return _run_safe(fg.forecast_temperature, latitude, longitude, hours_ahead)


@tool
def get_temperature_history(latitude: float, longitude: float, start_date: str, end_date: str = "") -> str:
    """Fetch historical temperature statistics for a date range (YYYY-MM-DD). Up to 1 month."""
    return _run_safe(fg.historical_temperatures, latitude, longitude, start_date, end_date)


@tool
def get_heat_stress(latitude: float, longitude: float, start_date: str, end_date: str = "",
                    threshold: float = 30.0, direction: str = "above") -> str:
    """Hours above (or below) a temperature threshold over a date range - heat-stress analysis."""
    return _run_safe(fg.heat_exceedance, latitude, longitude, start_date, end_date, threshold, direction)


@tool
def get_environmental_parameters(latitude: float, longitude: float, temperature: float,
                                 date: str = "", time: str = "") -> str:
    """Environmental parameters for a location: heat index, wet-bulb temperature, humidity, AQI, CO2, methane, solar irradiance."""
    return _run_safe(fg.env_params, latitude, longitude, temperature, date, time)


@tool
def get_heat_intelligence(latitude: float, longitude: float, temperature: float,
                          date: str, categories: str = "geographic,environmental,urban") -> str:
    """Multi-dimensional Heat Intelligence Report. categories: geographic, environmental, urban, events, anthropogenic."""
    return _run_safe(fg.heat_intelligence, latitude, longitude, temperature, date, categories)


@tool
def get_temperature_heatmap(latitude: float, longitude: float, start_date: str, end_date: str = "",
                            analytic_type: str = "tcm", threshold: float = 30.0,
                            direction: str = "above", granularity: int = 100) -> str:
    """Generate a heatmap over the local area around a point. analytic_type: tcm (temperature),
    exceedance (hours over threshold), persistence (longest continuous run), time_of_measure."""
    return _run_safe(fg.generate_heatmap, latitude, longitude, start_date, end_date, analytic_type,
                     threshold, direction, granularity)


@tool
def knowledge_base(query: str) -> str:
    """Search the built-in, curated climate & heat knowledge base (sourced from OSHA, NIOSH, EPA, DOE, NOAA, ACGIH)."""
    rows = db.run("SELECT topic, content, source FROM knowledge")
    q_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    for row in rows:
        text = f"{row['topic']} {row['content']}".lower()
        score = sum(1 for w in q_words if w in text) if q_words else 0
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = scored[:4]
    if not results:
        return "No matching entries in the built-in knowledge base. Do not invent facts; state that external sources are needed."
    return json.dumps(
        [{"topic": r[1]["topic"], "content": r[1]["content"], "source": r[1]["source"]} for r in results],
        ensure_ascii=False,
    )


def _read_table(path, data=None):
    ext = path.lower().rsplit(".", 1)[-1]
    if data is not None:
        import io
        if ext == "csv":
            try:
                df = pd.read_csv(io.BytesIO(data))
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(data), encoding="latin-1")
        elif ext in ("xlsx", "xls"):
            df = pd.read_excel(io.BytesIO(data))
        elif ext == "json":
            df = pd.read_json(io.BytesIO(data))
        else:
            raise ValueError("Unsupported file type. Upload a CSV, Excel, or JSON file.")
    elif ext == "csv":
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


def analyze_dataset(path, data=None):
    df = _read_table(path, data)
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


def dataset_text(data):
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
    return _run_safe(lambda p: dataset_text(analyze_dataset(p)), file_path)


@tool
def explore_dataset(file_path: str) -> str:
    """Full statistical analysis of an uploaded dataset: stats, correlations, histograms, time series, anomalies, categorical counts."""
    return _run_safe(
        lambda p: json.dumps(analyze_dataset(p), ensure_ascii=False, default=str)[:8000], file_path
    )
