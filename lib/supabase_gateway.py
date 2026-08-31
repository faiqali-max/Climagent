"""Optional Supabase gateway.

This module mirrors the existing "placeholder" approach in .env: Supabase is only
active when SUPABASE_URL and a Supabase key are configured. The application continues
to run entirely on local SQLite when Supabase is not configured, so this gateway is a
non-breaking bridge toward a future Supabase-backed deployment.

It uses the official `supabase` python client if installed; otherwise the gateway
reports itself as unavailable and the rest of the app works as before.
"""
import os

from lib.llm import is_configured


class SupabaseGatewayError(Exception):
    pass


def is_enabled():
    return is_configured("SUPABASE_URL") and (
        is_configured("SUPABASE_SERVICE_ROLE_KEY") or is_configured("SUPABASE_ANON_KEY")
    )


def _client():
    if not is_enabled():
        raise SupabaseGatewayError(
            "Supabase gateway is not configured. Set SUPABASE_URL and a "
            "SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) in .env."
        )
    try:
        from supabase import create_client
    except ImportError as exc:
        raise SupabaseGatewayError(
            "The 'supabase' python package is not installed. "
            "Run: pip install supabase"
        ) from exc
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()
    return create_client(url, key)


def status():
    """Return a safe, key-free status report used by /api/config and /health."""
    return {
        "enabled": is_enabled(),
        "url_set": bool(os.getenv("SUPABASE_URL", "").strip()),
        "note": "Optional gateway: persists on local SQLite until Supabase is configured.",
    }


def client():
    """Return an authenticated Supabase client, or raise SupabaseGatewayError."""
    return _client()
