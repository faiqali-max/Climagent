"""Optional Payoneer payout gateway.

Payoneer is used to pay out earnings. This module provides a minimal OAuth 2.0
connectivity layer: token acquisition from the Payoneer API plus a status report.
No live disbursements are performed without real client/secret/base_url keys.

Credentials are read strictly from the environment (.env) and are never hardcoded.
"""
import os

import requests

from lib.llm import is_configured


class PayoneerError(Exception):
    pass


def is_enabled():
    return (
        is_configured("PAYONEER_CLIENT_ID")
        and is_configured("PAYONEER_CLIENT_SECRET")
        and is_configured("PAYONEER_BASE_URL")
    )


def _token():
    if not is_enabled():
        raise PayoneerError(
            "Payoneer gateway is not configured. Set PAYONEER_CLIENT_ID, "
            "PAYONEER_CLIENT_SECRET and PAYONEER_BASE_URL in .env."
        )
    base = os.getenv("PAYONEER_BASE_URL", "").strip().rstrip("/")
    token_url = f"{base}/v4/oauth2/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": os.getenv("PAYONEER_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("PAYONEER_CLIENT_SECRET", "").strip(),
    }
    try:
        response = requests.post(
            token_url, data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PayoneerError(f"Payoneer token request failed: {exc}") from exc
    data = response.json()
    if not data.get("access_token"):
        raise PayoneerError("Payoneer did not return an access token.")
    # Never return the full token publicly; tests only need a boolean / safe metadata.
    return data


def status():
    """Safe, non-secret Payoneer status used by /api/config and /health."""
    base = os.getenv("PAYONEER_BASE_URL", "").strip()
    return {
        "enabled": is_enabled(),
        "base_url_set": bool(base),
        "credentials_set": is_configured("PAYONEER_CLIENT_ID") and is_configured("PAYONEER_CLIENT_SECRET"),
        "mode": "sandbox" if "sandbox" in base.lower() else ("production" if base else "not-configured"),
    }


def connectivity_check():
    """Try to obtain a token and report whether Payoneer is reachable."""
    if not is_enabled():
        return {"enabled": False, "reachable": False, "detail": "Not configured."}
    try:
        _token()
        return {"enabled": True, "reachable": True, "detail": "Connected."}
    except PayoneerError as exc:
        return {"enabled": True, "reachable": False, "detail": str(exc)}
