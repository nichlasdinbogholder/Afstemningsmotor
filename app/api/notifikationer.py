"""Notifikationer under /api/notifikationer (login krævet) – kun ens egne. Klokken i højre hjørne henter dem.

- `GET  /api/notifikationer`               de seneste 50 + antal ulæste
- `POST /api/notifikationer/{id}/laest`    markér én som læst
- `POST /api/notifikationer/laest`         markér alle som læst
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, nuvaerende_medarbejder
from app.notifikationer.models import Notification
from app.personale.models import Staff

router = APIRouter(prefix="/api/notifikationer")
MAKS = 50


def _json(n: Notification) -> dict:
    return {"id": n.id, "alvor": n.alvor, "titel": n.titel, "tekst": n.tekst, "link": n.link,
            "kunde_id": n.client_id, "oprettet": n.oprettet.isoformat() if n.oprettet else None,
            "laest": n.laest.isoformat() if n.laest else None}


@router.get("")
def mine(m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    raekker = session.scalars(select(Notification).where(Notification.staff_id == m.id)
                              .order_by(Notification.oprettet.desc(), Notification.id.desc()).limit(MAKS))
    ulaeste = session.scalar(select(func.count()).select_from(Notification).where(
        Notification.staff_id == m.id, Notification.laest.is_(None)))
    return {"ulaeste": ulaeste, "notifikationer": [_json(n) for n in raekker]}


@router.post("/laest", dependencies=[Depends(kraev_header)])
def alle_laest(m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    antal = session.execute(update(Notification).where(Notification.staff_id == m.id, Notification.laest.is_(None))
                            .values(laest=func.now())).rowcount
    session.commit()
    return {"markeret": antal}


@router.post("/{notifikation_id}/laest", dependencies=[Depends(kraev_header)])
def laest(notifikation_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    n = session.get(Notification, notifikation_id)
    if n is None or n.staff_id != m.id:      # andres notifikationer findes ikke for en
        raise HTTPException(status_code=404, detail="Notifikationen findes ikke")
    if n.laest is None:
        n.laest = func.now()
    session.commit()
    return {"id": n.id}
