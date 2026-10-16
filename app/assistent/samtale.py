"""Fagassistenten: samtaler i databasen og kaldet til modellen.

- Kaldet sker KUN her på serveren. API-nøglen sendes aldrig til browseren, logges aldrig og står aldrig
  i en fejlbesked (nøglen registreres, så logfilteret skjuler den, hvis den alligevel dukker op).
- Grundinstruksen ligger i `system_prompt.md`, så den kan rettes uden at røre koden.
- Før hvert kald slås spørgsmålet op i videnbasen (Postgres' danske fritekstsøgning). KUN aktive dokumenter,
  hvor dagens dato ligger i gyldighedsperioden, kommer med – en udløbet eller ikke-godkendt sats når ALDRIG
  modellen. Satser sendes med datoen, de senest er bekræftet. Findes intet, får modellen besked om det, så den
  ikke oplyser satser, beløbsgrænser eller frister (fast regel 1).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.assistent.models import AssistantConversation, AssistantMessage, KbDocument
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


# Søgningen: ordene i spørgsmålet, bøjet af Postgres' danske ordbog. Ét ord er nok til et match ("eller"),
# og der sorteres efter, hvor godt dokumentet passer. Ordbogen bøjer ikke altid ens ("sats" → "sat", men
# "satsen" → "sats"), så ord på mindst 6 tegn matcher også, når kun endelsen er anderledes (sidste tegn
# fjernes, og alt der begynder sådan, passer). Korte ord skal passe præcist – så "bil" ikke rammer "bilag".
SOEG_SQL = text("""
WITH q AS (
    SELECT to_tsquery('simple', string_agg(
               CASE WHEN length(lex) >= 6 THEN format('%L:*', left(lex, length(lex) - 1)) ELSE format('%L', lex) END,
               ' | ')) AS tq
    FROM unnest(tsvector_to_array(to_tsvector('danish', :q))) AS lex
)
SELECT d.id FROM kb_documents d, q
WHERE d.status = 'active'
  AND (d.valid_from IS NULL OR d.valid_from <= :dag)
  AND (d.valid_to IS NULL OR d.valid_to >= :dag)
  AND d.search @@ q.tq
ORDER BY ts_rank(d.search, q.tq) DESC, d.id
LIMIT :antal
""")


def find_materiale(session: Session, spoergsmaal: str, dag: date | None = None) -> list[KbDocument]:
    """De bedst passende, gyldige dokumenter til spørgsmålet (højst ASSISTANT_KB_TOP_K)."""
    dag = dag or datetime.now(timezone.utc).date()
    if not spoergsmaal.strip():
        return []
    ider = session.scalars(SOEG_SQL, {"q": spoergsmaal, "dag": dag,
                                      "antal": get_settings().assistant_kb_top_k}).all()
    efter_id = {d.id: d for d in session.scalars(select(KbDocument).where(KbDocument.id.in_(ider)))}
    return [efter_id[i] for i in ider if i in efter_id]


def _dk_dato(d: date | datetime | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "aldrig"


def dk_tal(v: Decimal) -> str:
    tekst = f"{v.normalize():f}"
    return tekst.replace(".", ",")


def materialetekst(materiale: list[KbDocument]) -> str:
    if not materiale:
        return INTET_MATERIALE
    dele = [
        "MATERIALE FRA VIDENBASEN\n"
        "Nedenfor står de dokumenter fra vores egen videnbase, der passer bedst til spørgsmålet. Satser, "
        "beløbsgrænser og frister må KUN komme herfra. Når du bruger en sats, så sig, hvornår den senest er "
        "bekræftet. Står det, du skal bruge, ikke her, så sig det."
    ]
    for nr, d in enumerate(materiale, start=1):
        hoved = f"[{nr}] {d.title} (kategori: {d.category})"
        if d.valid_from or d.valid_to:
            hoved += f" – gyldig {('fra ' + _dk_dato(d.valid_from)) if d.valid_from else ''}" \
                     f"{(' til ' + _dk_dato(d.valid_to)) if d.valid_to else ''}"
        linjer = [hoved]
        if d.category == "satser" and d.value is not None:
            linjer.append(f"Værdi: {dk_tal(d.value)} {d.unit}. Senest bekræftet: {_dk_dato(d.last_verified_at)}.")
        linjer.append(d.body.strip())
        if d.source_url:
            linjer.append(f"Kilde: {d.source_url}")
        dele.append("\n".join(linjer))
    return "\n\n".join(dele)


def systemtekst(materiale: list[KbDocument] | None = None) -> str:
    return GRUNDINSTRUKS.read_text(encoding="utf-8").strip() + "\n\n" + materialetekst(materiale or [])


def kilder(materiale: list[KbDocument]) -> list[dict]:
    return [{"kb_id": d.id, "title": d.title} for d in materiale]


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
                              sources=svar.kilder, lookups=[], model=svar.model, tokens_in=svar.tokens_ind,
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
    kilder: list = field(default_factory=list)  # [{kb_id, title}] – dokumenterne, der blev sendt med


def _klient():
    """Klienten til Anthropic. Kun her læses nøglen – og den registreres, så logs skjuler den."""
    import anthropic

    noegle = get_settings().anthropic_api_key
    if noegle is None:
        raise AssistentSlaaetFra()
    registrer_hemmelighed(noegle.get_secret_value())
    return anthropic.AsyncAnthropic(api_key=noegle.get_secret_value(), max_retries=2, timeout=120.0)


async def stream_svar(beskeder: list[dict], svar: Svar, system: str | None = None) -> AsyncIterator[str]:
    """Kald modellen og giv tekststykkerne videre, efterhånden som de kommer. `svar` udfyldes undervejs."""
    s = get_settings()
    klient = _klient()
    start = datetime.now(timezone.utc)
    # Afviser modellen et spørgsmål af sikkerhedsgrunde, prøver API'et selv en anden model ("fallbacks").
    async with klient.beta.messages.stream(
        model=s.assistant_model,
        max_tokens=s.assistant_max_tokens,
        system=system or systemtekst(),
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
