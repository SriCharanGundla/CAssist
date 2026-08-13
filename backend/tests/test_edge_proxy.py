from app.core.config import Settings
from app.core.edge_proxy import edge_proxy_authorized


def production_settings() -> Settings:
    return Settings(
        app_env="production",
        _env_file=None,
        auth_issuer_url="https://tenant.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
        edge_proxy_secret="e" * 32,
        openai_api_key="openai-key",
        r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        frontend_url="https://cassist.pages.dev",
        cors_origins=["https://cassist.pages.dev"],
        auth_callback_url="https://cassist.pages.dev/api/v1/auth/callback",
        auth_post_logout_redirect_url="https://cassist.pages.dev",
    )


def test_production_accepts_only_the_pages_proxy_secret() -> None:
    settings = production_settings()
    assert edge_proxy_authorized("e" * 32, settings)
    assert not edge_proxy_authorized(None, settings)
    assert not edge_proxy_authorized("wrong", settings)


def test_development_does_not_require_an_edge_proxy() -> None:
    assert edge_proxy_authorized(None, Settings(app_env="development", _env_file=None))
