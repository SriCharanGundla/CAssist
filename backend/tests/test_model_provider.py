from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from strands.models.gemini import GeminiModel
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.exceptions import ModelThrottledException

from app.core.config import Settings
from app.schemas.extraction import (
    DocumentClassification,
    DraftField,
    GenericExtractionDraft,
    PresentationDraft,
    QualityReviewDraft,
)
from app.services.document_text_tools import DocumentTextTools
from app.services.model_provider import (
    ModelSelection,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderScopeBlocked,
    StrandsExtractionProvider,
    create_extraction_provider,
    resolve_model_selection,
)


def _production_settings() -> Settings:
    return Settings(
        app_env="production",
        _env_file=None,
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
        edge_proxy_secret="e" * 32,
        openai_api_key="test-openai-key",
        r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        frontend_url="https://cassist.pages.dev",
        cors_origins=["https://cassist.pages.dev"],
        auth_callback_url="https://cassist.pages.dev/api/v1/auth/callback",
        auth_post_logout_redirect_url="https://cassist.pages.dev",
        auth_allowed_emails={"owner@example.test"},
    )


def test_verified_model_identifiers_and_production_selection_are_locked() -> None:
    development = Settings(app_env="test", _env_file=None)
    production = _production_settings()

    assert resolve_model_selection(development) == ModelSelection(
        provider="gemini",
        model_id="gemini-3.5-flash-lite",
    )
    assert resolve_model_selection(production) == ModelSelection(
        provider="openai",
        model_id="gpt-5.6-luna",
    )
    with pytest.raises(PermissionError):
        resolve_model_selection(production, provider_override="gemini")


def test_factory_uses_strands_responses_api_for_openai_and_gemini_only_outside_production() -> None:
    production_provider = create_extraction_provider(
        _production_settings(),
        ModelSelection(provider="openai", model_id="gpt-5.6-luna"),
    )
    assert isinstance(production_provider.model, OpenAIResponsesModel)
    assert production_provider.model.get_config()["stateful"] is False
    request = production_provider.model._format_request([])  # noqa: SLF001
    assert request["store"] is False

    development = Settings(
        app_env="test",
        _env_file=None,
        gemini_api_key="test-gemini-key",
    )
    gemini_provider = create_extraction_provider(
        development,
        ModelSelection(provider="gemini", model_id="gemini-3.5-flash-lite"),
    )
    assert isinstance(gemini_provider.model, GeminiModel)
    with pytest.raises(ProviderConfigurationError):
        create_extraction_provider(
            _production_settings(),
            ModelSelection(provider="gemini", model_id="gemini-3.5-flash-lite"),
        )


def test_provider_requires_the_selected_api_key() -> None:
    settings = Settings(app_env="test", _env_file=None)
    with pytest.raises(ProviderConfigurationError, match="Gemini"):
        create_extraction_provider(
            settings,
            ModelSelection(provider="gemini", model_id="gemini-3.5-flash-lite"),
        )
    with pytest.raises(ProviderConfigurationError, match="OpenAI"):
        create_extraction_provider(
            settings,
            ModelSelection(provider="openai", model_id="gpt-5.6-luna"),
        )


def test_agent_graph_has_bounded_specialists_and_only_native_text_tools() -> None:
    provider = create_extraction_provider(
        Settings(app_env="test", _env_file=None, gemini_api_key="test-key"),
        ModelSelection(provider="gemini", model_id="gemini-3.5-flash-lite"),
    )
    classifier, classification_graph = provider._build_classification_graph()  # type: ignore[attr-defined]
    specialists, graph = provider._build_extraction_graph(  # type: ignore[attr-defined]
        DocumentTextTools((None,))
    )

    assert list(classification_graph.nodes) == ["classify"]
    assert classifier.tool_names == []
    assert list(graph.nodes) == ["extract", "organize", "quality"]
    assert graph.nodes["extract"].executor.tool_names == [
        "read_document_text",
        "search_document_text",
    ]
    assert graph.nodes["organize"].executor.tool_names == []
    assert graph.nodes["quality"].executor.tool_names == [
        "read_document_text",
        "search_document_text",
    ]
    assert {
        (edge.from_node.node_id, edge.to_node.node_id, edge.condition is not None)
        for edge in graph.edges
    } == {
        ("extract", "organize", False),
        ("extract", "quality", True),
        ("organize", "quality", True),
    }


def test_provider_attempts_must_fit_inside_worker_lease() -> None:
    with pytest.raises(ValueError, match="WORKER_LEASE_SECONDS"):
        Settings(
            app_env="test",
            _env_file=None,
            worker_lease_seconds=269,
            provider_timeout_seconds=120,
            provider_max_retries=1,
        )

    settings = Settings(
        app_env="test",
        _env_file=None,
        worker_lease_seconds=270,
        provider_timeout_seconds=120,
        provider_max_retries=1,
    )
    assert settings.worker_lease_seconds == 270


def test_strands_adapter_sends_page_images_and_returns_usage(tmp_path: Path) -> None:
    page_path = tmp_path / "page-0001.png"
    Image.new("RGB", (120, 80), "white").save(page_path)

    class FakeClassificationGraph:
        def __init__(self) -> None:
            self.prompt = None

        def __call__(self, prompt):
            self.prompt = prompt
            return SimpleNamespace(
                results={
                    "classify": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=DocumentClassification(
                                scope="supported",
                                document_type="invoice",
                                confidence=0.99,
                                reason_code="financial_content",
                            ),
                            stop_reason="end_turn",
                        )
                    )
                },
                accumulated_usage={"inputTokens": 3, "outputTokens": 2},
            )

    class FakeExtractionGraph:
        def __init__(self, text_tools) -> None:
            self.text_tools = text_tools
            self.prompt = None

        def __call__(self, prompt):
            self.prompt = prompt
            return SimpleNamespace(
                results={
                    "extract": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=GenericExtractionDraft(
                                fields=[
                                    DraftField(
                                        label="Bill No.",
                                        value="INV-1",
                                        page_number=1,
                                    )
                                ]
                            ),
                            stop_reason="end_turn",
                        )
                    ),
                    "organize": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=PresentationDraft(
                                sections=[
                                    {
                                        "title": "Invoice details",
                                        "target_paths": ["/fields/0"],
                                    }
                                ]
                            ),
                            stop_reason="end_turn",
                        )
                    ),
                },
                accumulated_usage={"inputTokens": 12, "outputTokens": 8},
                execution_order=[
                    SimpleNamespace(node_id="extract"),
                    SimpleNamespace(node_id="organize"),
                ],
            )

    provider = StrandsExtractionProvider(model=object(), node_timeout_seconds=120)  # type: ignore[arg-type]
    classification_graph = FakeClassificationGraph()
    graph = None

    def build_graph(text_tools, _on_stage=None):
        nonlocal graph
        graph = FakeExtractionGraph(text_tools)
        return (SimpleNamespace(), SimpleNamespace(), SimpleNamespace()), graph

    provider._build_classification_graph = lambda: (  # type: ignore[method-assign]
        SimpleNamespace(),
        classification_graph,
    )
    provider._build_extraction_graph = build_graph  # type: ignore[method-assign]
    extraction = provider.extract_document([page_path], ("Bill No. INV-1",))

    assert extraction.document.document_type == "invoice"
    assert extraction.document.fields[0].label == "Bill No."
    assert extraction.document.fields[0].value == "INV-1"
    assert extraction.document.fields[0].id == "field-0001"
    assert extraction.presentation.sections[0].title == "Invoice details"
    assert extraction.presentation.sections[0].target_ids == ["field-0001"]
    assert extraction.quality_issues == []
    assert extraction.input_tokens == 15
    assert extraction.output_tokens == 10
    assert extraction.raw_provider_output["extraction_stop_reason"] == "end_turn"
    assert graph is not None
    assert graph.prompt[1]["image"]["source"]["bytes"] == page_path.read_bytes()
    assert classification_graph.prompt[1]["image"]["format"] == "jpeg"


def test_quality_review_is_recorded_without_overwriting_extraction(tmp_path: Path) -> None:
    page_path = tmp_path / "page.png"
    Image.new("RGB", (120, 80), "white").save(page_path)

    class FakeClassificationGraph:
        def __call__(self, _prompt):
            return SimpleNamespace(
                results={
                    "classify": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=DocumentClassification(
                                scope="supported",
                                document_type="receipt",
                                confidence=0.9,
                                reason_code="financial_content",
                            ),
                            stop_reason="end_turn",
                        )
                    )
                },
                accumulated_usage={"inputTokens": 4, "outputTokens": 2},
            )

    class FakeExtractionGraph:
        def __call__(self, _prompt):
            return SimpleNamespace(
                results={
                    "extract": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=GenericExtractionDraft(
                                fields=[
                                    DraftField(
                                        label="Receipt No",
                                        value="1NV-1O2",
                                        page_number=1,
                                    )
                                ],
                                quality_review_recommended=True,
                            ),
                            stop_reason="end_turn",
                        )
                    ),
                    "organize": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=PresentationDraft(
                                sections=[
                                    {
                                        "title": "Receipt details",
                                        "target_paths": ["/fields/0"],
                                    }
                                ]
                            ),
                            stop_reason="end_turn",
                        )
                    ),
                    "quality": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=QualityReviewDraft(
                                issues=[
                                    {
                                        "target_path": "/fields/0",
                                        "code": "possible_ocr_error",
                                        "message": "Possible character confusion",
                                        "suggested_value": "INV-102",
                                    }
                                ]
                            ),
                            stop_reason="end_turn",
                        )
                    ),
                },
                accumulated_usage={"inputTokens": 20, "outputTokens": 10},
                execution_order=[
                    SimpleNamespace(node_id="extract"),
                    SimpleNamespace(node_id="organize"),
                    SimpleNamespace(node_id="quality"),
                ],
            )

    provider = StrandsExtractionProvider(model=object(), node_timeout_seconds=120)  # type: ignore[arg-type]
    provider._build_classification_graph = lambda: (  # type: ignore[method-assign]
        SimpleNamespace(),
        FakeClassificationGraph(),
    )
    provider._build_extraction_graph = lambda _tools, _on_stage=None: (  # type: ignore[method-assign]
        (SimpleNamespace(), SimpleNamespace(), SimpleNamespace()),
        FakeExtractionGraph(),
    )

    extraction = provider.extract_document([page_path], (None,))

    assert extraction.document.fields[0].value == "1NV-1O2"
    assert extraction.quality_issues[0].target_id == "field-0001"
    assert extraction.quality_issues[0].suggested_value == "INV-102"


def test_provider_preserves_throttling_as_a_safe_retryable_error(tmp_path: Path) -> None:
    page_path = tmp_path / "page.png"
    Image.new("RGB", (120, 80), "white").save(page_path)

    class ThrottledGraph:
        def __call__(self, _prompt):
            raise ModelThrottledException("provider details must not escape")

    provider = StrandsExtractionProvider(model=object(), node_timeout_seconds=120)  # type: ignore[arg-type]
    provider._build_classification_graph = lambda: (  # type: ignore[method-assign]
        SimpleNamespace(),
        ThrottledGraph(),
    )

    with pytest.raises(ProviderRateLimitError, match="rate limit"):
        provider.extract_document([page_path], (None,))


def test_unrelated_and_low_confidence_documents_stop_before_extraction(tmp_path: Path) -> None:
    page_path = tmp_path / "page.png"
    Image.new("RGB", (2000, 1200), "white").save(page_path)

    class ClassificationGraph:
        def __init__(self, classification: DocumentClassification) -> None:
            self.classification = classification

        def __call__(self, _prompt):
            return SimpleNamespace(
                results={
                    "classify": SimpleNamespace(
                        result=SimpleNamespace(
                            structured_output=self.classification,
                            stop_reason="end_turn",
                        )
                    )
                },
                accumulated_usage={"inputTokens": 7, "outputTokens": 3},
            )

    for classification, expected_scope in (
        (
            DocumentClassification(
                scope="unrelated",
                document_type="unknown",
                confidence=0.95,
                reason_code="unrelated_content",
            ),
            "unrelated",
        ),
        (
            DocumentClassification(
                scope="supported",
                document_type="invoice",
                confidence=0.4,
                reason_code="financial_content",
            ),
            "uncertain",
        ),
    ):
        provider = StrandsExtractionProvider(model=object(), node_timeout_seconds=120)  # type: ignore[arg-type]
        provider._build_classification_graph = lambda classification=classification: (  # type: ignore[method-assign]
            SimpleNamespace(),
            ClassificationGraph(classification),
        )
        with pytest.raises(ProviderScopeBlocked) as blocked:
            provider.extract_document([page_path], (None,))
        assert blocked.value.classification.scope == expected_scope
        assert blocked.value.input_tokens == 7
