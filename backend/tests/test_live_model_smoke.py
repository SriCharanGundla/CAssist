import importlib.util
from pathlib import Path

from PIL import Image

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "live_model_smoke.py"
SPEC = importlib.util.spec_from_file_location("live_model_smoke", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
LIVE_MODEL_SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIVE_MODEL_SMOKE)


def test_synthetic_live_model_fixture_is_generated_locally(tmp_path: Path) -> None:
    invoice_path = tmp_path / "synthetic-invoice.png"

    LIVE_MODEL_SMOKE._synthetic_invoice(invoice_path)

    with Image.open(invoice_path) as image:
        assert image.format == "PNG"
        assert image.size == (1600, 2000)
