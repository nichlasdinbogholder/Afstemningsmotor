"""Register over jobtyper: hvilken funktion hører til hvilken type.

En jobtype registreres sådan:

    from app.jobs.register import JobKontekst, jobtype

    @jobtype("min_type")
    def min_funktion(job: JobKontekst) -> None:
        ...

Funktionen får en `JobKontekst` med jobbets oplysninger og en databasesession.
Alt, hvad funktionen skriver med `job.session`, gemmes i SAMME transaktion,
som markerer jobbet som færdigt – enten sker begge dele, eller ingen af dem.
Et job kan blive kørt mere end én gang (fx hvis workeren dør), så funktionen
skal kunne tåle at blive gentaget.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

# Moduler med jobtyper. Tilføj nye moduler her, så workeren kender dem.
JOBTYPE_MODULER = ("app.jobs.typer",)


class UkendtJobtype(Exception):
    pass


@dataclass(frozen=True)
class JobKontekst:
    job_id: int
    type: str
    client_id: int | None
    payload: dict
    forsoeg: int
    max_forsoeg: int
    session: Session = field(repr=False)


JobFunktion = Callable[[JobKontekst], None]
_REGISTER: dict[str, JobFunktion] = {}


def jobtype(navn: str) -> Callable[[JobFunktion], JobFunktion]:
    """Registrér en funktion som jobtypen `navn`."""

    def registrer(funktion: JobFunktion) -> JobFunktion:
        eksisterende = _REGISTER.get(navn)
        if eksisterende is not None and eksisterende is not funktion:
            raise ValueError(f"Jobtypen '{navn}' er allerede registreret")
        _REGISTER[navn] = funktion
        return funktion

    return registrer


def afregistrer(navn: str) -> None:
    """Fjern en jobtype (bruges af tests)."""
    _REGISTER.pop(navn, None)


def indlaes_jobtyper() -> None:
    for modul in JOBTYPE_MODULER:
        importlib.import_module(modul)


def hent_funktion(navn: str) -> JobFunktion:
    indlaes_jobtyper()
    try:
        return _REGISTER[navn]
    except KeyError:
        raise UkendtJobtype(f"Ukendt jobtype '{navn}'") from None


def kendte_typer() -> list[str]:
    indlaes_jobtyper()
    return sorted(_REGISTER)
