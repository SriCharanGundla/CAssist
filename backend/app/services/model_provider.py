import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from strands import Agent
from strands.models.gemini import GeminiModel
from strands.models.model import Model
from strands.models.openai_responses import OpenAIResponsesModel
from strands.multiagent import GraphBuilder

from app.core.config import Settings
from app.schemas.extraction import (
    CanonicalInvoice,
    DocumentClassification,
    ExtractionCompletion,
    FieldEvidence,
)
from app.services.extraction_tools import InvoiceExtractionWorkspace

_CLASSIFIER_PROMPT = """Classify the supplied accounting document by visible content only.
Use tax_invoice only when the document visibly identifies itself as a tax invoice or contains
clear GST tax-invoice characteristics. Use invoice for other invoices. Do not infer a type from
filename."""

_EXTRACTION_PROMPT = """Extract the supplied invoice for human accounting review.
Use the document tools to record only facts visibly supported by a supplied page. Never invent,
default, calculate, or copy a value merely because the contract supports it. Use inspect_page only
when a targeted reinspection is useful. Record line items with record_table. Call validate_draft
after recording. If validation reports a problem that targeted page evidence can resolve, perform at
most one repair pass and call validate_draft once more. Use replace_existing=true when repairing a
line-item table. Then return validated=true. Missing optional fields are normal. Monetary values,
quantities, rates, and prices must be base-10 strings without currency symbols or grouping
separators. Use ISO dates only when unambiguous."""


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
    evidence: list[FieldEvidence] = field(default_factory=list)


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
    def __init__(self, model: Model, node_timeout_seconds: float) -> None:
        self._configure_content_safe_dependency_logging()
        self.model = model
        self.node_timeout_seconds = node_timeout_seconds

    @staticmethod
    def _configure_content_safe_dependency_logging() -> None:
        for logger_name in ("strands", "google.genai"):
            dependency_logger = logging.getLogger(logger_name)
            dependency_logger.handlers.clear()
            dependency_logger.addHandler(logging.NullHandler())
            dependency_logger.propagate = False
            dependency_logger.setLevel(logging.CRITICAL + 1)

    def _build_graph(self, workspace: InvoiceExtractionWorkspace):
        classifier = Agent(
            model=self.model,
            name="document_classifier",
            system_prompt=_CLASSIFIER_PROMPT,
            structured_output_model=DocumentClassification,
            callback_handler=None,
            tools=[],
            retry_strategy=None,
        )
        extractor = Agent(
            model=self.model,
            name="invoice_extractor",
            system_prompt=_EXTRACTION_PROMPT,
            structured_output_model=ExtractionCompletion,
            callback_handler=None,
            tools=[
                workspace.inspect_page,
                workspace.record_field,
                workspace.record_table,
                workspace.validate_draft,
            ],
            retry_strategy=None,
        )
        graph = GraphBuilder()
        graph.add_node(classifier, "classify")
        graph.add_node(extractor, "extract")
        graph.add_edge("classify", "extract")
        return (
            graph.set_entry_point("classify")
            .set_max_node_executions(2)
            .set_node_timeout(self.node_timeout_seconds)
            .set_execution_timeout(self.node_timeout_seconds * 2)
            .set_graph_id("cassist_document_extraction")
            .build()
        )

    def extract_invoice(self, page_paths: Sequence[Path]) -> ProviderExtraction:
        if not page_paths:
            raise ProviderExtractionError("No preprocessed pages were supplied")

        workspace = InvoiceExtractionWorkspace(tuple(page_paths))
        graph = self._build_graph(workspace)
        content: list[dict[str, object]] = [
            {
                "text": (
                    "Classify and extract this accounting document across all supplied pages. "
                    "Page images follow in 1-based order."
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
            graph_result = graph(content)
            classification_result = graph_result.results["classify"].result
            extraction_result = graph_result.results["extract"].result
            classification = classification_result.structured_output
            completion = extraction_result.structured_output
            if not isinstance(classification, DocumentClassification):
                raise ProviderExtractionError("The classification agent returned no classification")
            if classification.document_type not in {"invoice", "tax_invoice"}:
                raise ProviderExtractionError(
                    "The first extraction contract supports invoices only"
                )
            if not isinstance(completion, ExtractionCompletion) or not completion.validated:
                raise ProviderExtractionError("The extraction agent did not complete validation")
            invoice, evidence = workspace.result(classification.document_type)
        except ProviderExtractionError:
            raise
        except Exception as exc:
            raise ProviderExtractionError("The agentic document workflow failed") from exc

        usage = graph_result.accumulated_usage
        return ProviderExtraction(
            invoice=invoice,
            evidence=evidence,
            raw_provider_output={
                "classification": classification.model_dump(mode="json"),
                "classification_stop_reason": classification_result.stop_reason,
                "extraction_stop_reason": extraction_result.stop_reason,
                "graph_execution_order": [node.node_id for node in graph_result.execution_order],
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
        model: Model = GeminiModel(
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

    node_timeout = (settings.worker_lease_seconds - 30) / 2
    return StrandsExtractionProvider(model=model, node_timeout_seconds=node_timeout)
