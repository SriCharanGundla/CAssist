from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings


@dataclass(frozen=True)
class ModelSelection:
    provider: Literal["openai", "gemini"]
    model_id: str


def resolve_model_selection(
    settings: Settings,
    provider_override: Literal["openai", "gemini"] | None = None,
    model_override: str | None = None,
) -> ModelSelection:
    if (provider_override or model_override) and not settings.allow_provider_override:
        raise PermissionError("Model-provider overrides are disabled")

    return ModelSelection(
        provider=provider_override or settings.model_provider,
        model_id=model_override or settings.model_id,
    )


# Concrete Strands OpenAIResponsesModel and GeminiModel adapters belong here.
# Both adapters must return the same canonical Pydantic extraction schema.
