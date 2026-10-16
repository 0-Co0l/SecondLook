from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class AuthMode(StrEnum):
    NONE = "none"
    ACCESS_CODE = "access_code"
    OIDC = "oidc"


class StorageBackendName(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class PdfBackendName(StrEnum):
    PYMUPDF = "pymupdf"
    PERMISSIVE = "permissive"


class LlmProvider(StrEnum):
    ANTHROPIC = "anthropic"
    # Canned findings with no network call; only allowed for local development and tests.
    FAKE = "fake"


class Settings(BaseSettings):
    # The repo-root .env is found from backend/ too; a later file wins.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    database_url: str = Field(default="sqlite:///./data/app.db", alias="DATABASE_URL")

    storage_backend: StorageBackendName = Field(default=StorageBackendName.LOCAL, alias="STORAGE_BACKEND")
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_access_key: str | None = Field(default=None, alias="S3_ACCESS_KEY")
    s3_secret_key: str | None = Field(default=None, alias="S3_SECRET_KEY")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")

    file_encryption_key: str = Field(alias="FILE_ENCRYPTION_KEY")
    # Signs session cookies. Derived from FILE_ENCRYPTION_KEY when unset so the two never share raw key material.
    session_secret: str | None = Field(default=None, alias="SESSION_SECRET")

    llm_provider: LlmProvider = Field(default=LlmProvider.ANTHROPIC, alias="LLM_PROVIDER")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-sonnet-5-5", alias="LLM_MODEL")
    llm_inference_geo: str | None = Field(default="us", alias="LLM_INFERENCE_GEO")
    llm_refusal_fallback: bool = Field(default=True, alias="LLM_REFUSAL_FALLBACK")

    retention_hours: int = Field(default=24, alias="RETENTION_HOURS")

    auth_mode: AuthMode = Field(default=AuthMode.NONE, alias="AUTH_MODE")
    access_code: str | None = Field(default=None, alias="ACCESS_CODE")
    oidc_issuer: str | None = Field(default=None, alias="OIDC_ISSUER")
    oidc_audience: str | None = Field(default=None, alias="OIDC_AUDIENCE")

    spacy_model: str = Field(default="en_core_web_md", alias="SPACY_MODEL")
    pdf_backend: PdfBackendName = Field(default=PdfBackendName.PYMUPDF, alias="PDF_BACKEND")
    pdf_timeout_seconds: int = Field(default=60, alias="PDF_TIMEOUT_SECONDS")
    license_path_confirmed: str | None = Field(default=None, alias="LICENSE_PATH_CONFIRMED")
    # Path A (AGPL) requires a visible link to the exact running source (§4.8).
    source_code_url: str | None = Field(default=None, alias="SOURCE_CODE_URL")

    workers: int = Field(default=2, alias="WORKERS")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    max_upload_bytes: int = 25 * 1024 * 1024
    max_pages: int = 100

    @property
    def is_local_only(self) -> bool:
        return self.app_host in LOCAL_HOSTS

    @property
    def default_tax_year(self) -> int:
        return datetime.now(UTC).year - 1

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @model_validator(mode="after")
    def validate_security_modes(self) -> "Settings":
        if self.auth_mode == AuthMode.NONE and not self.is_local_only:
            raise ValueError("AUTH_MODE=none is only allowed with APP_HOST=127.0.0.1 or localhost")

        if self.auth_mode == AuthMode.ACCESS_CODE and not self.access_code:
            raise ValueError("ACCESS_CODE is required when AUTH_MODE=access_code")

        if self.llm_provider == LlmProvider.FAKE and self.auth_mode != AuthMode.NONE:
            raise ValueError("LLM_PROVIDER=fake is only allowed for local development (AUTH_MODE=none)")

        if self.llm_provider == LlmProvider.ANTHROPIC and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")

        if self.storage_backend == StorageBackendName.S3:
            missing = [
                name
                for name, value in {
                    "S3_BUCKET": self.s3_bucket,
                    "S3_ACCESS_KEY": self.s3_access_key,
                    "S3_SECRET_KEY": self.s3_secret_key,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"Missing S3 configuration values: {', '.join(missing)}")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
