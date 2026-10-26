"""Fanen Aftale: det underskrevne aftalegrundlag med kunden som PDF (bygherren 26.10.2026).

- Kun PDF (filen skal starte med %PDF-), højst MAKS_STOERRELSE. Gemmes i databasen (kunde_aftaledokumenter).
- En ny version arkiverer den gamle – intet slettes, og et uploadet dokument kan aldrig ændres (håndhævet i databasen).
- Kun kundens ansvarlige, assistent og administratorer uploader (samme regel som kontaktpersoner). Alle kan se.
- ALARM: mangler der et gældende aftalegrundlag, får fanen en alarmklokke (`mangler_aftale`). Interne kunder
  (afregningsform 'intern', fx Din Bogholder selv) har ingen aftale med os og får ingen alarm.
- Alt skrives i kundens log (kunde_log).
"""

import hashlib
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.kunder.kontakter import maa_rette
from app.kunder.models import Client
from app.kunder.tabeller import KundeAftaleDokument, KundeLog
from app.personale.models import Staff
from app.tid import TIDSZONE

MAKS_STOERRELSE = 20 * 1024 * 1024          # 20 MB


class AftaleFejl(Exception):
    """Fejl ved upload. Beskeden er dansk og kan vises direkte."""


class IkkeTilladt(Exception):
    pass


def gaeldende(session: Session, client_id: int) -> KundeAftaleDokument | None:
    return session.scalars(select(KundeAftaleDokument).where(
        KundeAftaleDokument.client_id == client_id, KundeAftaleDokument.arkiveret.is_(None))).first()


def mangler_aftale(session: Session, kunde: Client, dag: date) -> bool:
    if session.scalar(text("SELECT time_afregning(:k, :d)"), {"k": kunde.id, "d": dag}) == "intern":
        return False
    return gaeldende(session, kunde.id) is None


def json(session: Session, d: KundeAftaleDokument) -> dict:
    af = session.get(Staff, d.uploadet_af)
    return {"id": d.id, "filnavn": d.filnavn, "stoerrelse": d.stoerrelse, "uploadet": d.uploadet.isoformat(),
            "uploadet_af": af.navn if af else None, "arkiveret": d.arkiveret.isoformat() if d.arkiveret else None}


def oversigt(session: Session, kunde: Client, medarbejder: Staff, dag: date) -> dict:
    alle = list(session.scalars(select(KundeAftaleDokument).where(KundeAftaleDokument.client_id == kunde.id)
                                .order_by(KundeAftaleDokument.uploadet.desc())))
    aktuel = next((d for d in alle if d.arkiveret is None), None)
    return {"gaeldende": json(session, aktuel) if aktuel else None,
            "tidligere": [json(session, d) for d in alle if d.arkiveret is not None],
            "mangler": mangler_aftale(session, kunde, dag), "kan_uploade": maa_rette(kunde, medarbejder)}


def upload(session: Session, af: Staff, client_id: int, filnavn: str, indhold: bytes) -> KundeAftaleDokument:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise AftaleFejl("Kunden findes ikke")
    if not maa_rette(kunde, af):
        raise IkkeTilladt()
    filnavn = (filnavn or "").strip().replace("/", "_").replace("\\", "_")[:255]
    if not indhold:
        raise AftaleFejl("Filen er tom")
    if len(indhold) > MAKS_STOERRELSE:
        raise AftaleFejl(f"Filen er for stor – højst {MAKS_STOERRELSE // (1024 * 1024)} MB")
    if not indhold.startswith(b"%PDF-"):
        raise AftaleFejl("Aftalegrundlaget skal være en PDF-fil")
    if not filnavn.lower().endswith(".pdf"):
        filnavn = (filnavn or "aftalegrundlag") + ".pdf"
    gammel = gaeldende(session, kunde.id)
    if gammel is not None:
        gammel.arkiveret = datetime.now(TIDSZONE)
        session.flush()
    d = KundeAftaleDokument(client_id=kunde.id, filnavn=filnavn, indhold=indhold, stoerrelse=len(indhold),
                            sha256=hashlib.sha256(indhold).hexdigest(), uploadet_af=af.id)
    session.add(d)
    session.add(KundeLog(client_id=kunde.id, event="aftale_uploadet", actor_staff_id=af.id,
                         detail=filnavn + (" (erstatter tidligere version)" if gammel else "")))
    session.flush()
    return d
