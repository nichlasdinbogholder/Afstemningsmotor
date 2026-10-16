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
from sqlalchemy import select, text
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
FEJL_TEKST = "Svend kunne ikke svare lige nu. Prøv igen om lidt – sker det igen, så sig det til Nichlas."
AFKORTET_TEKST = "\n\n(Svaret blev afkortet, fordi det blev for langt. Spørg gerne mere præcist.)"
AFVIST_TEKST = "Det spørgsmål kan Svend ikke svare på. Spørg den kundeansvarlige."


def _samtale_json(s: AssistantConversation) -> dict:
    return {"id": s.id, "titel": s.title, "kunde_id": s.client_id,
            "oprettet": s.created_at.isoformat(), "opdateret": s.updated_at.isoformat()}


def _besked_json(b: AssistantMessage, vurdering: str | None = None) -> dict:
    return {"id": b.id, "rolle": b.role, "tekst": b.content, "status": b.status,
            "kilder": b.sources or [], "tidspunkt": b.created_at.isoformat(), "min_vurdering": vurdering}


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
    ).all()
    vurderinger = dict(session.execute(
        select(AssistantFeedback.message_id, AssistantFeedback.rating)
        .where(AssistantFeedback.staff_id == m.id, AssistantFeedback.message_id.in_([b.id for b in raekker]))
    ).all())
    return [_besked_json(b, vurderinger.get(b.id)) for b in raekker]


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
    materiale = sm.find_materiale(session, tekst)
    return StreamingResponse(_stream(samtale_id, spoergsmaal.id, beskeder_til_model,
                                     sm.systemtekst(materiale), sm.kilder(materiale)),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _stream(samtale_id: int, spoergsmaal_id: int, beskeder_til_model: list[dict], system: str,
                  kilder: list[dict]):
    svar = sm.Svar(kilder=kilder)
    gemt = False
    yield _haendelse({"type": "start", "spoergsmaal_id": spoergsmaal_id, "kilder": kilder})
    try:
        async for stykke in sm.stream_svar(beskeder_til_model, svar, system):
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


# --- Loggen (back office-siden "Fagassistent") ----------------------------------------------------------
# Administratorer ser ALLE spørgsmål; en medarbejder ser kun sine egne (beslutning 16.10.2026).

MAKS_LOG = 200

LOG_SQL = text("""
SELECT q.id, q.created_at, q.content AS spoergsmaal, c.id AS samtale_id, st.navn, st.email,
       a.content AS svar, a.status AS svar_status, f.rating, f.comment
FROM assistant_messages q
JOIN assistant_conversations c ON c.id = q.conversation_id
JOIN staff st ON st.id = c.staff_id
LEFT JOIN LATERAL (
    SELECT id, content, status FROM assistant_messages
    WHERE conversation_id = q.conversation_id AND role = 'assistant' AND id > q.id
    ORDER BY id LIMIT 1
) a ON TRUE
LEFT JOIN assistant_feedback f ON f.message_id = a.id AND f.staff_id = c.staff_id
WHERE q.role = 'user'
  AND (CAST(:staff_id AS integer) IS NULL OR c.staff_id = :staff_id)
  AND (NOT :kun_ned OR f.rating = 'down')
ORDER BY q.created_at DESC, q.id DESC
LIMIT :graense
""")

# De hyppigste ord i spørgsmålene. Kun ord af bogstaver (aldrig tal – så et CPR- eller kontonummer, nogen har
# skrevet, aldrig vises her), mindst 4 bogstaver, og danske småord ("og", "det" …) springes over via
# Postgres' danske ordbog. Ordene vises, som de er skrevet.
EMNER_SQL = text("""
SELECT ord, count(DISTINCT q.id) AS antal
FROM assistant_messages q
JOIN assistant_conversations c ON c.id = q.conversation_id
CROSS JOIN LATERAL regexp_split_to_table(lower(q.content), '[^a-zæøåéü]+') AS ord
WHERE q.role = 'user' AND q.created_at > now() - interval '30 days'
  AND (CAST(:staff_id AS integer) IS NULL OR c.staff_id = :staff_id)
  AND length(ord) >= 4
  AND numnode(plainto_tsquery('danish', ord)) > 0
GROUP BY ord
ORDER BY antal DESC, ord
LIMIT 15
""")


def _hvis_ikke_admin(m: Staff) -> int | None:
    return None if m.rolle == "admin" else m.id


@router.get("/log")
def log_over_spoergsmaal(kun_ned: bool = False, m: Staff = Depends(nuvaerende_medarbejder),
                         session: Session = Depends(db)) -> dict:
    raekker = session.execute(LOG_SQL, {"staff_id": _hvis_ikke_admin(m), "kun_ned": kun_ned,
                                        "graense": MAKS_LOG}).all()
    return {"alle": m.rolle == "admin",
            "spoergsmaal": [{"id": r.id, "tidspunkt": r.created_at.isoformat(), "samtale_id": r.samtale_id,
                             "medarbejder": r.navn, "email": r.email, "spoergsmaal": r.spoergsmaal,
                             "svar": r.svar, "svar_status": r.svar_status, "vurdering": r.rating,
                             "kommentar": r.comment} for r in raekker],
            "afkortet": len(raekker) == MAKS_LOG}


@router.get("/emner")
def hyppigste_emner(m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> list[dict]:
    raekker = session.execute(EMNER_SQL, {"staff_id": _hvis_ikke_admin(m)}).all()
    return [{"ord": r.ord, "antal": r.antal} for r in raekker]
