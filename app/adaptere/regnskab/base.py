"""Fælles adapter-lag til regnskabssystemer (e-conomic nu, Dinero senere).

Resten af systemet henter ALDRIG data direkte fra et regnskabssystem – kun via
de fire funktioner på en adapter:

    with hent_adapter(session, client_id) as adapter:
        adapter.hent_customers()          -> list[Kunde]
        adapter.hent_suppliers()          -> list[Leverandoer]
        adapter.hent_entries(efter)       -> PosteringsSvar (kun nye siden `efter`)
        adapter.hent_open_entries()       -> list[AabenPost] (altid alle)

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


class UkendtSystem(AdapterFejl):
    pass


# Posttyper i vores format (entries.entry_type). e-conomics typer bruges som
# fælles ordforråd; en Dinero-adapter oversætter sine typer hertil.
ENTRY_TYPER = (
    "customerInvoice", "customerPayment", "supplierInvoice", "supplierPayment",
    "financeVoucher", "reminder", "openingEntry", "transferredOpeningEntry",
    "systemEntry", "manualDebtorInvoice",
)


# --- Vores eget format ------------------------------------------------------


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


@dataclass(frozen=True)
class PosteringsSvar:
    poster: list[Postering]
    ny_cursor: str | None
    cursor_type: str | None


class RegnskabsAdapter(Protocol):
    system: str

    def hent_customers(self) -> list[Kunde]: ...
    def hent_suppliers(self) -> list[Leverandoer]: ...
    def hent_entries(self, efter: str | None) -> PosteringsSvar: ...
    def hent_open_entries(self) -> list[AabenPost]: ...
    def __enter__(self) -> "RegnskabsAdapter": ...
    def __exit__(self, *args) -> None: ...


# --- Register ---------------------------------------------------------------

AdapterFabrik = Callable[[Session, int], RegnskabsAdapter]
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


def hent_adapter(session: Session, client_id: int) -> RegnskabsAdapter:
    """Adapteren til kundens regnskabssystem (fra clients.regnskabssystem)."""
    from app.kunder.models import Client

    _indlaes()
    system = session.scalar(select(Client.regnskabssystem).where(Client.id == client_id))
    if system not in _FABRIKKER:
        raise UkendtSystem(f"Kunde {client_id}: intet understøttet regnskabssystem ({system})")
    return _FABRIKKER[system](session, client_id)
