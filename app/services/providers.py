"""Provider selection shared by ingestion, retrieval, generation, and migration."""
from flask import current_app


def provider(provider_name=None, deadline=None):
    name = (provider_name or current_app.config.get("AI_PROVIDER", "gemini")).casefold()
    if name == "gemini":
        from .gemini import Gemini
    elif name == "openai":
        from .openai_provider import OpenAIProvider as Gemini
    else:
        raise ValueError("AI_PROVIDER must be openai or gemini")
    factory = current_app.config.get(f"{name.upper()}_FACTORY", Gemini)
    return factory(deadline=deadline)
