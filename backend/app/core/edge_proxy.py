import hashlib
import secrets

from app.core.config import Settings

EDGE_PROXY_HEADER = "X-CAssist-Proxy-Secret"


def edge_proxy_authorized(supplied_secret: str | None, settings: Settings) -> bool:
    if settings.app_env != "production":
        return True
    if supplied_secret is None or settings.edge_proxy_secret is None:
        return False
    supplied_digest = hashlib.sha256(supplied_secret.encode()).digest()
    expected_digest = hashlib.sha256(settings.edge_proxy_secret.encode()).digest()
    return secrets.compare_digest(supplied_digest, expected_digest)
