"""AI Planlægning under /api/planlaegning (login krævet) – bygherren 26.10.2026.

- `GET  /api/planlaegning/frister?fra=&til=&mine=&ansvarlig_id=&afgift=`   kommende frister + advarsler
- `GET  /api/planlaegning/kalender?tilbagekaldte=`                        Skats fristkalender (alle ser)
- `POST /api/planlaegning/kalender`                                       læg en frist ind – KUN admin
- `POST /api/planlaegning/kalender/{id}/godkend`                          godkend et udkast – KUN admin
- `POST /api/planlaegning/kalender/{id}/tilbagekald`                      tilbagekald en frist – KUN admin
Logikken ligger i app/frister/kalender.py. Ændringer kræver headeren X-Afstemning.
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin, nuvaerende_medarbejder
from app.frister import kalender
from app.personale.models import Staff
from app.tid import TIDSZONE

router = APIRouter(prefix="/api/planlaegning", dependencies=[Depends(nuvaerende_medarbejder)])
STANDARD_DAGE = 92                     # standard: de næste 3 måneder


@router.get("/frister")
def frister(fra: date | None = None, til: date | None = None, mine: bool = False, ansvarlig_id: int | None = None,
            afgift: str | None = None, m: Staff = Depends(nuvaerende_medarbejder),
            session: Session = Depends(db)) -> dict:
    fra = fra or datetime.now(TIDSZONE).date()
    til = til or fra + timedelta(days=STANDARD_DAGE)
    if til < fra:
        raise HTTPException(status_code=422, detail="Til-datoen ligger før fra-datoen")
    svar = kalender.kommende(session, m, fra, til, kalender.Filtre(mine=mine, ansvarlig_id=ansvarlig_id, afgift=afgift))
    return svar | {"fra": fra.isoformat(), "til": til.isoformat()}


@router.get("/kalender")
def kalender_vis(tilbagekaldte: bool = False, session: Session = Depends(db)) -> list[dict]:
    return [kalender.json(f) for f in kalender.kalender(session, tilbagekaldte)]


class NyFrist(BaseModel):
    afgift: str
    ordning: str
    periode_fra: date
    periode_til: date
    frist: date
    kilde: str = Field(max_length=500)


def _udfoer(session: Session, handling) -> dict:
    try:
        with session.begin_nested():
            f = handling()
    except kalender.IkkeTilladt:
        raise HTTPException(status_code=403, detail="Kun administratorer kan ændre Skats fristkalender") from None
    except kalender.FristFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return kalender.json(f)


@router.post("/kalender", dependencies=[Depends(kraev_header)])
def tilfoej(data: NyFrist, m: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kalender.tilfoej(session, m, data.afgift, data.ordning, data.periode_fra,
                                                     data.periode_til, data.frist, data.kilde))


@router.post("/kalender/{frist_id}/godkend", dependencies=[Depends(kraev_header)])
def godkend(frist_id: int, m: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kalender.godkend(session, m, frist_id))


@router.post("/kalender/{frist_id}/tilbagekald", dependencies=[Depends(kraev_header)])
def tilbagekald(frist_id: int, m: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kalender.tilbagekald(session, m, frist_id))
