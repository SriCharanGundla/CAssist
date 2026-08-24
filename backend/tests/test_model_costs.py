from decimal import Decimal

from app.core.config import Settings
from app.services.model_costs import estimate_model_cost_usd
from app.services.model_provider import ModelSelection


def test_luna_cost_estimate_uses_configured_full_run_token_rates() -> None:
    estimate = estimate_model_cost_usd(
        ModelSelection("openai", "gpt-5.6-luna"),
        input_tokens=1_000_000,
        output_tokens=100_000,
        settings=Settings(_env_file=None),
    )

    assert estimate == Decimal("0.320000")


def test_cost_estimate_is_omitted_for_unpriced_models() -> None:
    settings = Settings(_env_file=None)

    assert estimate_model_cost_usd(
        ModelSelection("gemini", "gemini-3.5-flash-lite"), 100, 100, settings
    ) is None
    assert estimate_model_cost_usd(
        ModelSelection("openai", "custom-model"), 100, 100, settings
    ) is None
