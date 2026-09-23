"""Lille klient til e-conomics REST API (kun læsning).

Kilde til endpoints, headere og paginering: e-conomics JSON-skemaer
(restapi.e-conomic.com, gengivet i OpenAPI-form) og restdocs.e-conomic.com:
- Base-URL: https://restapi.e-conomic.com
- Headere: X-AppSecretToken, X-AgreementGrantToken, Content-Type: application/json
- Lister pagineres med `skipPages` og `pageSize` (højst 1000). Svaret har
  `collection` og `pagination`; `pagination.nextPage` er den færdige URL til
  næste side og mangler på sidste side.
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

from app.sikkerhed.hemmeligheder import HemmeligtToken

log = logging.getLogger(__name__)

MAKS_SIDESTOERRELSE = 1000
FORSOEG = 5


class EconomicFejl(Exception):
    """Fejl fra e-conomic. Beskeden indeholder aldrig nøgler."""


def _skal_proeves_igen(fejl: BaseException) -> bool:
    """Prøv igen ved netværksfejl, for mange kald (429) og fejl hos e-conomic (5xx)."""
    if isinstance(fejl, httpx.TransportError):
        return True
    if isinstance(fejl, httpx.HTTPStatusError):
        kode = fejl.response.status_code
        return kode == 429 or kode >= 500
    return False


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
            wait=vent or wait_exponential_jitter(initial=1, max=30),
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
            hint = {401: " – tjek nøglerne", 403: " – adgangen mangler rettigheder"}.get(kode, "")
            raise EconomicFejl(
                f"e-conomic svarede {kode} på {fejl.request.url.path}{hint}"
            ) from None
        except httpx.TransportError as fejl:
            raise EconomicFejl(
                f"Kunne ikke nå e-conomic efter {FORSOEG} forsøg ({type(fejl).__name__})"
            ) from None

    def hent_alle(self, sti: str) -> Iterator[dict]:
        """Hent alle rækker fra et liste-endpoint ved at følge `nextPage`."""
        url: str | None = sti
        params: dict | None = {"skipPages": 0, "pageSize": MAKS_SIDESTOERRELSE}
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
