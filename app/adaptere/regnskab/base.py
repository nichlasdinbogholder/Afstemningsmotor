"""Fælles interface til regnskabssystemer: AccountingProvider.

Resten af systemet henter ALDRIG data direkte fra et regnskabssystem og ved
aldrig, hvilket system data kommer fra – det bruger kun AccountingProvider:

    with hent_adapter(session, client_id) as provider:
        provider.fetch_accounts()                -> list[Konto]       (kontoplan)
        provider.fetch_customers()               -> list[Kunde]
        provider.fetch_suppliers()               -> list[Leverandoer]
        provider.fetch_entries(efter)            -> PosteringsSvar    (kun nye siden `efter`)
        provider.fetch_open_entries()            -> list[AabenPost]   (altid alle)
        provider.fetch_journals()                -> list[Kassekladde]
        provider.fetch_journal_entries(nummer)   -> list[KladdePost]  (endnu ikke bogført)

Adapteren oversætter systemets felter til formatet herunder og gætter aldrig:
et felt, systemet ikke har leveret, bliver None.

En ny adapter (fx Dinero) lægges i sit eget modul, registreres med
@registrer_adapter("dinero") og tilføjes i ADAPTER_MODULER – intet andet ændres.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

# Moduler med adaptere. Dinero tilføjes her, når den findes.
ADAPTER_MODULER = ("app.adaptere.economic.adapter",)


# --- Fejl -------------------------------------------------------------------


class AdapterFejl(Exception):
    """Fejl fra et regnskabssystem. Beskeden indeholder aldrig nøgler."""


class ForMangeKald(AdapterFejl):
    """Systemet har bedt os vente (rate limit). Er IKKE en fejl – prøv igen senere."""

    taeller_ikke_som_fejl = True  # sync_state tæller den ikke med som fejl

    def __init__(self, besked: str, vent_sekunder: float = 60) -> None:
        super().__init__(besked)
        self.vent_sekunder = vent_sekunder


class DelvisHentet(AdapterFejl):
    """Hentningen stoppede midtvejs, men en del af posterne er sikre at gemme.

    `poster` er hentet før fejlen. `sikker_cursor` er det højeste bogmærke, hvor
    ALLE poster op til og med det er hentet (ellers det gamle bogmærke). Den
    oprindelige fejl ligger i `aarsag` og rejses igen, når det sikre er gemt.
    """

    def __init__(self, aarsag: Exception, poster: list, sikker_cursor: str | None,
                 cursor_type: str | None) -> None:
        super().__init__(f"Hentning stoppet midtvejs: {aarsag}")
        self.aarsag = aarsag
        self.poster = poster
        self.sikker_cursor = sikker_cursor
        self.cursor_type = cursor_type


class UkendtSystem(AdapterFejl):
    pass


# Posttyper i vores format (entries.entry_type). e-conomics typer bruges som
# fælles ordforråd; en Dinero-adapter oversætter sine typer hertil.
ENTRY_TYPER = (
    "customerInvoice", "customerPayment", "supplierInvoice", "supplierPayment",
    "financeVoucher", "reminder", "openingEntry", "transferredOpeningEntry",
    "systemEntry", "manualDebtorInvoice",
)


# Kontotyper og debet/kredit i vores format (accounts.kontotype/debet_kredit).
KONTOTYPER = (
    "profitAndLoss", "status", "totalFrom", "heading", "headingStart", "sumInterval", "sumAlpha",
)
DEBET_KREDIT = ("debit", "credit")


# --- Vores eget format ------------------------------------------------------


@dataclass(frozen=True)
class Konto:
    kontonummer: int
    navn: str
    kontotype: str | None
    debet_kredit: str | None
    momskode: str | None
    spaerret: bool | None
    direkte_posteringer_blokeret: bool | None
    saldo: Decimal | None
    kladdesaldo: Decimal | None
    # Systemets eget, uændrede svar – gemmes til opslag, må ALDRIG bruges af regler.
    raa_data: dict


@dataclass(frozen=True)
class Kunde:
    kundenummer: int
    navn: str
    cvr: str | None
    betalingsbetingelse: int | None
    spaerret: bool | None
    saldo: Decimal | None


@dataclass(frozen=True)
class Leverandoer:
    leverandoernummer: int
    navn: str
    cvr: str | None
    betalingsbetingelse: int | None
    saldo: Decimal | None


@dataclass(frozen=True)
class Postering:
    bogfoert_id: int
    bilagsnummer: int | None
    dato: date | None
    kontonummer: int | None
    tekst: str | None
    beloeb: Decimal | None
    modpart: str | None  # "debitor:<nr>" eller "kreditor:<nr>"
    valuta: str | None
    entry_type: str | None
    beloeb_dkk: Decimal | None = None  # beløbet i aftalens grundvaluta
    # Systemets eget, uændrede svar for posten – til senere brug, må ALDRIG bruges af regler.
    raa_data: dict | None = None


@dataclass(frozen=True)
class AabenPost:
    bogfoert_id: int
    type: str  # "debitor" eller "kreditor"
    partnummer: int
    partnavn: str | None
    bilagsnummer: int | None
    fakturanummer: str | None
    dato: date | None
    forfaldsdato: date | None
    beloeb: Decimal | None
    restbeloeb: Decimal
    valuta: str | None
    entry_type: str | None = None  # fx customerInvoice / customerPayment (ENTRY_TYPER)


@dataclass(frozen=True)
class Kassekladde:
    nummer: int
    navn: str | None


@dataclass(frozen=True)
class KladdePost:
    """En linje i en kassekladde – endnu IKKE bogført."""

    kladde_nummer: int
    linje_id: int | None
    bilagsnummer: int | None
    dato: date | None
    konto: int | None
    modkonto: int | None
    tekst: str | None
    beloeb: Decimal | None
    valuta: str | None
    entry_type: str | None


@dataclass(frozen=True)
class PosteringsSvar:
    poster: list[Postering]
    ny_cursor: str | None
    cursor_type: str | None


class AccountingProvider(Protocol):
    """Det fælles interface, som hver adapter (e-conomic, senere Dinero) implementerer."""

    system: str

    def fetch_accounts(self) -> list[Konto]: ...

    def fetch_customers(self) -> list[Kunde]: ...
    def fetch_suppliers(self) -> list[Leverandoer]: ...
    def fetch_entries(self, efter: str | None) -> PosteringsSvar: ...
    def fetch_open_entries(self) -> list[AabenPost]: ...
    def fetch_journals(self) -> list[Kassekladde]: ...
    def fetch_journal_entries(self, nummer: int) -> list[KladdePost]: ...
    def __enter__(self) -> "AccountingProvider": ...
    def __exit__(self, *args) -> None: ...


# --- Register ---------------------------------------------------------------

AdapterFabrik = Callable[[Session, int], AccountingProvider]
_FABRIKKER: dict[str, AdapterFabrik] = {}


def registrer_adapter(system: str) -> Callable[[AdapterFabrik], AdapterFabrik]:
    def registrer(fabrik: AdapterFabrik) -> AdapterFabrik:
        _FABRIKKER[system] = fabrik
        return fabrik

    return registrer


def _indlaes() -> None:
    for modul in ADAPTER_MODULER:
        importlib.import_module(modul)


def understoettede_systemer() -> list[str]:
    _indlaes()
    return sorted(_FABRIKKER)


def hent_adapter(session: Session, client_id: int) -> AccountingProvider:
    """Adapteren til kundens regnskabssystem (fra clients.regnskabssystem)."""
    from app.kunder.models import Client

    _indlaes()
    system = session.scalar(select(Client.regnskabssystem).where(Client.id == client_id))
    if system not in _FABRIKKER:
        raise UkendtSystem(f"Kunde {client_id}: intet understøttet regnskabssystem ({system})")
    return _FABRIKKER[system](session, client_id)
