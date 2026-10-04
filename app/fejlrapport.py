"""Fejlrapporter til Sentry – uden hemmeligheder.

    init_fejlrapport("api")      # i webdelen
    init_fejlrapport("worker")   # i worker og scheduler

Slået fra, når SENTRY_DSN er tom. Når den er slået til:
- lokale variabler sendes ALDRIG med (include_local_variables=False) – de kan
  indeholde tokens,
- ingen personoplysninger, cookies eller request-indhold (send_default_pii=False,
  max_request_body_size="never"),
- hver eneste tekst i en rapport køres gennem rediger(), så kendte tokens bliver
  maskeret (****abcd), før noget forlader serveren. Fællesnøglerne fra .env
  registreres som kendte hemmeligheder ved opstart.
"""

import logging

from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed, rediger

log = logging.getLogger(__name__)


def _rens(vaerdi):
    """Gå hele rapporten igennem og masker alle kendte hemmeligheder i tekster."""
    if isinstance(vaerdi, str):
        return rediger(vaerdi)
    if isinstance(vaerdi, dict):
        return {_rens(k): _rens(v) for k, v in vaerdi.items()}
    if isinstance(vaerdi, list):
        return [_rens(v) for v in vaerdi]
    if isinstance(vaerdi, tuple):
        return tuple(_rens(v) for v in vaerdi)
    return vaerdi


def rens_haendelse(haendelse, _hint=None):
    """Sentrys before_send / before_breadcrumb."""
    return _rens(haendelse)


def _registrer_faelles_hemmeligheder() -> None:
    s = get_settings()
    for felt in (s.database_url, s.credentials_key, s.economic_app_secret_token, s.sentry_dsn):
        if felt is not None and felt.get_secret_value():
            registrer_hemmelighed(felt.get_secret_value())
    # Adgangskoden inde i databaseadressen skal også skjules for sig.
    url = s.database_url.get_secret_value()
    if "://" in url and "@" in url:
        brugerinfo = url.split("://", 1)[1].rsplit("@", 1)[0]
        if ":" in brugerinfo:
            registrer_hemmelighed(brugerinfo.split(":", 1)[1])


def sentry_indstillinger(komponent: str) -> dict:
    """Indstillingerne til sentry_sdk.init (adskilt, så de kan testes)."""
    s = get_settings()
    return {
        "dsn": s.sentry_dsn.get_secret_value() if s.sentry_dsn else None,
        "environment": s.app_env,
        "server_name": komponent,
        "include_local_variables": False,
        "send_default_pii": False,
        "max_request_body_size": "never",
        "before_send": rens_haendelse,
        "before_send_transaction": rens_haendelse,
        "before_breadcrumb": rens_haendelse,
        "traces_sample_rate": 0.0,
    }


def init_fejlrapport(komponent: str) -> bool:
    """Slå Sentry til, hvis SENTRY_DSN er sat. Returnerer True, hvis det er slået til."""
    _registrer_faelles_hemmeligheder()
    indstillinger = sentry_indstillinger(komponent)
    if not indstillinger["dsn"]:
        log.info("Sentry er slået fra (SENTRY_DSN er tom)")
        return False
    import sentry_sdk

    sentry_sdk.init(**indstillinger)
    sentry_sdk.set_tag("komponent", komponent)
    log.info("Sentry slået til for %s", komponent)
    return True
