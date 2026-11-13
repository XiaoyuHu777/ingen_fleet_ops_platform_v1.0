"""Environment-based configuration without committed credentials."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    operator_api_key: SecretStr | None = None
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    minio_endpoint: str = "minio:9000"
    minio_root_user: str
    minio_root_password: SecretStr
    minio_secure: bool = False
    dependency_timeout_seconds: int = Field(default=2, ge=1, le=10)
    readiness_deadline_seconds: float = Field(default=4.0, gt=0, le=4.0)
    artifact_bucket: str = Field(
        default="fleet-artifacts", pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
    )
    artifact_trust_dir: Path | None = None
