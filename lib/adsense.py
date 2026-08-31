"""AdSense gateway.

Serves advertisement rendering configuration. Two sources:

1. Real Google AdSense — active when ADSENSE_CLIENT_ID and ADSENSE_SLOT_ID are
   configured. The frontend embeds the standard AdSense snippet using these values.
2. In-app ads — the fallback used for the ad-credit economy. Ads stored in the local
   `ads` table are surfaced and "views" are tracked in-app (see lib.credits).

Keys are read only from the environment; never hardcoded.
"""
import os

from lib.llm import is_configured


def is_adsense_enabled():
    return is_configured("ADSENSE_CLIENT_ID") and is_configured("ADSENSE_SLOT_ID")


def client_id():
    return os.getenv("ADSENSE_CLIENT_ID", "").strip()


def slot_id():
    return os.getenv("ADSENSE_SLOT_ID", "").strip()


def config():
    """Render-safe ad configuration (no secrets)."""
    enabled = is_adsense_enabled()
    return {
        "provider": "adsense" if enabled else "in-app",
        "client_id": client_id() if enabled else "",
        "slot_id": slot_id() if enabled else "",
        "enabled": enabled,
        "in_app": not enabled,
    }


def status():
    return {
        "enabled": is_adsense_enabled(),
        "client_id_set": bool(client_id()),
        "slot_id_set": bool(slot_id()),
        "provider": "adsense" if is_adsense_enabled() else "in-app",
    }
