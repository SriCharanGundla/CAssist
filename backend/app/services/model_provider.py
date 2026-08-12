from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from strands import Agent
from strands.models.gemini import GeminiModel
from strands.models.openai_responses import OpenAIResponsesModel

from app.core.config import Settings
from app.schemas.extraction import CanonicalInvoice

_SYSTEM_PROMPT = """You extract Indian tax invoices for human accounting review.
Return only facts visible in the supplied page images. Never infer missing identifiers or amounts.
Use ISO dates (YYYY-MM-DD) when the date is unambiguous. Every monetary, quantity, price, and rate
value must be a base-10 decimal string without currency symbols or grouping separators. Preserve
invoice text in names, descriptions, addresses, units, and notes. Use null for absent scalar fields
and empty lists for absent collections. This output is not accounting-ready until human review."""


@dataclass(frozen=True)
class ModelSelection:
    provider: Literal["openai", "gemini"]
    model_id: str


@dataclass(frozen=True)
class ProviderExtraction:
    invoice: CanonicalInvoice
    raw_provider_output: dict[str, object]
    input_tokens: int
    output_tokens: int


class ExtractionProvider(Protocol):
    def extract_invoice(self, page_paths: Sequence[Path]) -> ProviderExtraction: ...


class ProviderConfigurationError(Exception):
    pass


class ProviderExtractionError(Exception):
    pass


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


class StrandsExtractionProvider:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    def extract_invoice(self, page_paths: Sequence[Path]) -> ProviderExtraction:
        if not page_paths:
            raise ProviderExtractionError("No preprocessed pages were supplied")

        content: list[dict[str, object]] = [
            {
                "text": (
                    "Extract this invoice across all supplied pages. Preserve line-item order and "
                    "use source_pages to identify the 1-based pages supporting each line item."
                )
            }
        ]
        for page_path in page_paths:
            content.append(
                {
                    "image": {
                        "format": "png",
                        "source": {"bytes": page_path.read_bytes()},
                    }
                }
            )

        try:
            result = self.agent(content, structured_output_model=CanonicalInvoice)
        except Exception as exc:
            raise ProviderExtractionError("The model provider request failed") from exc
        if not isinstance(result.structured_output, CanonicalInvoice):
            raise ProviderExtractionError("The model provider returned no structured invoice")

        usage = result.metrics.accumulated_usage
        return ProviderExtraction(
            invoice=result.structured_output,
            raw_provider_output={
                "message": result.message,
                "stop_reason": result.stop_reason,
            },
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
        )


def create_extraction_provider(
    settings: Settings,
    selection: ModelSelection,
) -> ExtractionProvider:
    if selection.provider == "gemini":
        if settings.app_env == "production":
            raise ProviderConfigurationError("Gemini is unavailable in production")
        if not settings.gemini_api_key:
            raise ProviderConfigurationError("Gemini API key is not configured")
        model = GeminiModel(
            client_args={
                "api_key": settings.gemini_api_key,
                "http_options": {
                    "timeout": settings.provider_timeout_seconds * 1000,
                    "retry_options": {"attempts": settings.provider_max_retries + 1},
                },
            },
            model_id=selection.model_id,
        )
    else:
        if not settings.openai_api_key:
            raise ProviderConfigurationError("OpenAI API key is not configured")
        model = OpenAIResponsesModel(
            client_args={
                "api_key": settings.openai_api_key,
                "timeout": settings.provider_timeout_seconds,
                "max_retries": settings.provider_max_retries,
            },
            model_id=selection.model_id,
            stateful=False,
        )

    return StrandsExtractionProvider(
        Agent(
            model=model,
            system_prompt=_SYSTEM_PROMPT,
            callback_handler=None,
            tools=[],
            retry_strategy=None,
        )
    )
