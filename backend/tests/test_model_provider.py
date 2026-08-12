from pathlib import Path
from types import SimpleNamespace

import pytest
from strands.models.gemini import GeminiModel
from strands.models.openai_responses import OpenAIResponsesModel

from app.core.config import Settings
from app.schemas.extraction import CanonicalInvoice, InvoiceTotals, Party
from app.services.model_provider import (
    ModelSelection,
    ProviderConfigurationError,
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
        openai_api_key="test-openai-key",
    )


def test_verified_model_identifiers_and_production_selection_are_locked() -> None:
    development = Settings(app_env="test", _env_file=None)
    production = _production_settings()

    assert resolve_model_selection(development) == ModelSelection(
        provider="gemini",
        model_id="gemini-3.5-flash",
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
    assert isinstance(production_provider.agent.model, OpenAIResponsesModel)
    assert production_provider.agent.model.get_config()["stateful"] is False

    development = Settings(
        app_env="test",
        _env_file=None,
        gemini_api_key="test-gemini-key",
    )
    gemini_provider = create_extraction_provider(
        development,
        ModelSelection(provider="gemini", model_id="gemini-3.5-flash"),
    )
    assert isinstance(gemini_provider.agent.model, GeminiModel)
    with pytest.raises(ProviderConfigurationError):
        create_extraction_provider(
            _production_settings(),
            ModelSelection(provider="gemini", model_id="gemini-3.5-flash"),
        )


def test_provider_requires_the_selected_api_key() -> None:
    settings = Settings(app_env="test", _env_file=None)
    with pytest.raises(ProviderConfigurationError, match="Gemini"):
        create_extraction_provider(
            settings,
            ModelSelection(provider="gemini", model_id="gemini-3.5-flash"),
        )
    with pytest.raises(ProviderConfigurationError, match="OpenAI"):
        create_extraction_provider(
            settings,
            ModelSelection(provider="openai", model_id="gpt-5.6-luna"),
        )


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
    page_path.write_bytes(b"synthetic-image")
    invoice = CanonicalInvoice(
        invoice_number="INV-1",
        invoice_date="2026-08-12",
        supplier=Party(name="Supplier"),
        buyer=Party(name="Buyer"),
        totals=InvoiceTotals(grand_total="100.00"),
    )

    class FakeAgent:
        def __init__(self) -> None:
            self.prompt = None

        def __call__(self, prompt, **kwargs):
            self.prompt = prompt
            assert kwargs["structured_output_model"] is CanonicalInvoice
            return SimpleNamespace(
                structured_output=invoice,
                message={"role": "assistant", "content": [{"text": "structured"}]},
                stop_reason="end_turn",
                metrics=SimpleNamespace(accumulated_usage={"inputTokens": 12, "outputTokens": 8}),
            )

    agent = FakeAgent()
    provider = StrandsExtractionProvider(agent)  # type: ignore[arg-type]
    extraction = provider.extract_invoice([page_path])

    assert extraction.invoice == invoice
    assert extraction.input_tokens == 12
    assert extraction.output_tokens == 8
    assert extraction.raw_provider_output["stop_reason"] == "end_turn"
    assert agent.prompt[1]["image"]["source"]["bytes"] == b"synthetic-image"
