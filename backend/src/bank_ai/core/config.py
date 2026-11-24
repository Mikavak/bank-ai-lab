from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    app_name: str = "Bank AI Lab"
    app_env: str = "local"

    database_url: str

    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=30, ge=1, le=1440)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )
    demo_client_email: str = "client@bank-ai.local"
    demo_client_password: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()