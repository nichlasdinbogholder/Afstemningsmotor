"""Fagassistenten: samtaler i databasen og kaldet til modellen.

- Kaldet sker KUN her på serveren. API-nøglen sendes aldrig til browseren, logges aldrig og står aldrig
  i en fejlbesked (nøglen registreres, så logfilteret skjuler den, hvis den alligevel dukker op).
- Grundinstruksen ligger i `system_prompt.md`, så den kan rettes uden at røre koden.
- Indtil videnbasen er bygget (fase 2), får modellen altid besked om, at der ikke er materiale – så den
  aldrig oplyser satser, beløbsgrænser eller frister (fast regel 1).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistent.models import AssistantConversation, AssistantMessage
from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed

GRUNDINSTRUKS = Path(__file__).with_name("system_prompt.md")
INTET_MATERIALE = (
    "MATERIALE FRA VIDENBASEN\n"
    "Der er intet materiale fra videnbasen til dette spørgsmål. Oplys derfor ingen satser, "
    "beløbsgrænser eller frister, og vær tilsvarende forsigtig."
)
TITEL_LAENGDE = 60


class AssistentSlaaetFra(Exception):
    """Der er ingen API-nøgle på serveren."""


def slaaet_til() -> bool:
    return get_settings().anthropic_api_key is not None


def systemtekst() -> str:
    return GRUNDINSTRUKS.read_text(encoding="utf-8").strip() + "\n\n" + INTET_MATERIALE


def egen_samtale(session: Session, samtale_id: int, staff_id: int) -> AssistantConversation | None:
    """Samtalen – men kun hvis den tilhører medarbejderen. Ellers None (vises som 'findes ikke')."""
    samtale = session.get(AssistantConversation, samtale_id)
    if samtale is None or samtale.staff_id != staff_id:
        return None
    return samtale


def historik(session: Session, samtale_id: int) -> list[dict]:
    """De seneste beskeder i det format, modellen forventer. Fejlede svar springes over."""
    antal = get_settings().assistant_history_turns * 2
    raekker = session.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == samtale_id, AssistantMessage.status != "failed")
        .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
        .limit(antal)
    ).all()
    beskeder = [{"role": b.role, "content": b.content} for b in reversed(raekker) if b.content.strip()]
    # Modellen kræver, at samtalen starter med medarbejderens spørgsmål.
    while beskeder and beskeder[0]["role"] != "user":
        beskeder.pop(0)
    return beskeder


def gem_spoergsmaal(session: Session, samtale: AssistantConversation, tekst: str) -> AssistantMessage:
    besked = AssistantMessage(conversation_id=samtale.id, role="user", content=tekst)
    session.add(besked)
    if not samtale.title:
        samtale.title = tekst[:TITEL_LAENGDE]
    samtale.updated_at = datetime.now(timezone.utc)
    session.flush()
    return besked


def gem_svar(session: Session, samtale_id: int, svar: "Svar", status: str) -> AssistantMessage:
    besked = AssistantMessage(conversation_id=samtale_id, role="assistant", content=svar.tekst, status=status,
                              sources=[], lookups=[], model=svar.model, tokens_in=svar.tokens_ind,
                              tokens_out=svar.tokens_ud, latency_ms=svar.millisekunder)
    session.add(besked)
    samtale = session.get(AssistantConversation, samtale_id)
    if samtale is not None:
        samtale.updated_at = datetime.now(timezone.utc)
    session.flush()
    return besked


@dataclass
class Svar:
    """Det, modellen har svaret indtil nu (fyldes løbende, mens svaret streames)."""

    tekst: str = ""
    model: str | None = None
    tokens_ind: int | None = None
    tokens_ud: int | None = None
    millisekunder: int | None = None
    afkortet: bool = False
    afvist: bool = False


def _klient():
    """Klienten til Anthropic. Kun her læses nøglen – og den registreres, så logs skjuler den."""
    import anthropic

    noegle = get_settings().anthropic_api_key
    if noegle is None:
        raise AssistentSlaaetFra()
    registrer_hemmelighed(noegle.get_secret_value())
    return anthropic.AsyncAnthropic(api_key=noegle.get_secret_value(), max_retries=2, timeout=120.0)


async def stream_svar(beskeder: list[dict], svar: Svar) -> AsyncIterator[str]:
    """Kald modellen og giv tekststykkerne videre, efterhånden som de kommer. `svar` udfyldes undervejs."""
    s = get_settings()
    klient = _klient()
    start = datetime.now(timezone.utc)
    # Afviser modellen et spørgsmål af sikkerhedsgrunde, prøver API'et selv en anden model ("fallbacks").
    async with klient.beta.messages.stream(
        model=s.assistant_model,
        max_tokens=s.assistant_max_tokens,
        system=systemtekst(),
        messages=beskeder,
        output_config={"effort": s.assistant_effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        async for stykke in stream.text_stream:
            svar.tekst += stykke
            yield stykke
        endelig = await stream.get_final_message()
    svar.model = endelig.model
    svar.tokens_ind = endelig.usage.input_tokens
    svar.tokens_ud = endelig.usage.output_tokens
    svar.millisekunder = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    svar.afkortet = endelig.stop_reason == "max_tokens"
    svar.afvist = endelig.stop_reason == "refusal"
