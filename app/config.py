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
    # Hovednøgle til kryptering af kundernes tokens (Fernet).
    credentials_key: SecretStr

    # e-conomic: app-nøglen (X-AppSecretToken) er fælles for alle kunder og ligger
    # derfor her. Kundens egen nøgle (X-AgreementGrantToken) ligger krypteret i
    # credentials-tabellen.
    economic_app_secret_token: SecretStr | None = None
    economic_api_base_url: str = "https://restapi.e-conomic.com"

    allow_booking: bool = False

    # Fejlrapporter (Sentry). Tom = slået fra. Selve adressen (DSN) er en hemmelighed.
    sentry_dsn: SecretStr | None = None

    # Login med Microsoft (samme konto som Outlook). Tomme = login er slået fra.
    # Id'erne er ikke hemmelige; client secret og session-nøglen er.
    ms_tenant_id: str | None = None
    ms_client_id: str | None = None
    ms_client_secret: SecretStr | None = None
    # Nøgle til at underskrive login-cookien. Lav en med: openssl rand -hex 32
    session_secret: SecretStr | None = None
    # Adressen, brugerne åbner (til Microsofts tilbagesendelse efter login).
    public_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
