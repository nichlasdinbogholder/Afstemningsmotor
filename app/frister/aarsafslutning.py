"""Fristerne for ÅRSAFSLUTNINGEN, regnet ud fra kundens regnskabsår (bygherren 26.10.2026 – regeltabellen er godkendt).

Regnskabsåret kommer fra kundens regnskabssystem (accounting_years); når CVR-adgangen er på plads, bruges CVR til kontrol
(advarsel på fanen Stamdata). Alle selskaber er regnskabsklasse B.

To valg på fanen Frister (`kunde_obligations.aarsafslutning`):
- 'aarsregnskab_oplysningsskema':
    * Årsregnskab: 6 måneder efter regnskabsårets slutning (årsregnskabsloven, klasse B).
    * Oplysningsskema (selvangivelsen – tillæg til årsregnskabet): 6 måneder efter regnskabsårets slutning; slutter
      regnskabsåret 1. februar–31. marts, er fristen 1. august samme år (skattekontrolloven § 4, stk. 2).
- 'aarsopgoerelse':
    * Årsopgørelse: ALTID 20. maj i året efter det kalenderår, regnskabsåret slutter i (rykkes ikke).
Falder en frist (undtagen årsopgørelsen) på en lørdag, søndag eller helligdag, rykkes den til første hverdag efter.
"6 måneder efter" en månedsultimo er ultimo i måneden 6 måneder senere (31.12 → 30.06, 30.06 → 31.12).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.tid import helligdage

# Bankernes lukkedage (grundlovsdag, juleaften, nytårsaftensdag) er ikke helligdage – en frist rykkes ikke for dem.
IKKE_HELLIGDAGE = ((6, 5), (12, 24), (12, 31))


@dataclass
class Aarsfrist:
    hvad: str                 # aarsregnskab | oplysningsskema | aarsopgoerelse
    tekst: str
    frist: date


def er_hverdag(dag: date) -> bool:
    fri = {d for d in helligdage(dag.year) if (d.month, d.day) not in IKKE_HELLIGDAGE}
    return dag.weekday() < 5 and dag not in fri


def foerste_hverdag(dag: date) -> date:
    """Dagen selv, hvis den er en hverdag – ellers første hverdag efter."""
    while not er_hverdag(dag):
        dag += timedelta(days=1)
    return dag


def sidste_dag(aar: int, maaned: int) -> date:
    return date(aar + maaned // 12, maaned % 12 + 1, 1) - timedelta(days=1)


def plus_maaneder(dag: date, antal: int) -> date:
    """Samme dag `antal` måneder senere; fra en månedsultimo til månedsultimo."""
    maaned = dag.month - 1 + antal
    aar, maaned = dag.year + maaned // 12, maaned % 12 + 1
    if dag == sidste_dag(dag.year, dag.month):
        return sidste_dag(aar, maaned)
    return min(date(aar, maaned, 1) + timedelta(days=dag.day - 1), sidste_dag(aar, maaned))


def frister(valg: str | None, regnskabsaar_slut: date) -> list[Aarsfrist]:
    if valg == "aarsregnskab_oplysningsskema":
        if regnskabsaar_slut.month in (2, 3):
            oplysningsskema = date(regnskabsaar_slut.year, 8, 1)
        else:
            oplysningsskema = plus_maaneder(regnskabsaar_slut, 6)
        return [Aarsfrist("aarsregnskab", "Årsregnskab", foerste_hverdag(plus_maaneder(regnskabsaar_slut, 6))),
                Aarsfrist("oplysningsskema", "Oplysningsskema", foerste_hverdag(oplysningsskema))]
    if valg == "aarsopgoerelse":
        return [Aarsfrist("aarsopgoerelse", "Årsopgørelse", date(regnskabsaar_slut.year + 1, 5, 20))]
    return []
