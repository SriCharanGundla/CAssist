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
        if self.app_env == "production":
            self.model_provider = "openai"
            self.model_id = "gpt-5.6-luna"
            self.allow_provider_override = False
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
