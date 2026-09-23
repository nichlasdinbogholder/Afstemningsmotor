"""Lille klient til e-conomics REST API (kun læsning).

Kilde til endpoints, headere og paginering: e-conomics JSON-skemaer
(restapi.e-conomic.com, gengivet i OpenAPI-form) og restdocs.e-conomic.com:
- Base-URL: https://restapi.e-conomic.com
- Headere: X-AppSecretToken, X-AgreementGrantToken, Content-Type: application/json
- Lister pagineres med `skipPages` og `pageSize` (højst 1000). Svaret har
  `collection` og `pagination`; `pagination.nextPage` er den færdige URL til
  næste side og mangler på sidste side.
- Filtre: `filter=felt$operator:værdi`, fx `entryNumber$gt:123` ($and kæder).
- For mange kald (429): vent (Retry-After, hvis e-conomic sender den) og prøv
  igen. Bliver det ved, rejses ForMangeKald, så jobbet udskydes – ikke fejler.
"""

import logging
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.adaptere.regnskab.base import AdapterFejl, ForMangeKald
from app.config import get_settings
from app.sikkerhed.hemmeligheder import HemmeligtToken

log = logging.getLogger(__name__)

MAKS_SIDESTOERRELSE = 1000
FORSOEG = 5


MAKS_RETRY_AFTER = 120  # sekunder – længere ventetid overlades til jobkøen


class EconomicFejl(AdapterFejl):
    """Fejl fra e-conomic. Beskeden indeholder aldrig nøgler."""


def app_secret_token() -> HemmeligtToken:
    """Vores fælles app-nøgle (X-AppSecretToken) fra .env."""
    vaerdi = get_settings().economic_app_secret_token
    if vaerdi is None or not vaerdi.get_secret_value():
        raise EconomicFejl("ECONOMIC_APP_SECRET_TOKEN mangler i .env")
    return HemmeligtToken(vaerdi.get_secret_value())


def _retry_after(svar: httpx.Response) -> float | None:
    """Sekunder e-conomic beder os vente (Retry-After-headeren), hvis den er sendt."""
    vaerdi = svar.headers.get("Retry-After")
    try:
        return max(float(vaerdi), 0.0) if vaerdi is not None else None
    except ValueError:
        return None


def _skal_proeves_igen(fejl: BaseException) -> bool:
    """Prøv igen ved netværksfejl, for mange kald (429) og fejl hos e-conomic (5xx)."""
    if isinstance(fejl, httpx.TransportError):
        return True
    if isinstance(fejl, httpx.HTTPStatusError):
        kode = fejl.response.status_code
        return kode == 429 or kode >= 500
    return False


class _VentRespekterRetryAfter:
    """Ventetid mellem forsøg: e-conomics Retry-After, ellers eksponentiel med tilfældighed."""

    def __init__(self, reserve) -> None:
        self._reserve = reserve

    def __call__(self, tilstand) -> float:
        fejl = tilstand.outcome.exception() if tilstand.outcome else None
        if isinstance(fejl, httpx.HTTPStatusError) and fejl.response.status_code == 429:
            sekunder = _retry_after(fejl.response)
            if sekunder is not None and sekunder <= MAKS_RETRY_AFTER:
                return sekunder
        return self._reserve(tilstand)


class EconomicKlient:
    def __init__(
        self,
        app_secret_token: HemmeligtToken,
        agreement_grant_token: HemmeligtToken,
        base_url: str = "https://restapi.e-conomic.com",
        transport: httpx.BaseTransport | None = None,
        vent=None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._vaert = urlsplit(self._base_url).netloc
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={
                # Nøglerne bruges KUN her, direkte i kaldet til e-conomic.
                "X-AppSecretToken": app_secret_token.klartekst(),
                "X-AgreementGrantToken": agreement_grant_token.klartekst(),
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
            transport=transport,
            follow_redirects=False,
        )
        self._forsoeg = Retrying(
            retry=retry_if_exception(_skal_proeves_igen),
            wait=vent or _VentRespekterRetryAfter(wait_exponential_jitter(initial=1, max=30)),
            stop=stop_after_attempt(FORSOEG),
            reraise=True,
            before_sleep=lambda f: log.warning(
                "e-conomic-kald fejlede (forsøg %s af %s) – prøver igen",
                f.attempt_number, FORSOEG,
            ),
        )

    def __enter__(self) -> "EconomicKlient":
        return self

    def __exit__(self, *_) -> None:
        self._http.close()

    def _hent_side(self, url: str, params: dict | None) -> dict:
        def kald() -> dict:
            svar = self._http.get(url, params=params)
            svar.raise_for_status()
            return svar.json()

        try:
            return self._forsoeg(kald)
        except httpx.HTTPStatusError as fejl:
            kode = fejl.response.status_code
            if kode == 429:
                raise ForMangeKald(
                    f"e-conomic: for mange kald på {fejl.request.url.path} – prøver igen senere",
                    vent_sekunder=_retry_after(fejl.response) or 60,
                ) from None
            hint = {401: " – tjek nøglerne", 403: " – adgangen mangler rettigheder"}.get(kode, "")
            raise EconomicFejl(
                f"e-conomic svarede {kode} på {fejl.request.url.path}{hint}"
            ) from None
        except httpx.TransportError as fejl:
            raise EconomicFejl(
                f"Kunne ikke nå e-conomic efter {FORSOEG} forsøg ({type(fejl).__name__})"
            ) from None

    def hent_alle(self, sti: str, filter: str | None = None) -> Iterator[dict]:
        """Hent alle rækker fra et liste-endpoint ved at følge `nextPage`."""
        url: str | None = sti
        params: dict | None = {"skipPages": 0, "pageSize": MAKS_SIDESTOERRELSE}
        if filter:
            params["filter"] = filter
        forventet = None
        hentet = 0
        while url:
            side = self._hent_side(url, params)
            params = None  # nextPage indeholder allerede alle parametre
            for raekke in side.get("collection", []):
                hentet += 1
                yield raekke
            paginering = side.get("pagination", {})
            forventet = paginering.get("results", forventet)
            url = paginering.get("nextPage")
            if url and urlsplit(url).netloc != self._vaert:
                # Send aldrig nøglerne til en anden adresse end e-conomic.
                raise EconomicFejl("nextPage peger uden for e-conomic – stopper")
        if forventet is not None and hentet != forventet:
            raise EconomicFejl(
                f"Hentede {hentet} rækker, men e-conomic oplyste {forventet}"
            )
