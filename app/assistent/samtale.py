"""Fagassistenten: samtaler i databasen og kaldet til modellen.

- Kaldet sker KUN her på serveren. API-nøglen sendes aldrig til browseren, logges aldrig og står aldrig
  i en fejlbesked (nøglen registreres, så logfilteret skjuler den, hvis den alligevel dukker op).
- Grundinstruksen ligger i `system_prompt.md`, så den kan rettes uden at røre koden.
- Før hvert kald slås spørgsmålet op i videnbasen (Postgres' danske fritekstsøgning). KUN aktive dokumenter,
  hvor dagens dato ligger i gyldighedsperioden, kommer med – en udløbet eller ikke-godkendt sats når ALDRIG
  modellen. Satser sendes med datoen, de senest er bekræftet. Findes intet, får modellen besked om det, så den
  ikke oplyser satser, beløbsgrænser eller frister (fast regel 1).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.assistent.models import AssistantConversation, AssistantMessage, KbDocument
from app.assistent.satskontrol import er_foraeldet
from app.config import get_settings
from app.kunder.models import Client
from app.personale.models import Staff
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
            if er_foraeldet(d):
                linjer.append(
                    "ADVARSEL: Satsen er FORÆLDET (ikke bekræftet længe). Når du bruger den, skal du skrive ordret: "
                    f"\"Satsen er senest bekræftet {_dk_dato(d.last_verified_at)} — tjek den, før du bruger den "
                    "til noget bindende.\"")
        linjer.append(d.body.strip())
        if d.source_url:
            linjer.append(f"Kilde: {d.source_url}")
        dele.append("\n".join(linjer))
    return "\n\n".join(dele)


def tilknyttede_kunder(session: Session, medarbejder: Staff) -> list[Client]:
    """De kunder, medarbejderen må slå op hos i Svend (beslutning 18.10.2026): en ADMINISTRATOR må alle aktive
    kunder; en medarbejder kun de aktive kunder, hun er ANSVARLIG for. Kontrolleres på serveren ved hvert kald –
    aldrig kun i brugerfladen."""
    q = select(Client).where(Client.status == "aktiv")
    if medarbejder.rolle != "admin":
        q = q.where(Client.ansvarlig_medarbejder_id == medarbejder.id)
    return session.scalars(q.order_by(Client.navn)).all()


def er_tilknyttet(session: Session, medarbejder: Staff, client_id: int) -> bool:
    k = session.get(Client, client_id)
    if k is None or k.status != "aktiv":
        return False
    return medarbejder.rolle == "admin" or k.ansvarlig_medarbejder_id == medarbejder.id


def kundetekst(kunde: Client | None) -> str:
    if kunde is None:
        return ""
    return ("VALGT KUNDE\n"
            f"Samtalen handler om kunden {kunde.navn} (kundenr. {kunde.kundenummer}"
            f"{', CVR ' + kunde.cvr if kunde.cvr else ''}), client_id {kunde.id}.\n"
            "Du kan slå op i kundens regnskab med opslagsfunktionerne (kun læsning). Brug dem, når spørgsmålet "
            "kræver kundens tal – gæt aldrig et tal. Nævn kun tal, du selv har slået op i denne samtale.\n"
            "Har du slået tal op, bygger du svaret sådan:\n"
            "1. Først hvad du slog op, og hvor: konto (nummer og navn), periode og hvad du fandt – fx antal "
            "posteringer og beløb.\n"
            "2. Så reglen, forklaret kort.\n"
            "3. Til sidst, hvem der skal vurdere den konkrete sag.\n"
            "Eksempel:\n"
            "Jeg har set på konto 3217 (Arbejdstøj) for 3. kvartal: der er bogført 12.400 kr. fordelt på fem bilag.\n"
            "Reglen er, at … [forklaring]\n"
            "Om de konkrete bilag kan stå, skal du have den kundeansvarlige til at vurdere.\n"
            "Skriv beløb på dansk (1.234,50 kr.). Rammer et opslag loftet for rækker eller antal opslag, så sig det "
            "i svaret i stedet for at hente videre. Tallene er fakta fra bogføringen – du konkluderer stadig ikke "
            "på kundens vegne, og du laver ingen momsangivelse eller siger, hvad der skal indberettes.")


OPSLAGSNAVNE = {
    "get_kontoplan": "Kontoplan", "get_saldo": "Saldo", "get_posteringer": "Posteringer",
    "get_aabne_debitorer": "Åbne poster hos debitorer", "get_aabne_kreditorer": "Åbne poster hos kreditorer",
    "get_moms": "Momskonti", "get_kundeinfo": "Kundeinfo",
}


def beskriv_opslag(opslag: dict) -> str:
    """Én linje om et opslag til visning under svaret, fx 'Saldo · konto 3217 · 01.07.2026–30.09.2026 · 1 række'."""
    a = opslag.get("argumenter") or {}
    dele = [OPSLAGSNAVNE.get(opslag.get("funktion"), str(opslag.get("funktion")))]
    if a.get("konto") is not None:
        dele.append(f"konto {a['konto']}")
    try:
        dele.append(f"{_dk_dato(date.fromisoformat(a['fra']))}–{_dk_dato(date.fromisoformat(a['til']))}")
    except (KeyError, TypeError, ValueError):
        pass
    if opslag.get("fejl"):
        dele.append(f"ikke udført: {opslag['fejl']}")
    else:
        n = opslag.get("antal_raekker", 0)
        dele.append(f"{n} {'række' if n == 1 else 'rækker'}" + (" (afkortet)" if opslag.get("afkortet") else ""))
    return " · ".join(dele)


def systemtekst(materiale: list[KbDocument] | None = None, kunde: Client | None = None) -> str:
    dele = [GRUNDINSTRUKS.read_text(encoding="utf-8").strip(), materialetekst(materiale or [])]
    if kunde is not None:
        dele.append(kundetekst(kunde))
    return "\n\n".join(dele)


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
                              sources=svar.kilder, lookups=svar.lookups, model=svar.model, tokens_in=svar.tokens_ind,
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
    lookups: list = field(default_factory=list)  # opslag i kundens regnskab (se opslag.py)


def _klient():
    """Klienten til Anthropic. Kun her læses nøglen – og den registreres, så logs skjuler den."""
    import anthropic

    noegle = get_settings().anthropic_api_key
    if noegle is None:
        raise AssistentSlaaetFra()
    registrer_hemmelighed(noegle.get_secret_value())
    return anthropic.AsyncAnthropic(api_key=noegle.get_secret_value(), max_retries=2, timeout=120.0)


LOFT_OPSLAG_TEKST = ("\n\n(Svend har brugt det højeste antal opslag i kundens regnskab ({n}) til dette spørgsmål. "
                     "Mangler der noget, så spørg mere præcist.)")
LOFT_RAEKKER_TEKST = ("\n\n(Mindst ét opslag gav flere rækker end Svend må hente ({n}), så listen er afkortet. "
                      "Spørg gerne om en kortere periode eller én konto.)")


def _blok(b) -> dict:
    """En indholdsblok fra modellens svar, som den skal sendes tilbage i næste kald."""
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return b.model_dump(mode="json", exclude_none=True)


async def stream_svar(beskeder: list[dict], svar: Svar, system: str | None = None,
                      opslag=None) -> AsyncIterator[str]:
    """Kald modellen og giv tekststykkerne videre, efterhånden som de kommer. `svar` udfyldes undervejs.

    Med `opslag` (kun i en samtale om en kunde) får modellen de læsende opslagsfunktioner. Beder den om et
    opslag, udføres det på serveren (med kontrol af kunden), resultatet sendes tilbage, og modellen fortsætter.
    Højst ASSISTANT_MAX_TOOL_CALLS opslag pr. besked – derefter må modellen kun skrive sit svar."""
    from app.assistent import opslag as opslagsmodul

    s = get_settings()
    klient = _klient()
    start = datetime.now(timezone.utc)
    beskeder = list(beskeder)
    brugt = 0
    loft_naaet = False
    svar.tokens_ind, svar.tokens_ud = 0, 0
    if opslag is not None:
        svar.lookups = opslag.lookups  # samme liste – så opslagene gemmes, også hvis svaret afbrydes
    for _ in range(s.assistant_max_tool_calls + 2):
        ekstra = {}
        if opslag is not None:
            ekstra["tools"] = opslagsmodul.VAERKTOEJER
            if brugt >= s.assistant_max_tool_calls:
                ekstra["tool_choice"] = {"type": "none"}
        # Afviser modellen et spørgsmål af sikkerhedsgrunde, prøver API'et selv en anden model ("fallbacks").
        async with klient.beta.messages.stream(
            model=s.assistant_model,
            max_tokens=s.assistant_max_tokens,
            system=system or systemtekst(),
            messages=beskeder,
            output_config={"effort": s.assistant_effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **ekstra,
        ) as stream:
            async for stykke in stream.text_stream:
                svar.tekst += stykke
                yield stykke
            endelig = await stream.get_final_message()
        svar.model = endelig.model
        svar.tokens_ind += endelig.usage.input_tokens
        svar.tokens_ud += endelig.usage.output_tokens
        svar.afkortet = endelig.stop_reason == "max_tokens"
        svar.afvist = endelig.stop_reason == "refusal"
        kald = [b for b in endelig.content if b.type == "tool_use"]
        if opslag is None or endelig.stop_reason != "tool_use" or not kald:
            break
        resultater = []
        for b in kald:
            if brugt >= s.assistant_max_tool_calls:
                loft_naaet = True
                indhold = {"fejl": f"Loftet på {s.assistant_max_tool_calls} opslag pr. besked er nået. Hent ikke "
                                   "mere – skriv svaret ud fra det, du har, og sig, at loftet er nået."}
            else:
                brugt += 1
                indhold = await asyncio.to_thread(opslag.udfoer, b.name, dict(b.input or {}))
            resultater.append({"type": "tool_result", "tool_use_id": b.id, "is_error": "fejl" in indhold,
                               "content": json.dumps(indhold, ensure_ascii=False)})
        if brugt >= s.assistant_max_tool_calls:
            loft_naaet = True
        beskeder += [{"role": "assistant", "content": [_blok(b) for b in endelig.content]},
                     {"role": "user", "content": resultater}]
        # Kommer der mere tekst efter et opslag, adskilles det fra det foregående.
        if svar.tekst and not svar.tekst.endswith("\n"):
            svar.tekst += "\n\n"
            yield "\n\n"
    if opslag is not None:
        if loft_naaet:
            tekst = LOFT_OPSLAG_TEKST.format(n=s.assistant_max_tool_calls)
            svar.tekst += tekst
            yield tekst
        if opslag.afkortet:
            tekst = LOFT_RAEKKER_TEKST.format(n=s.assistant_max_rows)
            svar.tekst += tekst
            yield tekst
    svar.millisekunder = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
