import os

DEFAULT_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")


class AgentConfigError(Exception):
    pass


PLACEHOLDERS = {"", "your-llm-api-key", "your-fortyguard-api-key", "your-langsmith-api-key",
                "your-api-key", "changeme", "your-key", "key"}


def is_configured(name):
    value = os.getenv(name, "").strip()
    return bool(value) and value.lower() not in PLACEHOLDERS


def get_llm(temperature=0.2):
    if not is_configured("GOOGLE_API_KEY") and not is_configured("LLM_API_KEY"):
        raise AgentConfigError(
            "No LLM configured. Set GOOGLE_API_KEY (Gemini) or LLM_API_KEY "
            "in the .env file to enable the agent brain."
        )
    # Preferred: use the single Google API key with Gemini via LangChain.
    if is_configured("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from lib.google_gateway import model_name
            return ChatGoogleGenerativeAI(
                model=model_name(), api_key=os.getenv("GOOGLE_API_KEY", "").strip(),
                temperature=temperature, convert_system_message_to_human=True,
            )
        except Exception as exc:
            raise AgentConfigError(f"Google Gemini LLM failed to initialize: {exc}")

    # Fallback: any OpenAI-compatible provider.
    from langchain_openai import ChatOpenAI
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    return ChatOpenAI(model=DEFAULT_MODEL, api_key=os.getenv("LLM_API_KEY", "").strip(),
                      base_url=base_url, temperature=temperature)
