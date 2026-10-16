from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-sonnet-5-5", alias="LLM_MODEL")
    llm_inference_geo: str = Field(default="us", alias="LLM_INFERENCE_GEO")

    retention_hours: int = Field(default=24, alias="RETENTION_HOURS")

    auth_mode: AuthMode = Field(default=AuthMode.NONE, alias="AUTH_MODE")
    access_code: str | None = Field(default=None, alias="ACCESS_CODE")
    oidc_issuer: str | None = Field(default=None, alias="OIDC_ISSUER")
    oidc_audience: str | None = Field(default=None, alias="OIDC_AUDIENCE")

    spacy_model: str = Field(default="en_core_web_md", alias="SPACY_MODEL")
    pdf_backend: PdfBackendName = Field(default=PdfBackendName.PYMUPDF, alias="PDF_BACKEND")
    license_path_confirmed: str | None = Field(default=None, alias="LICENSE_PATH_CONFIRMED")

    workers: int = Field(default=2, alias="WORKERS")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    @property
    def current_tax_year_default(self) -> int:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        return now.year - 1

    @model_validator(mode="after")
    def validate_security_modes(self) -> "Settings":
        if self.auth_mode == AuthMode.NONE and self.app_host not in {"127.0.0.1", "localhost"}:
            raise ValueError("AUTH_MODE=none is only allowed with APP_HOST=127.0.0.1 or localhost")

        if self.auth_mode == AuthMode.ACCESS_CODE and not self.access_code:
            raise ValueError("ACCESS_CODE is required when AUTH_MODE=access_code")

        if self.storage_backend == StorageBackendName.S3:
            missing = [name for name, value in {
                "S3_BUCKET": self.s3_bucket,
                "S3_ACCESS_KEY": self.s3_access_key,
                "S3_SECRET_KEY": self.s3_secret_key,
            }.items() if not value]
            if missing:
                raise ValueError(f"Missing S3 configuration values: {', '.join(missing)}")

        return self


settings = Settings()
