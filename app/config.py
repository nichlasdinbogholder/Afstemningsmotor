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

    # Bilagsindbakken (app/bilag). Hemmeligheden til Microsoft Graph ligger her som alle fælles nøgler.
    inbox_mailbox: str = "bilag@dinbogholder.dk"
    inbox_domain: str = "bilag.dinbogholder.dk"
    inbox_confidence_threshold: float = 0.85
    inbox_retention_days: int = 90
    inbox_max_attachment_mb: int = 25
    inbox_poll_seconds: int = 120
    inbox_due_date_fallback_days: int = 8
    inbox_min_image_px: int = 1000
    inbox_block_incomplete_representation: bool = False
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: SecretStr | None = None

    # Referencesatsen (Nationalbankens udlånsrente) hentes automatisk fra Danmarks Statistiks
    # Statistikbank (offentlig, ingen nøgle). VALG er kun nødvendig, hvis tabellen har flere mulige
    # serier – fx "INSTRUMENT=ODKNAA" (kør `python -m app.opkraevning.referencesats hent` for at se dem).
    # DNRENTD har opgørelserne E (daglige renter), A (gennemsnit) og T (omsætning): vi bruger E.
    referencesats_api_url: str = "https://api.statbank.dk/v1"
    referencesats_tabel: str = "DNRENTD"
    referencesats_valg: str | None = "OPGOER=E"

    # CVR-registret (Kundeoversigten trin 2): Erhvervsstyrelsens "CVR system-til-system"-adgang. Brugernavn og
    # adgangskode får man hos Erhvervsstyrelsen. Uden dem er CVR-opslag slået fra (intet hentes, intet fejler).
    kunde_cvr_api_url: str = "https://distribution.virk.dk/cvr-permanent/virksomhed/_search"
    kunde_cvr_api_bruger: str | None = None
    kunde_cvr_api_kode: SecretStr | None = None
    kunde_cvr_sync_cron: str = "0 6 * * *"   # dagligt opslag på alle aktive kunder (dansk tid)
    # Datafordeleren (bygherren 26.10.2026): CVR-virksomhedsdata med en gratis API-nøgle – app/kunder/datafordeler.py.
    datafordeler_cvr_url: str = "https://graphql.datafordeler.dk/CVR/v2"
    datafordeler_api_key: SecretStr | None = None

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
    # KUN til udvikling på egen maskine: /dev-login?email=... logger ind uden Microsoft.
    # Virker aldrig, når APP_ENV=production – uanset denne værdi.
    dev_login: bool = False

    # Fagassistenten (app/assistent). Uden nøgle er assistenten slået fra. Nøglen findes KUN på serveren.
    anthropic_api_key: SecretStr | None = None
    assistant_model: str = "claude-opus-5-5"
    assistant_max_tokens: int = 1500
    assistant_effort: str = "low"  # low | medium | high – hvor grundigt modellen tænker før svaret
    assistant_history_turns: int = 10
    assistant_kb_top_k: int = 5
    assistant_rate_stale_days: int = 45
    assistant_max_rows: int = 200
    assistant_max_tool_calls: int = 5
    # Svend slår selv satser, grænser og frister op på nettet (bygherren 26.10.2026) – KUN på disse sider
    # (kommasepareret; underdomæner som info.skat.dk er med). Tom = websøgning slået fra.
    assistant_web_domains: str = ("skat.dk,skm.dk,sktst.dk,retsinformation.dk,borger.dk,virk.dk,"
                                  "erhvervsstyrelsen.dk,nationalbanken.dk,dst.dk,atp.dk")
    assistant_web_max_uses: int = 5  # højst så mange søgninger og så mange hentede sider pr. svar

    # Udgående mail (fx påmindelser i timeregistreringen) via Microsoft Graph fra én fast afsender.
    # Egen app-registrering med KUN Mail.Send, begrænset til netop MAIL_AFSENDER. Uden nøgler sendes intet.
    mail_afsender: str = "info@dinbogholder.dk"
    mail_tenant_id: str | None = None
    mail_client_id: str | None = None
    mail_client_secret: SecretStr | None = None
    # Outlook-kalenderen til masterafstemninger (bygherren 26.10.2026): EGEN app-registrering med KUN
    # Calendars.ReadWrite, begrænset til KALENDER_POSTKASSE. Uden id/hemmelighed/postkasse er planlægningen slået fra.
    kalender_tenant_id: str | None = None
    kalender_client_id: str | None = None
    kalender_client_secret: SecretStr | None = None
    kalender_postkasse: str | None = None          # den kalender, masterafstemningerne bookes i
    kalender_book: bool = False                    # false = tørløb: vis hvad der ville bookes, men book intet
    kalender_arbejdsdag_fra: int = 8               # arbejdstid (hele timer, dansk tid) – der bookes kun inden for den
    kalender_arbejdsdag_til: int = 16
    # Timeregistreringens påmindelse kl. 16.05: false = tørløb (registreres, men sendes ikke).
    time_reminder_send: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
