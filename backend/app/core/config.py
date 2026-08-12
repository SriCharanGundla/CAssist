from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCKED_ALLOWED_USER_EMAILS = frozenset(
    {"owner@example.test", "reviewer@example.test"}
)


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
    auth_allowed_emails: frozenset[str] = Field(
        default_factory=lambda: LOCKED_ALLOWED_USER_EMAILS
    )

    model_provider: Literal["openai", "gemini"] = "gemini"
    model_id: str = "gemini-3.5-flash-lite"
    comparison_gemini_model_id: str = "gemini-3.5-flash-lite"
    comparison_openai_model_id: str = "gpt-5.6-luna"
    allow_provider_override: bool = True
    prompt_version: str = "generic-document-v1"
    schema_version: str = "generic-extraction-v1"
    preprocessing_version: str = "document-native-text-v2"
    gemini_api_key: str | None = None
    openai_api_key: str | None = None

    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str = "cassist-documents"
    r2_presigned_url_ttl_seconds: int = 300
    upload_max_bytes: int = 25 * 1024 * 1024
    worker_lease_seconds: int = 5 * 60
    worker_poll_seconds: float = 2.0
    provider_rate_limit_retry_seconds: int = 60
    provider_rate_limit_max_attempts: int = 3
    preprocessing_max_pages: int = 50
    preprocessing_render_dpi: int = 144
    preprocessing_max_pixels: int = 40_000_000
    preprocessing_max_total_pixels: int = 200_000_000
    provider_timeout_seconds: int = 120
    provider_max_retries: int = 1

    @model_validator(mode="after")
    def enforce_production_model(self) -> "Settings":
        self.auth_allowed_emails = frozenset(
            email.strip().casefold() for email in self.auth_allowed_emails
        )
        if self.app_env != "test" and self.auth_allowed_emails != LOCKED_ALLOWED_USER_EMAILS:
            raise ValueError("AUTH_ALLOWED_EMAILS is locked outside tests")
        if self.auth_session_idle_seconds <= 0:
            raise ValueError("AUTH_SESSION_IDLE_SECONDS must be positive")
        if self.auth_session_absolute_seconds < self.auth_session_idle_seconds:
            raise ValueError("AUTH_SESSION_ABSOLUTE_SECONDS must be at least the idle lifetime")
        if not 60 <= self.r2_presigned_url_ttl_seconds <= 900:
            raise ValueError("R2_PRESIGNED_URL_TTL_SECONDS must be between 60 and 900")
        if self.upload_max_bytes <= 0:
            raise ValueError("UPLOAD_MAX_BYTES must be positive")
        if self.worker_lease_seconds <= 0:
            raise ValueError("WORKER_LEASE_SECONDS must be positive")
        if self.worker_poll_seconds <= 0:
            raise ValueError("WORKER_POLL_SECONDS must be positive")
        if self.provider_rate_limit_retry_seconds <= 0:
            raise ValueError("PROVIDER_RATE_LIMIT_RETRY_SECONDS must be positive")
        if not 1 <= self.provider_rate_limit_max_attempts <= 5:
            raise ValueError("PROVIDER_RATE_LIMIT_MAX_ATTEMPTS must be between 1 and 5")
        if self.preprocessing_max_pages <= 0:
            raise ValueError("PREPROCESSING_MAX_PAGES must be positive")
        if self.preprocessing_render_dpi <= 0:
            raise ValueError("PREPROCESSING_RENDER_DPI must be positive")
        if self.preprocessing_max_pixels <= 0:
            raise ValueError("PREPROCESSING_MAX_PIXELS must be positive")
        if self.preprocessing_max_total_pixels < self.preprocessing_max_pixels:
            raise ValueError(
                "PREPROCESSING_MAX_TOTAL_PIXELS must be at least PREPROCESSING_MAX_PIXELS"
            )
        if self.provider_timeout_seconds <= 0:
            raise ValueError("PROVIDER_TIMEOUT_SECONDS must be positive")
        if not 0 <= self.provider_max_retries <= 5:
            raise ValueError("PROVIDER_MAX_RETRIES must be between 0 and 5")
        minimum_lease_seconds = self.provider_timeout_seconds * (self.provider_max_retries + 1) + 30
        if self.worker_lease_seconds < minimum_lease_seconds:
            raise ValueError(
                "WORKER_LEASE_SECONDS must cover all provider attempts plus a 30-second margin"
            )

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

    @property
    def r2_configured(self) -> bool:
        return all(
            (
                self.r2_endpoint_url,
                self.r2_access_key_id,
                self.r2_secret_access_key,
                self.r2_bucket_name,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
