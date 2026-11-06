from functools import lru_cache
from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PCC_", env_file=".env", extra="ignore"
    )
    bind_host: str = "127.0.0.1"
    database_url: str = "sqlite:///tmp/pcc.sqlite"
    secret: str = ""
    environment: str = "production"
    storage_root: Path = Path("tmp/artifacts")
    web_root: Path = Path("tmp/web-dist")
    secure_cookies: bool = True
    owner_username: str = "Fubuki"
    owner_password: str = ""
    heartbeat_timeout: int = Field(default=45, ge=5, le=3600)
    lease_seconds: int = Field(default=30, ge=5, le=300)
    artifact_max_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024
    )
    artifact_retention_days: int = Field(default=7, ge=1, le=3650)
    user_active_job_limit: int = Field(default=4, ge=1, le=100)
    user_daily_job_limit: int = Field(default=1000, ge=1, le=100000)
    trusted_proxies: str = "127.0.0.1"
    session_hours: int = Field(default=12, ge=1, le=168)
    chat_wait_seconds: int = Field(default=120, ge=1, le=3600)
    image_wait_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_security(self):
        if len(self.secret) < 32 or self.secret.startswith("REPLACE_"):
            raise ValueError("PCC_SECRET must contain at least 32 characters")
        if self.environment != "development":
            if "REPLACE_" in self.database_url:
                raise ValueError(
                    "Replace application database credentials before deployment"
                )
            if not self.secure_cookies or not self.database_url.startswith(
                "postgresql"
            ):
                raise ValueError("Production requires PostgreSQL and secure cookies")
        return self


@lru_cache
def get_settings():
    return Settings()
