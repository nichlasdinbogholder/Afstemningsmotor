"""Rentelovens grænser og morarenteberegningen – ét sted.

Grænserne er IKKE indstillinger. De står her som konstanter, håndhæves af koden her, og de
vigtigste er desuden låst i databasen (CHECK-regler og triggeren `kontroller_rykker`), så de
heller ikke kan omgås af anden kode:

- Rykkergebyr: højst 100 kr. pr. skrivelse                       (renteloven § 9 b, stk. 1)
- Højst 3 rykkere pr. ydelse                                     (§ 9 b, stk. 1)
- Mindst 10 dage mellem rykkerne                                 (§ 9 b, stk. 1)
- Morarente: Nationalbankens udlånsrente pr. 1. januar / 1. juli
  + 8 procentpoint, pr. dag på det udestående beløb fra forfaldsdagen   (§ 5)
- Kompensationsbeløb kun i erhvervsforhold (§ 9 b, stk. 2), beløbet fastsat af justitsministeren.
  Opkræves én gang pr. fordring – her på første rykker.
- Inkasso: karensperioden efter 3. rykker er den SAMME for alle kunder (forretningsbeslutning
  07.10.2026: ingen kan udskyde eller fremskynde en enkelt sag).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.opkraevning.models import ReferenceRate

MAKS_RYKKERGEBYR = Decimal("100.00")
MAKS_RYKKERE = 3
MIN_DAGE_MELLEM_RYKKERE = 10
RENTETILLAEG_PROCENTPOINT = Decimal("8")
# Kompensationsbeløbet er fastsat af justitsministeren (pt. 310 kr.). Ændres beløbet, rettes det HER
# – og tidligere rykkere beholder det beløb, de blev sendt med.
KOMPENSATIONSBELOEB = Decimal("310.00")
INKASSO_KARENS_DAGE = 10
# Morarente pr. dag = årlig sats / 365 (faktiske dage / 365).
DAGE_PR_AAR = Decimal("365")

OERE = Decimal("0.01")


class LovgraenseFejl(Exception):
    """Et forsøg på at gøre noget, renteloven ikke tillader. Skal aldrig fanges og ignoreres."""


class ManglerReferencesats(Exception):
    """Der er ingen referencesats for perioden. Motoren gætter ALDRIG og bruger ALDRIG sidste kendte."""


def halvaar_start(dag: date) -> date:
    """Den 1. januar eller 1. juli, som satsen for `dag` gælder fra."""
    return date(dag.year, 1 if dag.month < 7 else 7, 1)


def referencesats(session: Session, dag: date) -> Decimal:
    """Udlånsrenten for halvåret, `dag` ligger i. Fejler højlydt, hvis den ikke er sat."""
    fra = halvaar_start(dag)
    sats = session.scalar(select(ReferenceRate.rate).where(ReferenceRate.valid_from == fra))
    if sats is None:
        raise ManglerReferencesats(
            f"Der er ingen referencesats for halvåret fra {fra:%d.%m.%Y}. Sæt den med: "
            f"python -m app.opkraevning.referencesats saet --fra {fra.isoformat()} --sats <procent> --kilde ...")
    return Decimal(sats)


def morarentesats(session: Session, dag: date) -> Decimal:
    """Årlig morarente i procent for `dag`: referencesats + 8 procentpoint."""
    return referencesats(session, dag) + RENTETILLAEG_PROCENTPOINT


@dataclass(frozen=True)
class Renteperiode:
    fra: date          # første rentedag
    til: date          # sidste rentedag
    dage: int
    hovedstol: Decimal
    sats: Decimal      # årlig, i procent
    rente: Decimal     # ikke afrundet


def beregn_morarente(session: Session, forfaldsdato: date, til_dato: date,
                     hovedstol_bevaegelser: list[tuple[date, Decimal]], start_hovedstol: Decimal
                     ) -> tuple[Decimal, list[Renteperiode]]:
    """Morarente pr. dag på det til enhver tid udestående beløb.

    Rentedagene er dagene EFTER forfaldsdagen til og med `til_dato` (forfald 15/9, betalt 25/9 =
    10 rentedage). `hovedstol_bevaegelser` er (dato, ændring) – fx (25/9, -5000) for en betaling på
    hovedstolen; ændringen gælder fra og med den dato. Satsen skifter 1. januar og 1. juli.
    Returnerer renten afrundet til øre og perioderne bag beregningen (til dokumentation på rykkeren).
    """
    if til_dato <= forfaldsdato:
        return Decimal("0.00"), []
    bevaegelser = sorted(hovedstol_bevaegelser)
    perioder: list[Renteperiode] = []
    dag = forfaldsdato + timedelta(days=1)
    hovedstol = start_hovedstol + sum((b for d, b in bevaegelser if d < dag), Decimal("0"))
    while dag <= til_dato:
        # Periodens slutning: dagen før næste skift i hovedstol eller sats – eller til_dato.
        naeste_skift = [d for d, _ in bevaegelser if d > dag]
        naeste_halvaar = date(dag.year, 7, 1) if dag.month < 7 else date(dag.year + 1, 1, 1)
        slut = min([til_dato] + [d - timedelta(days=1) for d in naeste_skift] + [naeste_halvaar - timedelta(days=1)])
        dage = (slut - dag).days + 1
        sats = morarentesats(session, dag)
        rente = max(hovedstol, Decimal("0")) * sats / Decimal("100") * dage / DAGE_PR_AAR
        perioder.append(Renteperiode(dag, slut, dage, hovedstol, sats, rente))
        dag = slut + timedelta(days=1)
        hovedstol += sum((b for d, b in bevaegelser if d == dag), Decimal("0"))
    total = sum((p.rente for p in perioder), Decimal("0")).quantize(OERE, rounding=ROUND_HALF_UP)
    return total, perioder


def kontroller_rykkergebyr(beloeb: Decimal) -> None:
    if beloeb < 0 or beloeb > MAKS_RYKKERGEBYR:
        raise LovgraenseFejl(f"Rykkergebyr {beloeb} kr. er ikke tilladt (højst {MAKS_RYKKERGEBYR} kr.)")


def kontroller_rykkernummer(nr_i_alt: int) -> None:
    if nr_i_alt > MAKS_RYKKERE:
        raise LovgraenseFejl(f"Rykker nr. {nr_i_alt} er ikke tilladt (højst {MAKS_RYKKERE} pr. faktura)")


def kontroller_interval(forrige: date | None, ny: date) -> None:
    if forrige is not None and ny < forrige + timedelta(days=MIN_DAGE_MELLEM_RYKKERE):
        raise LovgraenseFejl(f"Kun {(ny - forrige).days} dage siden forrige rykker "
                             f"(mindst {MIN_DAGE_MELLEM_RYKKERE})")


def kontroller_kompensation(beloeb: Decimal, erhverv: bool) -> None:
    if beloeb > 0 and not erhverv:
        raise LovgraenseFejl("Kompensationsbeløb kan kun opkræves i erhvervsforhold")
    if beloeb not in (Decimal("0"), KOMPENSATIONSBELOEB):
        raise LovgraenseFejl(f"Kompensationsbeløbet er fastsat til {KOMPENSATIONSBELOEB} kr.")
