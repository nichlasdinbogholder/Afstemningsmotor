"""Kundeoversigtens liste (Kundeoversigten, trin 4): standard "mine kunder", sorteret efter nærmeste frist.

Kolonner: kunde og CVR, ansvarlig og assistent, afregningsform og månedspris, system og forbindelsesstatus, næste frist
med dage til. Bilagsdækning, månedens status og dækningsgrad afventer bygherrens definition (gættes ikke).
Filtre: mine, ansvarlig, afregningsform, system, livscyklus (aktiv/pause), forfaldne frister, døde forbindelser,
manglende hvidvask. Alt regnes her på serveren – webdelen viser kun.

"Mine kunder" = kunder, hvor man er ansvarlig ELLER assistent (app/kunder/roller.py).
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kunder.kartotek import liste as kartotek_liste
from app.kunder.models import Client
from app.kunder.tabeller import KundeAml, KundeLedger, KundeTask
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
    forfaldne: bool = False
    doede_forbindelser: bool = False
    mangler_hvidvask: bool = False


def _forbindelser(session: Session, ider: list[int]) -> dict[int, str]:
    statusser: dict[int, set[str]] = {}
    for client_id, status in session.execute(select(KundeLedger.client_id, KundeLedger.status).where(
            KundeLedger.client_id.in_(ider), KundeLedger.archived_at.is_(None))):
        statusser.setdefault(client_id, set()).add(status)
    ud = {}
    for client_id, s in statusser.items():
        ud[client_id] = next(x for x in FORBINDELSE_RAEKKEFOELGE if x in s)
    return ud


def _naeste_frister(session: Session, ider: list[int]) -> dict[int, date]:
    return dict(session.execute(select(KundeTask.client_id, func.min(KundeTask.due_date)).where(
        KundeTask.client_id.in_(ider), KundeTask.completed_at.is_(None)).group_by(KundeTask.client_id)).all())


def _hvidvask_ok(session: Session, ider: list[int]) -> set[int]:
    return set(session.scalars(select(KundeAml.client_id).where(
        KundeAml.client_id.in_(ider), KundeAml.risk_level.is_not(None), KundeAml.assessed_on.is_not(None))))


def liste(session: Session, medarbejder: Staff, dag: date, f: Filtre) -> list[dict]:
    raekker = kartotek_liste(session, False, dag)
    ider = [r["id"] for r in raekker]
    kunder = {k.id: k for k in session.scalars(select(Client).where(Client.id.in_(ider)))}
    navne = dict(session.execute(select(Staff.id, Staff.navn)).all())
    forbindelser = _forbindelser(session, ider)
    frister = _naeste_frister(session, ider)
    hvidvask = _hvidvask_ok(session, ider)

    ud = []
    for r in raekker:
        k = kunder[r["id"]]
        forbindelse = forbindelser.get(k.id)
        frist = frister.get(k.id)
        r = r | {
            "assistent": navne.get(k.assistent_id), "assistent_id": k.assistent_id,
            "system_kode": k.regnskabssystem,
            "forbindelse": forbindelse, "forbindelse_tekst": FORBINDELSE_TEKST[forbindelse],
            "naeste_frist": frist.isoformat() if frist else None,
            "dage_til_frist": (frist - dag).days if frist else None,
            "hvidvask_ok": k.id in hvidvask,
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
        if f.forfaldne and not (frist and frist < dag):
            continue
        if f.doede_forbindelser and forbindelse not in DOEDE:
            continue
        if f.mangler_hvidvask and r["hvidvask_ok"]:
            continue
        ud.append(r)
    # Nærmeste frist først (kunder uden frist sidst), derefter navn.
    ud.sort(key=lambda r: (r["naeste_frist"] is None, r["naeste_frist"] or "", r["navn"].lower()))
    return ud
