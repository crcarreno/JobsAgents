from app.providers.base import AIProvider, AIResponse, AIUsageRecord, TaskContext
from app.providers.opencode_provider import OpenCodeGoProvider


def build_ai_provider(config, settings, budget) -> AIProvider:
    provider = str(settings.get("ai.provider", "opencode_go")).lower()
    if provider in {"opencode_go", "opencode", "mixed"}:
        return OpenCodeGoProvider(config, settings, budget)
    raise ValueError(f"Unsupported AI provider: {provider}")


__all__ = ["AIProvider", "AIResponse", "AIUsageRecord", "OpenCodeGoProvider", "TaskContext", "build_ai_provider"]
