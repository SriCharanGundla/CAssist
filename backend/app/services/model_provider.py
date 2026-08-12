import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from strands import Agent
from strands.hooks import BeforeNodeCallEvent
from strands.models.gemini import GeminiModel
from strands.models.model import Model
from strands.models.openai_responses import OpenAIResponsesModel
from strands.multiagent import GraphBuilder
from strands.types.exceptions import ModelThrottledException

from app.core.config import Settings
from app.models import ProcessingStage
from app.schemas.extraction import (
    DocumentClassification,
    DocumentPresentation,
    GenericDocumentExtraction,
    GenericExtractionDraft,
    PresentationDraft,
    QualityIssue,
    QualityReviewDraft,
)
from app.services.document_text_tools import DocumentTextTools
from app.services.generic_extraction import (
    finalize_extraction,
    finalize_presentation,
    needs_quality_review,
    remove_non_accounting_boilerplate,
)

_CLASSIFIER_PROMPT = """Classify the supplied accounting document by visible content only.
Choose the closest broad type. Classification is descriptive metadata and must not impose an
extraction template. Do not infer a type from the filename."""

_EXTRACTION_PROMPT = """Extract the visible information from this document for human review.
Return only label/value pairs actually present, preserving each visible label and value as written.
Preserve table titles, headers, cells, and row order. Put useful unlabelled narrative content in
text_blocks. Exclude page furniture and non-accounting boilerplate: document title repetitions,
page numbers, repeated page headers, continuation labels, signature disclaimers, synthetic-test
notices, and other processing-only annotations. Preserve terms or notices that affect payment, tax,
liability, or the accounting meaning of the document. Do not
invent required fields, normalize dates, calculate amounts, correct spelling, rename labels, or
apply an invoice schema. Represent line breaks as actual newlines, never literal backslash-n text.
Page images are the primary source. The native PDF text tools are optional supporting evidence when
text is available. Set quality_review_recommended only when the source is genuinely ambiguous,
illegible, or likely misread. Do not duplicate observations."""

_ORGANIZER_PROMPT = """Organize the preceding generic extraction for a Chartered Accountant's
review without changing any extracted content. Return short ordered section titles and references
to every extracted top-level observation using only /fields/N, /tables/N, or /text_blocks/N.
Prefer visible document headings. When headings are absent, use concise financial-document labels
such as Invoice details, Supplier, Taxes and totals, Payment details, Transactions, or Terms only
when supported by the observations. Do not create, rename, correct, summarize, or omit values."""

_QUALITY_PROMPT = """Review the preceding generic extraction against the supplied document.
Only flag likely gibberish, OCR-like character confusion, duplicate observations, or illegible
text. Never rewrite, remove, or add extracted fields, table cells, or text blocks. Return issues
separately using the extraction target path. A suggested_value is optional and must be supported by
visible or native-text evidence; otherwise leave it null. The human decides whether to accept it."""


@dataclass(frozen=True)
class ModelSelection:
    provider: Literal["openai", "gemini"]
    model_id: str


@dataclass(frozen=True)
class ProviderExtraction:
    document: GenericDocumentExtraction
    raw_provider_output: dict[str, object]
    input_tokens: int
    output_tokens: int
    presentation: DocumentPresentation = field(default_factory=DocumentPresentation)
    quality_issues: list[QualityIssue] = field(default_factory=list)


class ExtractionProvider(Protocol):
    def extract_document(
        self,
        page_paths: Sequence[Path],
        page_text: Sequence[str | None],
        on_stage: Callable[[ProcessingStage], None] | None = None,
    ) -> ProviderExtraction: ...

    def cancel(self) -> None: ...


class ProviderConfigurationError(Exception):
    pass


class ProviderExtractionError(Exception):
    pass


class ProviderCancellationError(ProviderExtractionError):
    pass


class ProviderRateLimitError(ProviderExtractionError):
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
        self._cancel_requested = threading.Event()
        self._agents_lock = threading.Lock()
        self._active_agents: tuple[Agent, ...] = ()

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._agents_lock:
            agents = self._active_agents
        for agent in agents:
            agent.cancel()

    @staticmethod
    def _configure_content_safe_dependency_logging() -> None:
        for logger_name in ("strands", "google.genai"):
            dependency_logger = logging.getLogger(logger_name)
            dependency_logger.handlers.clear()
            dependency_logger.addHandler(logging.NullHandler())
            dependency_logger.propagate = False
            dependency_logger.setLevel(logging.CRITICAL + 1)

    def _build_graph(
        self,
        text_tools: DocumentTextTools,
        on_stage: Callable[[ProcessingStage], None] | None = None,
    ):
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
            name="generic_document_extractor",
            system_prompt=_EXTRACTION_PROMPT,
            structured_output_model=GenericExtractionDraft,
            callback_handler=None,
            tools=[text_tools.read_document_text, text_tools.search_document_text],
            retry_strategy=None,
        )
        organizer = Agent(
            model=self.model,
            name="document_presentation_organizer",
            system_prompt=_ORGANIZER_PROMPT,
            structured_output_model=PresentationDraft,
            callback_handler=None,
            tools=[],
            retry_strategy=None,
        )
        quality_reviewer = Agent(
            model=self.model,
            name="extraction_quality_reviewer",
            system_prompt=_QUALITY_PROMPT,
            structured_output_model=QualityReviewDraft,
            callback_handler=None,
            tools=[text_tools.read_document_text, text_tools.search_document_text],
            retry_strategy=None,
        )
        with self._agents_lock:
            self._active_agents = (
                classifier,
                extractor,
                organizer,
                quality_reviewer,
            )

        def should_review(state) -> bool:
            node = state.results.get("extract")
            draft = node.result.structured_output if node else None
            return isinstance(draft, GenericExtractionDraft) and needs_quality_review(draft)

        graph = GraphBuilder()
        graph.add_node(classifier, "classify")
        graph.add_node(extractor, "extract")
        graph.add_node(organizer, "organize")
        graph.add_node(quality_reviewer, "quality")
        graph.add_edge("classify", "extract")
        graph.add_edge("extract", "organize")
        graph.add_edge("extract", "quality", condition=should_review)
        graph.add_edge("organize", "quality", condition=should_review)
        built_graph = (
            graph.set_entry_point("classify")
            .set_max_node_executions(4)
            .set_node_timeout(self.node_timeout_seconds)
            .set_execution_timeout(self.node_timeout_seconds * 4)
            .set_graph_id("cassist_generic_document_extraction")
            .build()
        )
        node_stages = {
            "classify": ProcessingStage.CLASSIFYING,
            "extract": ProcessingStage.EXTRACTING,
            "organize": ProcessingStage.ORGANIZING,
            "quality": ProcessingStage.QUALITY_CHECK,
        }

        def report_node_stage(event: BeforeNodeCallEvent) -> None:
            if self._cancel_requested.is_set():
                event.cancel_node = "Document processing was cancelled"
                return
            stage = node_stages.get(event.node_id)
            if stage is not None and on_stage is not None:
                on_stage(stage)

        built_graph.add_hook(report_node_stage, BeforeNodeCallEvent)
        return built_graph

    def extract_document(
        self,
        page_paths: Sequence[Path],
        page_text: Sequence[str | None],
        on_stage: Callable[[ProcessingStage], None] | None = None,
    ) -> ProviderExtraction:
        if not page_paths:
            raise ProviderExtractionError("No preprocessed pages were supplied")
        if len(page_paths) != len(page_text):
            raise ProviderExtractionError("Page image and text counts do not match")

        text_tools = DocumentTextTools(tuple(page_text))
        graph = self._build_graph(text_tools, on_stage)
        content: list[dict[str, object]] = [
            {
                "text": (
                    "Classify and extract this accounting document across all supplied pages. "
                    "Page images follow in 1-based order. Preserve source wording exactly."
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
            if self._cancel_requested.is_set():
                raise ProviderCancellationError("Document processing was cancelled")
            classification_result = graph_result.results["classify"].result
            extraction_result = graph_result.results["extract"].result
            organizer_result = graph_result.results["organize"].result
            classification = classification_result.structured_output
            draft = extraction_result.structured_output
            presentation_draft = organizer_result.structured_output
            quality_node = graph_result.results.get("quality")
            quality_result = quality_node.result if quality_node else None
            quality_review = quality_result.structured_output if quality_result else None
            if not isinstance(classification, DocumentClassification):
                raise ProviderExtractionError("The classifier returned no classification")
            if not isinstance(draft, GenericExtractionDraft):
                raise ProviderExtractionError("The extractor returned no structured extraction")
            if not isinstance(presentation_draft, PresentationDraft):
                raise ProviderExtractionError("The organizer returned no presentation plan")
            if quality_review is not None and not isinstance(quality_review, QualityReviewDraft):
                raise ProviderExtractionError("The quality reviewer returned invalid output")
            document, quality_issues = finalize_extraction(
                draft,
                classification.document_type,
                page_paths,
                quality_review,
            )
            presentation = finalize_presentation(document, presentation_draft)
            document, presentation, quality_issues = remove_non_accounting_boilerplate(
                document,
                presentation,
                quality_issues,
            )
        except ModelThrottledException as exc:
            raise ProviderRateLimitError("The model provider rate limit was reached") from exc
        except ProviderCancellationError:
            raise
        except ProviderExtractionError:
            raise
        except Exception as exc:
            if self._cancel_requested.is_set():
                raise ProviderCancellationError("Document processing was cancelled") from exc
            raise ProviderExtractionError("The agentic document workflow failed") from exc
        finally:
            with self._agents_lock:
                self._active_agents = ()
            self._cancel_requested.clear()

        usage = graph_result.accumulated_usage
        return ProviderExtraction(
            document=document,
            presentation=presentation,
            quality_issues=quality_issues,
            raw_provider_output={
                "classification": classification.model_dump(mode="json"),
                "classification_stop_reason": classification_result.stop_reason,
                "extraction_stop_reason": extraction_result.stop_reason,
                "organizer_stop_reason": organizer_result.stop_reason,
                "quality_review_performed": quality_result is not None,
                "quality_stop_reason": quality_result.stop_reason if quality_result else None,
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

    node_timeout = (settings.worker_lease_seconds - 30) / 3
    return StrandsExtractionProvider(model=model, node_timeout_seconds=node_timeout)
