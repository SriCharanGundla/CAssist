from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "CAssist API"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://cassist:cassist@localhost:5432/cassist"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    frontend_url: str = "http://localhost:5173"

    auth_issuer_url: str | None = None
    auth_client_id: str | None = None
    auth_client_secret: str | None = None
    auth_callback_url: str = "http://localhost:8000/api/v1/auth/callback"
    auth_post_logout_redirect_url: str = "http://localhost:5173"
    auth_state_secret: str | None = None
    auth_session_idle_seconds: int = 8 * 60 * 60
    auth_session_absolute_seconds: int = 7 * 24 * 60 * 60
    auth_session_cookie_name: str = "cassist_session"

    model_provider: Literal["openai", "gemini"] = "gemini"
    model_id: str = "gemini-3.5-flash"
    allow_provider_override: bool = True
    gemini_api_key: str | None = None
    openai_api_key: str | None = None

    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str = "cassist-documents"
    r2_presigned_url_ttl_seconds: int = 300

    @model_validator(mode="after")
    def enforce_production_model(self) -> "Settings":
        if self.auth_session_idle_seconds <= 0:
            raise ValueError("AUTH_SESSION_IDLE_SECONDS must be positive")
        if self.auth_session_absolute_seconds < self.auth_session_idle_seconds:
            raise ValueError("AUTH_SESSION_ABSOLUTE_SECONDS must be at least the idle lifetime")

        if self.app_env == "production":
            self.model_provider = "openai"
            self.model_id = "gpt-5.6-luna"
            self.allow_provider_override = False
            missing_auth_settings = [
                name
                for name, value in {
                    "AUTH_ISSUER_URL": self.auth_issuer_url,
                    "AUTH_CLIENT_ID": self.auth_client_id,
                    "AUTH_CLIENT_SECRET": self.auth_client_secret,
                    "AUTH_STATE_SECRET": self.auth_state_secret,
                }.items()
                if not value
            ]
            if missing_auth_settings:
                missing = ", ".join(missing_auth_settings)
                raise ValueError(f"Production authentication settings are missing: {missing}")
            if len(self.auth_state_secret or "") < 32:
                raise ValueError("AUTH_STATE_SECRET must contain at least 32 characters")
            self.auth_session_cookie_name = "__Host-cassist_session"
        return self

    @property
    def auth_configured(self) -> bool:
        return all(
            (
                self.auth_issuer_url,
                self.auth_client_id,
                self.auth_client_secret,
                self.auth_state_secret,
            )
        )

    @property
    def auth_cookie_secure(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
