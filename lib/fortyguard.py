import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from lib.llm import is_configured

BASE = "https://api.fortyguard.com/v1"


class FortyGuardError(Exception):
    pass


def _headers():
    if not is_configured("FORTYGUARD_API_KEY"):
        raise FortyGuardError(
            "FORTYGUARD_API_KEY is not configured. Add it to the .env file to "
            "enable live climate data."
        )
    return {"api-key": os.getenv("FORTYGUARD_API_KEY", "").strip(), "Content-Type": "application/json"}


def point_aoi(lat, lon, size_deg=0.004):
    d = size_deg / 2
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [lon - d, lat - d],
                            [lon + d, lat - d],
                            [lon + d, lat + d],
                            [lon - d, lat + d],
                            [lon - d, lat - d],
                        ]
                    ],
                },
            }
        ],
    }


def _post(endpoint, payload, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=40) as client:
                resp = client.post(f"{BASE}/{endpoint}", headers=_headers(), json=payload)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise FortyGuardError(f"FortyGuard POST /{endpoint} -> HTTP {resp.status_code}: {resp.text[:400]}")
            body = resp.json()
            if body.get("error"):
                raise FortyGuardError(f"FortyGuard /{endpoint} error: {body.get('message')}")
            return body["data"]["activity_id"]
        except FortyGuardError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise FortyGuardError(f"FortyGuard POST /{endpoint} failed after {retries} attempts: {last_error}")


def _wait(activity_id, timeout=600):
    deadline = time.time() + timeout
    with httpx.Client(timeout=40) as client:
        while time.time() < deadline:
            try:
                resp = client.get(f"{BASE}/status/{activity_id}", headers=_headers())
            except Exception as exc:
                if time.time() >= deadline:
                    raise FortyGuardError(f"FortyGuard status polling failed: {exc}")
                time.sleep(5)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(5)
                continue
            if resp.status_code != 200:
                raise FortyGuardError(f"FortyGuard status -> HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json().get("data", {})
            status = data.get("status")
            if status == "Completed":
                return data.get("result") or {}
            if status == "Failed":
                raise FortyGuardError(f"FortyGuard activity {activity_id} failed: {data.get('message', '')}")
            time.sleep(5)
    raise FortyGuardError(f"FortyGuard activity {activity_id} timed out after {timeout}s")


def _run_async(endpoint, payload, timeout=600):
    return _wait(_post(endpoint, payload), timeout)


def _to_json(value, max_len=8000):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= max_len else text[:max_len] + " ...(truncated)"


def _extract_stats(result):
    for key, val in (result or {}).items():
        if isinstance(val, dict) and key.lower() == "stats_data":
            return val
    return {}


def _temp_stats(stats):
    for key, val in (stats or {}).items():
        if isinstance(val, dict) and "temp" in key.lower():
            return {k.lower(): v for k, v in val.items()}
    return {}


def _date_time(filter_type, start_date, start_time="", end_date="", end_time=""):
    payload = {"start_date": start_date, "filter_type": filter_type}
    if start_time:
        payload["start_time"] = start_time
    if end_date:
        payload["end_date"] = end_date
    if end_time:
        payload["end_time"] = end_time
    return payload


def current_temperature(lat, lon):
    now = datetime.now(timezone.utc)
    payload = {
        "polygon_aoi": point_aoi(lat, lon),
        "date_time": _date_time(1, now.date().isoformat(), now.strftime("%H:%M")),
        "granularity": 100,
    }
    result = _run_async("heatmap", payload)
    return _to_json(
        {
            "location": {"lat": lat, "lon": lon},
            "datetime_utc": now.isoformat(),
            "temperature_stats": _temp_stats(_extract_stats(result)),
            "source": "FortyGuard heatmap snapshot (tcm)",
        }
    )


def forecast_temperature(lat, lon, hours_ahead=6):
    if not 0 < hours_ahead <= 12:
        hours_ahead = max(1, min(12, hours_ahead))
    when = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    payload = {
        "polygon_aoi": point_aoi(lat, lon),
        "date_time": _date_time(1, when.date().isoformat(), when.strftime("%H:%M")),
        "granularity": 100,
    }
    result = _run_async("heatmap", payload)
    return _to_json(
        {
            "location": {"lat": lat, "lon": lon},
            "forecast_datetime_utc": when.isoformat(),
            "hours_ahead": hours_ahead,
            "temperature_stats": _temp_stats(_extract_stats(result)),
            "source": "FortyGuard forecast heatmap (tcm)",
        }
    )


def historical_temperatures(lat, lon, start_date, end_date=""):
    filt = 4 if end_date else 3
    payload = {
        "polygon_aoi": point_aoi(lat, lon),
        "date_time": _date_time(filt, start_date, end_date=end_date),
        "granularity": 100,
    }
    result = _run_async("heatmap", payload)
    return _to_json(
        {
            "location": {"lat": lat, "lon": lon},
            "start_date": start_date,
            "end_date": end_date or start_date,
            "temperature_stats": _temp_stats(_extract_stats(result)),
            "source": "FortyGuard historical heatmap (tcm)",
        }
    )


def heat_exceedance(lat, lon, start_date, end_date="", threshold=30.0, direction="above"):
    filt = 4 if end_date else 3
    payload = {
        "polygon_aoi": point_aoi(lat, lon),
        "date_time": _date_time(filt, start_date, end_date=end_date),
        "granularity": 100,
        "analytic_type": "exceedance",
        "threshold": float(threshold),
        "direction": direction if direction in ("above", "below") else "above",
    }
    result = _run_async("heatmap", payload)
    return _to_json(
        {
            "location": {"lat": lat, "lon": lon},
            "window": {"start_date": start_date, "end_date": end_date or start_date},
            "threshold_c": float(threshold),
            "direction": direction,
            "stats_data": _extract_stats(result),
            "source": "FortyGuard exceedance heatmap (hours above/below threshold)",
        }
    )


def env_params(lat, lon, temperature, start_date="", start_time=""):
    now = datetime.now(timezone.utc)
    if not start_date:
        start_date = now.date().isoformat()
    if not start_time:
        start_time = now.strftime("%H:%M")
    payload = {
        "latitude": float(lat),
        "longitude": float(lon),
        "temperature": float(temperature),
        "date_time": _date_time(1, start_date, start_time),
    }
    result = _run_async("env_params", payload)
    return _to_json(
        {
            "location": {"lat": lat, "lon": lon},
            "input_temperature_c": float(temperature),
            "environmental_parameters": result,
            "source": "FortyGuard environmental parameters",
        }
    )


def heat_intelligence(lat, lon, temperature, date, analysis="geographic,environmental,urban"):
    allowed = {"geographic", "environmental", "urban", "events", "anthropogenic"}
    cats = [c.strip() for c in analysis.split(",") if c.strip() in allowed] or ["environmental"]
    payload = {
        "latitude": float(lat),
        "longitude": float(lon),
        "temperature": float(temperature),
        "date": date,
        "analysis": cats,
    }
    result = _run_async("heat_intelligence", payload, timeout=1800)
    download_link = (result or {}).get("download_link", "")
    if download_link:
        try:
            with httpx.Client(timeout=90) as client:
                resp = client.get(download_link)
        except Exception as exc:
            return _to_json({"report_error": str(exc), "categories": cats, "source": "FortyGuard heat intelligence"})
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type or resp.text.strip().startswith("{"):
            try:
                report = resp.json()
            except Exception:
                report = resp.text[:6000]
            return _to_json({"report": report, "categories": cats, "source": "FortyGuard heat intelligence report"})
        return _to_json(
            {
                "report_type": "document",
                "content_type": content_type,
                "size_bytes": len(resp.content),
                "note": "Full report downloaded as a document; summary limited.",
                "categories": cats,
            }
        )
    return _to_json({"report": result, "categories": cats, "source": "FortyGuard heat intelligence"})


def generate_heatmap(lat, lon, start_date, end_date="", analytic_type="tcm",
                     threshold=30.0, direction="above", granularity=100):
    filt = 4 if end_date else 3
    payload = {
        "polygon_aoi": point_aoi(lat, lon, size_deg=0.05),
        "date_time": _date_time(filt, start_date, end_date=end_date),
        "granularity": int(granularity) if granularity in (60, 80, 100) else 100,
    }
    if analytic_type in ("exceedance", "persistence"):
        payload["analytic_type"] = analytic_type
        payload["threshold"] = float(threshold)
        payload["direction"] = direction if direction in ("above", "below") else "above"
    elif analytic_type == "time_of_measure":
        payload["analytic_type"] = analytic_type
    result = _run_async("heatmap", payload)
    stats = _extract_stats(result)
    return _to_json(
        {
            "window": {"start_date": start_date, "end_date": end_date or start_date},
            "analytic_type": analytic_type,
            "stats_data": stats,
            "map_data_present": bool((result or {}).get("map_data")),
            "source": "FortyGuard heatmap over local area",
        }
    )
