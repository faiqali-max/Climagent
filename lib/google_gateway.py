"""Parallel Google Gemini (LLM) gateway.

This is an OPTIONAL, parallel LLM path. When GOOGLE_API_KEY is configured it can be
used to interpret climate data, including the "FortyGuard -> Google LLM" flow where a
result returned by the FortyGuard API is passed to Google's Gemini for interpretation.

The module degrades gracefully:
  - If GOOGLE_API_KEY is not set, generate() raises GeminiNotConfigured and callers can
    fall back to the default LangChain brain.
  - If the google-genai / google-generativeai package is not installed, a clear error is
    raised so callers can surface a useful message instead of crashing.

Keys are read strictly from the environment (.env); never hardcoded.
"""
import json
import os

from lib.llm import is_configured

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiNotConfigured(Exception):
    pass


class GeminiError(Exception):
    pass


def is_enabled():
    return is_configured("GOOGLE_API_KEY")


def model_name():
    return os.getenv("GOOGLE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _client():
    if not is_enabled():
        raise GeminiNotConfigured(
            "GOOGLE_API_KEY is not configured. Add it to the .env file to enable the "
            "Google Gemini gateway."
        )
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiError(
            "The 'google-genai' package is not installed. Run: pip install google-genai"
        ) from exc
    return genai.Client(api_key=api_key)


def generate(prompt: str, temperature: float = 0.2) -> str:
    """Send a prompt to Gemini and return the generated text."""
    try:
        client = _client()
    except GeminiNotConfigured:
        raise
    except GeminiError:
        raise
    try:
        response = client.models.generate_content(
            model=model_name(),
            contents=prompt,
            config={"temperature": temperature},
        )
    except Exception as exc:
        raise GeminiError(f"Gemini request failed: {exc}") from exc
    text = getattr(response, "text", None)
    if not text:
        # Some SDK versions return candidates; recover the text if present.
        try:
            text = response.candidates[0].content.parts[0].text
        except (IndexError, AttributeError, KeyError):
            text = ""
    return text or "(empty response from Gemini)"


def json_response(prompt: str) -> dict:
    """Ask Gemini for a JSON object and return the parsed dict (best-effort)."""
    text = generate(prompt)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def interpret_fortyguard(fg_payload, question="", lat=None, lon=None):
    """The 'FortyGuard -> Google LLM' flow.

    Takes a result already returned by the FortyGuard API (fg_payload) and asks
    Google Gemini to interpret it into a clear, human-readable climate assessment.
    """
    prompt = (
        "You are Climagent's climate AI. A result has just come back from the "
        "FortyGuard climate-data API. Interpret it for a non-technical user.\n"
    )
    if lat is not None and lon is not None:
        prompt += f"\nLocation: latitude={lat}, longitude={lon}.\n"
    if question:
        prompt += f"\nUser question: {question}\n"
    prompt += (
        "\nFortyGuard payload (JSON):\n"
        + json.dumps(fg_payload, ensure_ascii=False, default=str)[:8000]
        + "\n\n"
        "Please give a concise plain-language analysis: what the data shows, the "
        "implied risk/comfort level, and practical recommendations. Use Celsius. "
        "If the data is missing or ambiguous, say so rather than inventing numbers."
    )
    return generate(prompt)
