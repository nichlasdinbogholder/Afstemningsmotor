"""Kundeoversigtens liste (Kundeoversigten, trin 4): standard "mine kunder", sorteret efter navn (bygherren 26.10.2026:
næste frist og dækningsgrad er ikke relevante i oversigten – dækningsgraden bruges i ledervisningen, trin 18).

Kolonner: kunde, kundenummer, CVR, ansvarlig og assistent, afregningsform (kun abonnement/timeafregning – ingen pris),
system og forbindelsesstatus (bygherren 26.10.2026):
- Månedens status og frister (kunde_tasks) vises IKKE her – de hører til under Controlling (bygherren 26.10.2026).
  `maanedens_status()` er bevaret til Controlling.
- DÆKNINGSGRAD = KUN for abonnementskunder, seneste HELE måned: (abonnementspris + timer uden for abonnementet ×
  salgspris − alle registrerede timer på kunden × medarbejderens kostpris) / indtægten. Satser slås op på hver
  arbejdsdag (time_salgspris/time_kostpris). Mangler en sats, vises "mangler sats" – aldrig et gættet tal.
- Bilagsdækning (andel af bankposteringer af typen finans med bilag; leverandørbetalinger tæller ikke) bygges i et
  selvstændigt trin – e-conomic sender ikke med på posteringen, om der er et bilag.
Filtre: mine, ansvarlig, afregningsform, system, livscyklus (onboarding/aktiv/pause), døde forbindelser,
Alt regnes her på serveren – webdelen viser kun. (Hvidvask er fjernet – bygherren 26.10.2026: Pingo Documents kan
ikke integreres.)

"Mine kunder" = kunder, hvor man er ansvarlig ELLER assistent (app/kunder/roller.py).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.kunder.kartotek import liste as kartotek_liste
from app.kunder.models import Client
from app.kunder.tabeller import KundeLedger, KundeTask
from app.personale.models import Staff

# Forbindelsens status pr. kunde = den dårligste af kundens regnskabsaftaler.
FORBINDELSE_RAEKKEFOELGE = ("afvist", "fejlet", "ikke_tilsluttet", "aktiv")
FORBINDELSE_TEKST = {"afvist": "Afvist", "fejlet": "Fejlet", "ikke_tilsluttet": "Ikke tilsluttet", "aktiv": "Aktiv",
                     None: "Ingen aftale"}
DOEDE = ("afvist", "fejlet")


@dataclass
class Filtre:
    mine: bool = False
    ansvarlig_id: int | None = None
    afregning: str | None = None          # abonnement | timeafregning
    system: str | None = None             # economic | dinero
    status: str | None = None             # aktiv | pause
    doede_forbindelser: bool = False


def _forbindelser(session: Session, ider: list[int]) -> dict[int, str]:
    statusser: dict[int, set[str]] = {}
    for client_id, status in session.execute(select(KundeLedger.client_id, KundeLedger.status).where(
            KundeLedger.client_id.in_(ider), KundeLedger.archived_at.is_(None))):
        statusser.setdefault(client_id, set()).add(status)
    ud = {}
    for client_id, s in statusser.items():
        ud[client_id] = next(x for x in FORBINDELSE_RAEKKEFOELGE if x in s)
    return ud


def maanedens_status(session: Session, ider: list[int], dag: date) -> dict[int, tuple[int, int]]:
    """(fuldførte, i alt) af denne måneds opgaver pr. kunde."""
    periode = dag.replace(day=1)
    raekker = session.execute(select(KundeTask.client_id, func.count(KundeTask.completed_at), func.count()).where(
        KundeTask.client_id.in_(ider), KundeTask.period == periode).group_by(KundeTask.client_id)).all()
    return {r[0]: (r[1], r[2]) for r in raekker}


def seneste_hele_maaned(dag: date) -> tuple[date, date]:
    slut = dag.replace(day=1) - timedelta(days=1)
    return slut.replace(day=1), slut


# Pr. kunde: abonnementsprisen (gældende den 1. i måneden), indtægt for timer uden for abonnementet, omkostning for
# alle timer – og om en sats mangler. Kun timer med en kunde; satserne slås op på arbejdsdagen.
DAEKNING_SQL = text("""
SELECT c.id,
       time_abonnementspris(c.id, :fra) AS pris,
       COALESCE(SUM(e.hours * time_salgspris(e.work_date)) FILTER (WHERE e.billable_separately), 0) AS saerskilt,
       COALESCE(SUM(e.hours * time_kostpris(e.employee_id, e.work_date)), 0) AS omkostning,
       BOOL_OR(e.billable_separately AND e.hours > 0 AND time_salgspris(e.work_date) IS NULL) AS mangler_salgspris,
       BOOL_OR(e.hours > 0 AND time_kostpris(e.employee_id, e.work_date) IS NULL) AS mangler_kostpris
FROM clients c
LEFT JOIN time_entries e ON e.client_id = c.id AND e.work_date BETWEEN :fra AND :til
WHERE c.id = ANY(:ider) AND time_afregning(c.id, :fra) = 'abonnement'
GROUP BY c.id
""")


def daekningsgrader(session: Session, ider: list[int], dag: date) -> dict[int, dict]:
    fra, til = seneste_hele_maaned(dag)
    ud = {}
    for r in session.execute(DAEKNING_SQL, {"ider": ider, "fra": fra, "til": til}):
        if r.mangler_kostpris or r.mangler_salgspris:
            ud[r.id] = {"procent": None, "tekst": "mangler sats"}
            continue
        indtaegt = (r.pris or Decimal(0)) + r.saerskilt
        if indtaegt <= 0:
            ud[r.id] = {"procent": None, "tekst": "ingen indtægt"}
            continue
        procent = ((indtaegt - r.omkostning) / indtaegt * 100).quantize(Decimal("0.1"))
        ud[r.id] = {"procent": str(procent), "tekst": None}
    return ud


def liste(session: Session, medarbejder: Staff, dag: date, f: Filtre) -> list[dict]:
    raekker = kartotek_liste(session, False, dag)
    ider = [r["id"] for r in raekker]
    kunder = {k.id: k for k in session.scalars(select(Client).where(Client.id.in_(ider)))}
    navne = dict(session.execute(select(Staff.id, Staff.navn)).all())
    forbindelser = _forbindelser(session, ider)

    ud = []
    for r in raekker:
        k = kunder[r["id"]]
        forbindelse = forbindelser.get(k.id)
        r = r | {
            "assistent": navne.get(k.assistent_id), "assistent_id": k.assistent_id,
            "system_kode": k.regnskabssystem,
            "forbindelse": forbindelse, "forbindelse_tekst": FORBINDELSE_TEKST[forbindelse],
            "min_kunde": medarbejder.id in (k.ansvarlig_medarbejder_id, k.assistent_id),
        }
        if f.mine and not r["min_kunde"]:
            continue
        if f.ansvarlig_id is not None and k.ansvarlig_medarbejder_id != f.ansvarlig_id:
            continue
        if f.afregning and r["afregning"] != f.afregning:
            continue
        if f.system and k.regnskabssystem != f.system:
            continue
        if f.status and k.status != f.status:
            continue
        if f.doede_forbindelser and forbindelse not in DOEDE:
            continue
        ud.append(r)
    ud.sort(key=lambda r: r["navn"].lower())
    return ud
