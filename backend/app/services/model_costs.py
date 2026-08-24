from decimal import ROUND_HALF_UP, Decimal

from app.core.config import Settings
from app.services.model_provider import ModelSelection

_ONE_MILLION = Decimal(1_000_000)
_DATABASE_INCREMENT = Decimal("0.000001")


def estimate_model_cost_usd(
    selection: ModelSelection,
    input_tokens: int,
    output_tokens: int,
    settings: Settings,
) -> Decimal | None:
    """Estimate a run cost from provider usage; invoices remain authoritative."""
    if selection.provider != "openai" or selection.model_id != "gpt-5.6-luna":
        return None
    cost = (
        Decimal(input_tokens) * settings.openai_input_cost_per_million_usd
        + Decimal(output_tokens) * settings.openai_output_cost_per_million_usd
    ) / _ONE_MILLION
    return cost.quantize(_DATABASE_INCREMENT, rounding=ROUND_HALF_UP)
