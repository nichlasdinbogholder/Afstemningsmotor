"""Medarbejdere, der må logge ind – administreres fra websiden (/api/medarbejdere). KUN administratorer.

Samme regler som kommandoen `python -m app.personale.bruger`:
- Medarbejdere slettes aldrig. "Luk adgang" sætter aktiv = false, og adgangen lukkes med det samme
  (login slås op ved hver forespørgsel).
- E-mailen kan ikke ændres (Microsoft-kontoen bindes ved første login) – opret i stedet en ny.
- Man kan ikke ændre sin egen rolle eller lukke sin egen adgang, og der skal altid være mindst
  én aktiv administrator – så ingen kan låse alle ude.
- Alt logges i audit_log med, hvem der gjorde det.
- Kostpris pr. time (staff_cost_rates) gemmes med den dato, den gælder fra – en ny overskriver aldrig den gamle.
  Kostpriser ses og rettes KUN af administratorer (hele routeren kræver admin).
"""

import re
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin
from app.audit.models import AuditLog
from app.personale.models import STAFF_ROLLER, Staff, StaffCostRate

router = APIRouter(prefix="/api/medarbejdere", dependencies=[Depends(kraev_admin)])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _gaeldende_kostpris(session: Session, staff_id: int, dag: date) -> StaffCostRate | None:
    return session.scalars(
        select(StaffCostRate).where(StaffCostRate.staff_id == staff_id, StaffCostRate.valid_from <= dag)
        .order_by(StaffCostRate.valid_from.desc())
    ).first()


def _kostpris_json(session: Session, m: Staff) -> dict:
    k = _gaeldende_kostpris(session, m.id, date.today())
    naeste = session.scalars(
        select(StaffCostRate).where(StaffCostRate.staff_id == m.id, StaffCostRate.valid_from > date.today())
        .order_by(StaffCostRate.valid_from)
    ).first()
    return {"kostpris_pr_time": str(k.hourly_cost) if k else None,
            "kostpris_fra": k.valid_from.isoformat() if k else None,
            "naeste_kostpris": ({"kostpris_pr_time": str(naeste.hourly_cost), "fra": naeste.valid_from.isoformat()}
                                if naeste else None)}


def _json(m: Staff, session: Session | None = None) -> dict:
    return ({} if session is None else _kostpris_json(session, m)) | {"id": m.id, "navn": m.navn, "email": m.email, "rolle": m.rolle, "aktiv": m.aktiv,
            "forbundet_til_microsoft": m.microsoft_oid is not None,
            "sidst_logget_ind": m.sidst_logget_ind.isoformat() if m.sidst_logget_ind else None,
            "oprettet": m.oprettet.isoformat() if isinstance(m.oprettet, datetime) else None}


def _log(session: Session, af: Staff, handling: str, m: Staff) -> None:
    session.add(AuditLog(staff_id=af.id, handling=f"medarbejder_{handling}",
                         detaljer={"medarbejder_id": m.id, "email": m.email, "rolle": m.rolle,
                                   "aktiv": m.aktiv, "udfoert_af": af.email}))


@router.get("")
def liste(session: Session = Depends(db)) -> list[dict]:
    return [_json(m, session) for m in session.scalars(select(Staff).order_by(Staff.aktiv.desc(), Staff.navn))]


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
    return _json(m, session)


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
    return _json(m, session)


@router.get("/{medarbejder_id}/kostpriser")
def kostpriser(medarbejder_id: int, session: Session = Depends(db)) -> list[dict]:
    """Alle kostpriser for medarbejderen, nyeste først (historikken)."""
    if session.get(Staff, medarbejder_id) is None:
        raise HTTPException(status_code=404, detail="Medarbejderen findes ikke")
    raekker = session.scalars(select(StaffCostRate).where(StaffCostRate.staff_id == medarbejder_id)
                              .order_by(StaffCostRate.valid_from.desc())).all()
    return [{"kostpris_pr_time": str(r.hourly_cost), "fra": r.valid_from.isoformat(),
             "lagt_ind_af": (session.get(Staff, r.created_by).navn if r.created_by else None),
             "lagt_ind": r.created_at.isoformat()} for r in raekker]


class NyKostpris(BaseModel):
    kostpris_pr_time: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    gyldig_fra: date


@router.post("/{medarbejder_id}/kostpris", dependencies=[Depends(kraev_header)])
def ny_kostpris(medarbejder_id: int, data: NyKostpris, af: Staff = Depends(kraev_admin),
                session: Session = Depends(db)) -> dict:
    """Ny kostpris fra en dato. Findes der allerede en fra samme dato, rettes den (fx en tastefejl) – logges."""
    m = session.get(Staff, medarbejder_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Medarbejderen findes ikke")
    findes = session.scalars(select(StaffCostRate).where(StaffCostRate.staff_id == m.id,
                                                         StaffCostRate.valid_from == data.gyldig_fra)).one_or_none()
    fra = str(findes.hourly_cost) if findes else None
    if findes:
        findes.hourly_cost, findes.created_by = data.kostpris_pr_time, af.id
    else:
        session.add(StaffCostRate(staff_id=m.id, hourly_cost=data.kostpris_pr_time, valid_from=data.gyldig_fra,
                                  created_by=af.id))
    session.add(AuditLog(staff_id=af.id, handling="medarbejder_kostpris", detaljer={
        "medarbejder_id": m.id, "email": m.email, "gyldig_fra": data.gyldig_fra.isoformat(),
        "fra": fra, "til": str(data.kostpris_pr_time), "udfoert_af": af.email}))
    session.commit()
    return _json(m, session)
