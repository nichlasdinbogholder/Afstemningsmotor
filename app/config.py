"""Indstillinger læses fra miljøvariabler / .env.

Hemmeligheder er typet som SecretStr, så de vises som '**********', hvis
indstillingerne nogensinde bliver udskrevet eller logget.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: SecretStr
    token_encryption_key: SecretStr

    allow_booking: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
