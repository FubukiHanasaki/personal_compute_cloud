from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PCC_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///tmp/pcc.sqlite"
    secret: str = ""
    environment: str = "production"
    storage_root: Path = Path("tmp/artifacts")
    web_root: Path = Path("tmp/web-dist")
    secure_cookies: bool = True
    owner_username: str = "Fubuki"
    owner_password: str = ""
    heartbeat_timeout: int = 45
    lease_seconds: int = 30
    artifact_max_bytes: int = 10 * 1024 * 1024
    artifact_retention_days: int = 7
    user_active_job_limit: int = 4
    session_hours: int = 12
    chat_wait_seconds: int = 120

    @model_validator(mode="after")
    def validate_security(self):
        if len(self.secret) < 32:
            raise ValueError("PCC_SECRET must contain at least 32 characters")
        if self.environment != "development":
            if not self.secure_cookies or not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL and secure cookies")
        return self


@lru_cache
def get_settings():
    return Settings()
