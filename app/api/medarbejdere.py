"""Medarbejdere, der må logge ind – administreres fra websiden (/api/medarbejdere). KUN administratorer.

Samme regler som kommandoen `python -m app.personale.bruger`:
- Medarbejdere slettes aldrig. "Luk adgang" sætter aktiv = false, og adgangen lukkes med det samme
  (login slås op ved hver forespørgsel).
- E-mailen kan ikke ændres (Microsoft-kontoen bindes ved første login) – opret i stedet en ny.
- Man kan ikke ændre sin egen rolle eller lukke sin egen adgang, og der skal altid være mindst
  én aktiv administrator – så ingen kan låse alle ude.
- Alt logges i audit_log med, hvem der gjorde det.
"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin
from app.audit.models import AuditLog
from app.personale.models import STAFF_ROLLER, Staff

router = APIRouter(prefix="/api/medarbejdere", dependencies=[Depends(kraev_admin)])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _json(m: Staff) -> dict:
    return {"id": m.id, "navn": m.navn, "email": m.email, "rolle": m.rolle, "aktiv": m.aktiv,
            "forbundet_til_microsoft": m.microsoft_oid is not None,
            "sidst_logget_ind": m.sidst_logget_ind.isoformat() if m.sidst_logget_ind else None,
            "oprettet": m.oprettet.isoformat() if isinstance(m.oprettet, datetime) else None}


def _log(session: Session, af: Staff, handling: str, m: Staff) -> None:
    session.add(AuditLog(staff_id=af.id, handling=f"medarbejder_{handling}",
                         detaljer={"medarbejder_id": m.id, "email": m.email, "rolle": m.rolle,
                                   "aktiv": m.aktiv, "udfoert_af": af.email}))


@router.get("")
def liste(session: Session = Depends(db)) -> list[dict]:
    return [_json(m) for m in session.scalars(select(Staff).order_by(Staff.aktiv.desc(), Staff.navn))]


class NyMedarbejder(BaseModel):
    email: str = Field(max_length=255)
    navn: str = Field(min_length=1, max_length=255)
    rolle: str = "medarbejder"


@router.post("", dependencies=[Depends(kraev_header)])
def opret(data: NyMedarbejder, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    email = data.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status_code=422, detail="Det er ikke en gyldig e-mailadresse")
    if data.rolle not in STAFF_ROLLER:
        raise HTTPException(status_code=422, detail="Rollen skal være admin eller medarbejder")
    findes = session.scalars(select(Staff).where(Staff.email == email)).one_or_none()
    if findes is not None:
        raise HTTPException(status_code=409, detail=f"{email} findes allerede" +
                            ("" if findes.aktiv else " (adgangen er lukket – genåbn den i listen)"))
    m = Staff(email=email, navn=data.navn.strip(), rolle=data.rolle, aktiv=True)
    session.add(m)
    session.flush()
    _log(session, af, "opret", m)
    session.commit()
    return _json(m)


class Aendring(BaseModel):
    navn: str | None = Field(default=None, min_length=1, max_length=255)
    rolle: str | None = None
    aktiv: bool | None = None


@router.post("/{medarbejder_id}", dependencies=[Depends(kraev_header)])
def aendr(medarbejder_id: int, data: Aendring, af: Staff = Depends(kraev_admin),
          session: Session = Depends(db)) -> dict:
    m = session.get(Staff, medarbejder_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Medarbejderen findes ikke")
    if data.rolle is not None and data.rolle not in STAFF_ROLLER:
        raise HTTPException(status_code=422, detail="Rollen skal være admin eller medarbejder")
    mister_admin = m.rolle == "admin" and m.aktiv and (
        (data.rolle is not None and data.rolle != "admin") or data.aktiv is False)
    if m.id == af.id and mister_admin:
        raise HTTPException(status_code=409, detail="Du kan ikke fjerne din egen administratoradgang")
    if mister_admin and session.scalar(select(func.count()).select_from(Staff).where(
            Staff.rolle == "admin", Staff.aktiv.is_(True))) <= 1:
        raise HTTPException(status_code=409, detail="Der skal altid være mindst én aktiv administrator")
    handlinger = []
    if data.navn is not None and data.navn.strip() != m.navn:
        m.navn = data.navn.strip()
        handlinger.append("navn")
    if data.rolle is not None and data.rolle != m.rolle:
        m.rolle = data.rolle
        handlinger.append("rolle")
    if data.aktiv is not None and data.aktiv != m.aktiv:
        m.aktiv = data.aktiv
        handlinger.append("aktiver" if data.aktiv else "deaktiver")
    for h in handlinger:
        _log(session, af, h, m)
    session.commit()
    return _json(m)
