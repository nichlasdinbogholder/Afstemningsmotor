"""Timeregistreringen under /api/tid (login krævet) – medarbejderens EGNE timer.

- `GET  /api/tid/uge?dato=ÅÅÅÅ-MM-DD`  ugen (mandag–søndag), dens linjer, kunderne og opgavetyperne til valg.
- `GET  /api/tid/afregning?kunde=&dato=`  kundens afregningsform på datoen (vises på en ny linje, før den gemmes).
- `POST /api/tid/linje`  gem én linje (ny eller rettet). `POST /api/tid/linje/slet`  slet en linje.
Al logik ligger i app/timeregistrering/registrering.py. Ændringer kræver headeren X-Afstemning.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, nuvaerende_medarbejder
from app.kunder.models import Client
from app.personale.models import Staff
from app.timeregistrering import registrering as reg
from app.timeregistrering.models import TimeTaskType
from app.timeregistrering.satser import ManglerSats, satser_paa
from app.timeregistrering.stamdata import ansat

router = APIRouter(prefix="/api/tid", dependencies=[Depends(nuvaerende_medarbejder)])
IKKE_SAT_OP = "Du er ikke sat op til timeregistrering endnu – en administrator skal vælge din ansættelsesform"


def _ansat(session: Session, m: Staff):
    e = ansat(session, m.id)
    if e is None:
        raise HTTPException(status_code=409, detail=IKKE_SAT_OP)
    return e


@router.get("/uge")
def uge(dato: date, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    e = _ansat(session, m)
    u = reg.find_uge(session, e, dato)
    aar, nr, _ = dato.isocalendar()
    opgavetyper = [{"kode": t.code, "navn": t.name, "art": t.kind, "nul_timer": t.zero_hours}
                   for t in session.scalars(select(TimeTaskType).where(TimeTaskType.active.is_(True))
                                            .order_by(TimeTaskType.sort_order))]
    kunder = [{"id": k.id, "navn": k.navn, "kundenummer": k.kundenummer}
              for k in session.scalars(select(Client).where(Client.status == "aktiv").order_by(Client.navn))]
    return {
        "aar": aar, "uge": nr, "dage": [d.isoformat() for d in reg.ugens_dage(dato)],
        "status": u.status if u else "kladde",
        "kan_rettes": (u.status if u else "kladde") in reg.KAN_RETTES,
        "afvisning": u.rejection_note if u and u.status == "afvist" else None,
        "linjer": reg.ugens_linjer(session, u) if u else [],
        "i_alt": reg.dagstotaler(session, u, reg.ugens_dage(dato)),
        "opgavetyper": opgavetyper, "kunder": kunder,
    }


@router.get("/afregning")
def afregning(kunde: int, dato: date, session: Session = Depends(db)) -> dict:
    try:
        return {"afregning": satser_paa(session, dato, client_id=kunde).afregning}
    except ManglerSats:
        return {"afregning": None}


class Linje(BaseModel):
    dato: date                                   # en dag i ugen
    ids: list[int] = Field(default_factory=list, max_length=7)
    opgavetype: str = Field(max_length=50)
    kunde_id: int | None = None
    beskrivelse: str | None = Field(default=None, max_length=1000)
    saerskilt: bool = False
    timer: dict[date, str] = Field(default_factory=dict)   # dato -> timer som tekst, fx "1,25"


@router.post("/linje", dependencies=[Depends(kraev_header)])
def gem(data: Linje, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    e = _ansat(session, m)
    # Alt eller intet: fejler noget, rulles hele linjen tilbage (savepoint).
    try:
        with session.begin_nested():
            timer = {d: reg.timer(v) for d, v in data.timer.items() if str(v).strip() != ""}
            reg.gem_linje(session, e, m.id, data.dato, data.ids,
                          reg.Linje(data.opgavetype, data.kunde_id, data.beskrivelse, data.saerskilt), timer)
    except reg.RegistreringsFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    except DBAPIError as fejl:
        raise HTTPException(status_code=422, detail=_databasefejl(fejl)) from None
    session.commit()
    return uge(data.dato, m, session)


class Slet(BaseModel):
    dato: date
    ids: list[int] = Field(min_length=1, max_length=7)


@router.post("/linje/slet", dependencies=[Depends(kraev_header)])
def slet(data: Slet, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    e = _ansat(session, m)
    try:
        with session.begin_nested():
            reg.slet_linje(session, e, m.id, data.dato, data.ids)
    except reg.RegistreringsFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    except DBAPIError as fejl:
        raise HTTPException(status_code=422, detail=_databasefejl(fejl)) from None
    session.commit()
    return uge(data.dato, m, session)


def _databasefejl(fejl: DBAPIError) -> str:
    """Databasens egne regler (triggere) har danske beskeder – de vises. Alt andet bliver en kort, generel besked."""
    diag = getattr(getattr(fejl, "orig", None), "diag", None)
    besked = getattr(diag, "message_primary", None)
    if besked and getattr(diag, "sqlstate", None) == "P0001":  # RAISE EXCEPTION fra vores triggere
        return besked
    return "Linjen kunne ikke gemmes – tjek timer, kunde og opgavetype"
