"""Medarbejdere, der må logge ind – administreres fra websiden (/api/medarbejdere). KUN administratorer.

Samme regler som kommandoen `python -m app.personale.bruger`:
- Medarbejdere slettes aldrig. "Luk adgang" sætter aktiv = false, og adgangen lukkes med det samme
  (login slås op ved hver forespørgsel).
- E-mailen kan ikke ændres (Microsoft-kontoen bindes ved første login) – opret i stedet en ny.
- Man kan ikke ændre sin egen rolle eller lukke sin egen adgang, og der skal altid være mindst
  én aktiv administrator – så ingen kan låse alle ude.
- Alt logges i audit_log med, hvem der gjorde det.
- Invitation (app/personale/invitation.py): en velkomstmail fra info@ sendes, når medarbejderen oprettes, og kan
  sendes igen med `POST /api/medarbejdere/{id}/invitation`. Oprettelsen lykkes, også hvis mailen ikke kan sendes.
- Timeregistreringens stamdata (app/timeregistrering/stamdata.py): ansættelsesform, flexjob og satser (normtid,
  kostpris pr. time og produktive timer pr. år) med gyldig-fra-dato. En ny sats overskriver aldrig den gamle.
  Kostpriser ses og rettes KUN af administratorer (hele routeren kræver admin). Ændringer logges i time_audit.
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
from app.personale import invitation
from app.personale.models import STAFF_ROLLER, Staff
from app.timeregistrering import stamdata

router = APIRouter(prefix="/api/medarbejdere", dependencies=[Depends(kraev_admin)])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _sats_json(r) -> dict | None:
    if r is None:
        return None
    return {"fra": r.valid_from.isoformat(), "til": r.valid_to.isoformat() if r.valid_to else None,
            "normtid": None if r.weekly_norm is None else str(r.weekly_norm), "kostpris_pr_time": str(r.cost_per_hour),
            "produktive_timer_aar": str(r.productive_hours_year), "note": r.note}


def _tid_json(session: Session, m: Staff) -> dict:
    """Timeregistreringens oplysninger. Kostpriser, der lå her før timeregistreringen, står i 'tidligere_kostpris',
    indtil der er tastet en sats (de kunne ikke flyttes automatisk)."""
    e = stamdata.ansat(session, m.id)
    i_dag = date.today()
    tidligere = None
    if e is None or not stamdata.satser(session, e.id):
        log = session.scalars(select(AuditLog).where(AuditLog.handling == "kostpris_flyttet",
                                                     AuditLog.staff_id == m.id).order_by(AuditLog.id.desc())).first()
        if log is not None:
            tidligere = {"kostpris_pr_time": log.detaljer.get("kostpris_pr_time"), "fra": log.detaljer.get("gyldig_fra")}
    return {"ansaettelse": e.employment if e else None, "flexjob": e.flexjob if e else False,
            "afspadsering": e.accrues_toil if e else None, "loenref": e.payroll_ref if e else None,
            "sats": _sats_json(stamdata.sats_paa(session, e.id, i_dag)) if e else None,
            "naeste_sats": _sats_json(stamdata.naeste_sats(session, e.id, i_dag)) if e else None,
            "tidligere_kostpris": tidligere}


def _json(m: Staff, session: Session | None = None) -> dict:
    return ({} if session is None else {"tid": _tid_json(session, m)}) | {"id": m.id, "navn": m.navn, "email": m.email, "rolle": m.rolle, "aktiv": m.aktiv,
            "forbundet_til_microsoft": m.microsoft_oid is not None,
            "sidst_logget_ind": m.sidst_logget_ind.isoformat() if m.sidst_logget_ind else None,
            "invitation": None if session is None else invitation.seneste(session, m.id),
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
    # Først når medarbejderen er gemt, sendes velkomstmailen – går den galt, kan den sendes igen fra listen.
    resultat = invitation.send_invitation(session, m, af)
    session.commit()
    return _json(m, session) | {"invitation_sendt": resultat}


@router.post("/{medarbejder_id}/invitation", dependencies=[Depends(kraev_header)])
def send_igen(medarbejder_id: int, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    """Send velkomstmailen igen (fx hvis den ikke kan findes i Outlook)."""
    m = session.get(Staff, medarbejder_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Medarbejderen findes ikke")
    if not m.aktiv:
        raise HTTPException(status_code=409, detail="Adgangen er lukket – genåbn den, før du sender en invitation")
    resultat = invitation.send_invitation(session, m, af)
    session.commit()
    if resultat["status"] == "ikke_slaaet_til":
        raise HTTPException(status_code=503, detail="Invitationen kan ikke sendes endnu: udgående mail fra "
                                                    "info@dinbogholder.dk er ikke slået til")
    if resultat["status"] == "fejl":
        raise HTTPException(status_code=502, detail=f"Invitationen kunne ikke sendes: {resultat['fejl']}")
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


def _medarbejder(session: Session, medarbejder_id: int) -> Staff:
    m = session.get(Staff, medarbejder_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Medarbejderen findes ikke")
    return m


class Ansaettelse(BaseModel):
    ansaettelse: str
    flexjob: bool = False
    loenref: str | None = Field(default=None, max_length=100)


@router.post("/{medarbejder_id}/ansaettelse", dependencies=[Depends(kraev_header)])
def ansaettelse(medarbejder_id: int, data: Ansaettelse, af: Staff = Depends(kraev_admin),
                session: Session = Depends(db)) -> dict:
    m = _medarbejder(session, medarbejder_id)
    stamdata.saet_aktoer(session, af.id)
    try:
        stamdata.saet_ansaettelse(session, m.id, data.ansaettelse, data.flexjob, data.loenref)
    except stamdata.StamdataFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return _json(m, session)


@router.get("/{medarbejder_id}/satser")
def satser(medarbejder_id: int, session: Session = Depends(db)) -> list[dict]:
    """Alle satser for medarbejderen, nyeste først (historikken)."""
    m = _medarbejder(session, medarbejder_id)
    e = stamdata.ansat(session, m.id)
    return [] if e is None else [_sats_json(r) | {"lagt_ind_af": session.get(Staff, r.created_by).navn
                                                  if r.created_by else None, "lagt_ind": r.created_at.isoformat()}
                                 for r in stamdata.satser(session, e.id)]


class NySats(BaseModel):
    gyldig_fra: date
    normtid: Decimal | None = Field(default=None, ge=0, le=168, max_digits=5, decimal_places=2)
    kostpris_pr_time: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    produktive_timer_aar: Decimal = Field(gt=0, max_digits=7, decimal_places=2)
    note: str | None = Field(default=None, max_length=500)


@router.post("/{medarbejder_id}/sats", dependencies=[Depends(kraev_header)])
def ny_sats(medarbejder_id: int, data: NySats, af: Staff = Depends(kraev_admin),
            session: Session = Depends(db)) -> dict:
    """Ny sats fra en dato (samme dato som en eksisterende = rettelse). Kræver, at ansættelsesformen er sat."""
    m = _medarbejder(session, medarbejder_id)
    e = stamdata.ansat(session, m.id)
    if e is None:
        raise HTTPException(status_code=422, detail="Vælg først ansættelsesform for medarbejderen")
    stamdata.saet_aktoer(session, af.id)
    try:
        stamdata.ny_sats(session, e, data.gyldig_fra, data.normtid, data.kostpris_pr_time,
                         data.produktive_timer_aar, data.note, af.id)
    except stamdata.StamdataFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return _json(m, session)
