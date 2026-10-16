"""Fagassistenten under /api/v1/assistant (login krævet).

- Alt er medarbejderens EGNE samtaler. En andens samtale svarer "findes ikke" (404) – også hvis man
  gætter et id.
- Spørgsmålet gemmes, FØR modellen kaldes. Svaret streames (Server-Sent Events) og gemmes, når det er
  færdigt. Afbrydes streamen, gemmes det modtagne med status 'interrupted'.
- Ændringer kræver headeren X-Afstemning (som resten af webdelen).
- Fejl fra modellen vises som en kort dansk besked – aldrig den tekniske fejl (der kan stå detaljer i den).
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, nuvaerende_medarbejder
from app.assistent import samtale as sm
from app.assistent.models import AssistantConversation, AssistantFeedback, AssistantMessage
from app.db import ny_session
from app.personale.models import Staff

router = APIRouter(prefix="/api/v1/assistant", dependencies=[Depends(nuvaerende_medarbejder)])
log = logging.getLogger(__name__)

MAKS_SAMTALER = 50
FEJL_TEKST = "Assistenten kunne ikke svare lige nu. Prøv igen om lidt – sker det igen, så sig det til Nichlas."
AFKORTET_TEKST = "\n\n(Svaret blev afkortet, fordi det blev for langt. Spørg gerne mere præcist.)"
AFVIST_TEKST = "Det spørgsmål kan assistenten ikke svare på. Spørg den kundeansvarlige."


def _samtale_json(s: AssistantConversation) -> dict:
    return {"id": s.id, "titel": s.title, "kunde_id": s.client_id,
            "oprettet": s.created_at.isoformat(), "opdateret": s.updated_at.isoformat()}


def _besked_json(b: AssistantMessage) -> dict:
    return {"id": b.id, "rolle": b.role, "tekst": b.content, "status": b.status,
            "kilder": b.sources or [], "tidspunkt": b.created_at.isoformat()}


def _hent_egen(session: Session, samtale_id: int, m: Staff) -> AssistantConversation:
    samtale = sm.egen_samtale(session, samtale_id, m.id)
    if samtale is None:
        raise HTTPException(status_code=404, detail="Samtalen findes ikke")
    return samtale


@router.get("/status")
def status() -> dict:
    return {"slaaet_til": sm.slaaet_til()}


class NySamtale(BaseModel):
    kunde_id: int | None = None


@router.post("/conversations", dependencies=[Depends(kraev_header)])
def ny_samtale(data: NySamtale | None = None, m: Staff = Depends(nuvaerende_medarbejder),
               session: Session = Depends(db)) -> dict:
    if data is not None and data.kunde_id is not None:
        raise HTTPException(status_code=422, detail="Opslag i en kundes regnskab kommer i en senere fase")
    samtale = AssistantConversation(staff_id=m.id)
    session.add(samtale)
    session.commit()
    return _samtale_json(samtale)


@router.get("/conversations")
def samtaler(m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> list[dict]:
    raekker = session.scalars(
        select(AssistantConversation).where(AssistantConversation.staff_id == m.id)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc()).limit(MAKS_SAMTALER)
    )
    return [_samtale_json(s) for s in raekker]


@router.get("/conversations/{samtale_id}/messages")
def beskeder(samtale_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> list[dict]:
    _hent_egen(session, samtale_id, m)
    raekker = session.scalars(
        select(AssistantMessage).where(AssistantMessage.conversation_id == samtale_id)
        .order_by(AssistantMessage.created_at, AssistantMessage.id)
    )
    return [_besked_json(b) for b in raekker]


class Spoergsmaal(BaseModel):
    tekst: str = Field(min_length=1, max_length=8000)


def _haendelse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/conversations/{samtale_id}/messages", dependencies=[Depends(kraev_header)])
async def spoerg(samtale_id: int, data: Spoergsmaal, m: Staff = Depends(nuvaerende_medarbejder),
                 session: Session = Depends(db)) -> StreamingResponse:
    samtale = _hent_egen(session, samtale_id, m)
    if not sm.slaaet_til():
        raise HTTPException(status_code=503, detail="Fagassistenten er ikke slået til endnu")
    tekst = data.tekst.strip()
    if not tekst:
        raise HTTPException(status_code=422, detail="Skriv et spørgsmål")
    # Spørgsmålet gemmes FØR modellen kaldes – så det er logget, uanset hvad der sker bagefter.
    spoergsmaal = sm.gem_spoergsmaal(session, samtale, tekst)
    session.commit()
    beskeder_til_model = sm.historik(session, samtale_id)
    return StreamingResponse(_stream(samtale_id, spoergsmaal.id, beskeder_til_model),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _stream(samtale_id: int, spoergsmaal_id: int, beskeder_til_model: list[dict]):
    svar = sm.Svar()
    gemt = False
    yield _haendelse({"type": "start", "spoergsmaal_id": spoergsmaal_id})
    try:
        async for stykke in sm.stream_svar(beskeder_til_model, svar):
            yield _haendelse({"type": "tekst", "tekst": stykke})
        if svar.afvist and not svar.tekst.strip():
            svar.tekst = AFVIST_TEKST
            yield _haendelse({"type": "tekst", "tekst": AFVIST_TEKST})
        if svar.afkortet:
            svar.tekst += AFKORTET_TEKST
            yield _haendelse({"type": "tekst", "tekst": AFKORTET_TEKST})
        besked_id = _gem(samtale_id, svar, "complete")
        gemt = True
        yield _haendelse({"type": "slut", "besked_id": besked_id, "status": "complete"})
    except (asyncio.CancelledError, GeneratorExit):
        # Medarbejderen lukkede siden midt i svaret: gem det modtagne med markering (ikke en tom række).
        if svar.tekst.strip():
            _gem(samtale_id, svar, "interrupted")
        gemt = True
        raise
    except Exception as fejl:  # noqa: BLE001 – vises som kort dansk besked; detaljer kun i loggen
        log.error("Fagassistenten kunne ikke svare (%s)", type(fejl).__name__)
        if svar.tekst.strip():
            besked_id = _gem(samtale_id, svar, "interrupted")
            gemt = True
            yield _haendelse({"type": "slut", "besked_id": besked_id, "status": "interrupted"})
        yield _haendelse({"type": "fejl", "besked": FEJL_TEKST})
    finally:
        if not gemt and svar.tekst.strip():
            _gem(samtale_id, svar, "interrupted")


def _gem(samtale_id: int, svar: sm.Svar, status: str) -> int:
    with ny_session() as s:
        besked = sm.gem_svar(s, samtale_id, svar, status)
        s.commit()
        return besked.id


class Vurdering(BaseModel):
    vurdering: str = Field(pattern="^(up|down)$")
    kommentar: str | None = Field(default=None, max_length=2000)


@router.post("/messages/{besked_id}/feedback", dependencies=[Depends(kraev_header)])
def feedback(besked_id: int, data: Vurdering, m: Staff = Depends(nuvaerende_medarbejder),
             session: Session = Depends(db)) -> dict:
    besked = session.get(AssistantMessage, besked_id)
    if besked is None or sm.egen_samtale(session, besked.conversation_id, m.id) is None:
        raise HTTPException(status_code=404, detail="Svaret findes ikke")
    if besked.role != "assistant":
        raise HTTPException(status_code=422, detail="Kun svar fra assistenten kan vurderes")
    kommentar = (data.kommentar or "").strip() or None
    session.add(AssistantFeedback(message_id=besked.id, staff_id=m.id, rating=data.vurdering, comment=kommentar))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Du har allerede vurderet dette svar")
    return {"ok": True}
