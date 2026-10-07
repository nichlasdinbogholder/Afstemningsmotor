"""Rammen for afstemningsregler.

En regel er ÉN fil i app/rules/ med en klasse, der har `code`, `version`, `name_da`
og `run(...)`, markeret med `@registrer_regel`. Filen tilføjes i REGEL_MODULER
nedenfor – mere skal der ikke til.

En regel FORESLÅR fund. Den retter intet, bogfører intet og taler aldrig med et
regnskabssystem – den læser kun vores egen database (fx tabellen entries).
"""

import hashlib
import importlib
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy.orm import Session

# Moduler med regler. Ny regel: tilføj ét modulnavn her.
REGEL_MODULER = (
    "app.rules.duplicate_entries",
    "app.rules.kontoudtog",
)


@dataclass
class FindingDraft:
    """Et fund, som reglen foreslår. Skrives til findings af app.rules.koersel."""

    fingerprint: str
    severity: str  # 'low' | 'medium' | 'high'
    title: str     # én linje på dansk til en medarbejder
    detail: dict   # tal og felter, reglen byggede på
    entry_ids: list[int]
    period_start: date | None = None
    period_end: date | None = None


class Rule(Protocol):
    code: str
    version: int
    name_da: str  # vises for medarbejderen

    def run(self, session: Session, client_id: int, since: date | None) -> list[FindingDraft]: ...


def fingerprint(external_ids: list[str]) -> str:
    """Stabilt aftryk af de involverede posteringer: samme poster giver altid samme
    aftryk – uanset rækkefølge og uanset hvornår reglen køres."""
    joined = "|".join(sorted(str(i) for i in external_ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


_REGLER: dict[str, Rule] = {}


def registrer_regel(klasse):
    """Dekorator: registrér en regel under dens `code`."""
    regel = klasse()
    if regel.code in _REGLER:
        raise ValueError(f"Reglen '{regel.code}' er registreret to gange")
    _REGLER[regel.code] = regel
    return klasse


def alle_regler() -> list[Rule]:
    for modul in REGEL_MODULER:
        importlib.import_module(modul)
    return [_REGLER[k] for k in sorted(_REGLER)]


def hent_regel(code: str) -> Rule:
    for regel in alle_regler():
        if regel.code == code:
            return regel
    raise KeyError(f"Ukendt regel: {code}")
