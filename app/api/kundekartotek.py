"""Kundekartoteket under /api/kundekartotek (login krævet). Alle kan se listerne; KUN administratorer kan oprette,
arkivere og genaktivere. Logikken ligger i app/kunder/kartotek.py. Ændringer kræver headeren X-Afstemning.

- `GET  /api/kundekartotek?arkiverede=false|true`   Kundeoversigten eller de arkiverede kunder
- `GET  /api/kundekartotek/valg`                    medarbejdere, regnskabssystemer og årsager til formularerne
- `POST /api/kundekartotek`                         opret en kunde (startdato + afregningsform kræves)
- `POST /api/kundekartotek/{id}/ansvarlig`          skift ansvarlig medarbejder (styrer adgangen hos Svend)
- `POST /api/kundekartotek/{id}/arkiver`            årsag + dato for samarbejdets ophør
- `POST /api/kundekartotek/{id}/genaktiver`
"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin, nuvaerende_medarbejder
from app.kunder import kartotek
from app.kunder.models import ARKIV_AARSAGER, SYSTEMER
from app.personale.models import Staff
from app.tid import TIDSZONE

router = APIRouter(prefix="/api/kundekartotek", dependencies=[Depends(nuvaerende_medarbejder)])


def _i_dag() -> date:
    return datetime.now(TIDSZONE).date()


@router.get("")
def kunder(arkiverede: bool = False, session: Session = Depends(db)) -> list[dict]:
    return kartotek.liste(session, arkiverede, _i_dag())


@router.get("/valg")
def valg(session: Session = Depends(db)) -> dict:
    return {
        "medarbejdere": [{"id": m.id, "navn": m.navn} for m in session.scalars(
            select(Staff).where(Staff.aktiv.is_(True)).order_by(Staff.navn))],
        "systemer": [{"kode": s, "navn": kartotek.SYSTEM_TEKST.get(s, s)} for s in SYSTEMER],
        "aarsager": [{"kode": a, "navn": kartotek.AARSAG_TEKST[a]} for a in ARKIV_AARSAGER],
    }


class NyKunde(BaseModel):
    navn: str = Field(max_length=255)
    kundenummer: str = Field(max_length=50)
    cvr: str | None = Field(default=None, max_length=12)
    system: str
    ansvarlig_id: int | None = None
    startdato: date
    afregning: str
    abonnementspris: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)


@router.post("", dependencies=[Depends(kraev_header)])
def opret(data: NyKunde, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    try:
        with session.begin_nested():
            k = kartotek.opret(session, af, data.navn, data.kundenummer, data.cvr, data.system, data.ansvarlig_id,
                               data.startdato, data.afregning, data.abonnementspris)
    except kartotek.KartotekFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return {"id": k.id, "navn": k.navn}


class Ansvarlig(BaseModel):
    ansvarlig_id: int | None


@router.post("/{kunde_id}/ansvarlig", dependencies=[Depends(kraev_header)])
def ansvarlig(kunde_id: int, data: Ansvarlig, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    try:
        with session.begin_nested():
            k = kartotek.saet_ansvarlig(session, af, kunde_id, data.ansvarlig_id)
    except kartotek.KartotekFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return {"id": k.id, "ansvarlig_id": k.ansvarlig_medarbejder_id}


class Arkivering(BaseModel):
    aarsag: str
    ophoersdato: date


@router.post("/{kunde_id}/arkiver", dependencies=[Depends(kraev_header)])
def arkiver(kunde_id: int, data: Arkivering, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    try:
        with session.begin_nested():
            k = kartotek.arkiver(session, af, kunde_id, data.aarsag, data.ophoersdato)
    except kartotek.KartotekFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return {"id": k.id, "navn": k.navn, "status": k.status}


@router.post("/{kunde_id}/genaktiver", dependencies=[Depends(kraev_header)])
def genaktiver(kunde_id: int, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    try:
        with session.begin_nested():
            k = kartotek.genaktiver(session, af, kunde_id)
    except kartotek.KartotekFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return {"id": k.id, "navn": k.navn, "status": k.status}
