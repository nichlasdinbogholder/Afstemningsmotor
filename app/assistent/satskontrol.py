"""Satskontrollen (fase 3, trin 10): hver mandag efterses alle aktive satser mod deres kildeside.

For hver aktiv sats med en kontrolside (`verify_source_url`):
1. Hent siden (kun læsning).
2. Find værdien ud fra hintet (`verify_hint`). Først med en fast regel; kan den ikke afgøres entydigt, læser
   modellen det relevante tekststykke og svarer med et tal og en begrundelse. Tallet SKAL stå i sidens tekst –
   ellers regnes det som en fejl (modellen må aldrig finde på et tal).
3. Sammenlign med den nuværende værdi:
   - uændret → `last_verified_at` = nu. Intet andet.
   - ændret  → et nyt dokument som KLADDE med den nye værdi (`replaces_id` = den gamle). Den gamle forbliver
               aktiv. Kontrollen er et fund, indtil et menneske har godkendt eller afvist.
   - fejl    → fund med fejlbeskeden. Den gamle forbliver aktiv, `last_verified_at` opdateres IKKE.
Hver kontrol skrives i kb_rate_checks – også de uændrede.

INTET bliver aktivt automatisk – databasen afviser det (trigger `kb_kraev_godkendelse`).
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistent.models import KbDocument, KbRateCheck
from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed

# Et dansk tal: 3,79 · 1.234,50 · 12.345 · 25
TAL = re.compile(r"(?<![\d,.])-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?(?![\d])")
ANKER_I_CITAT = re.compile(r"['\"‘’“”«»]([^'\"‘’“”«»]{2,})['\"‘’“”«»]")
SOEG_EFTER_ANKER = 200  # tegn efter ankeret, hvor tallet skal stå
UDDRAG_FOER, UDDRAG_EFTER = 150, 250
MODEL_MAKS_TEGN = 20_000


class KontrolFejl(Exception):
    """Værdien kunne ikke findes sikkert. Beskeden vises til medarbejderen – aldrig tekniske detaljer."""


class _Tekst(HTMLParser):
    """Gør en hjemmeside til ren tekst (uden script/style), så tallet kan findes."""

    SKJULT = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.dele: list[str] = []
        self._skjult = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKJULT:
            self._skjult += 1
        elif tag in {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "table", "section"}:
            self.dele.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKJULT and self._skjult:
            self._skjult -= 1

    def handle_data(self, data):
        if not self._skjult:
            self.dele.append(data)


def html_til_tekst(html: str) -> str:
    p = _Tekst()
    p.feed(html)
    return re.sub(r"\s+", " ", "".join(p.dele)).strip()


def hent_side(url: str) -> str:
    """Hent kildesiden som ren tekst. Kun GET."""
    try:
        svar = httpx.get(url, timeout=30.0, follow_redirects=True,
                         headers={"User-Agent": "DinBogholder-Satskontrol/1.0"})
        svar.raise_for_status()
    except httpx.HTTPStatusError as fejl:
        raise KontrolFejl(f"Kildesiden svarede med fejl {fejl.response.status_code}") from None
    except httpx.HTTPError:
        raise KontrolFejl("Kildesiden kunne ikke hentes") from None
    if "html" in svar.headers.get("content-type", "html"):
        return html_til_tekst(svar.text)
    return re.sub(r"\s+", " ", svar.text).strip()


def dansk_tal(tekst: str) -> Decimal:
    """'1.234,50' → 1234.50 · '3,79' → 3.79. Kun dansk skrivemåde (punktum = tusinder, komma = decimal)."""
    rent = tekst.strip().replace(" ", "")
    if not TAL.fullmatch(rent):
        raise KontrolFejl(f"'{tekst}' er ikke et tal på dansk form")
    try:
        return Decimal(rent.replace(".", "").replace(",", "."))
    except InvalidOperation:
        raise KontrolFejl(f"'{tekst}' er ikke et tal") from None


def anker(hint: str) -> str:
    """Den tekst på siden, tallet står efter: et citat i hintet ('…'), ellers hele hintet."""
    m = ANKER_I_CITAT.search(hint)
    return (m.group(1) if m else hint).strip()


def _uddrag(tekst: str, start: int, slut: int) -> str:
    return tekst[max(0, start - UDDRAG_FOER):slut + UDDRAG_EFTER].strip()


@dataclass
class Fund:
    vaerdi: Decimal
    uddrag: str
    metode: str  # 'regel' | 'model'


def find_med_regel(tekst: str, hint: str) -> Fund | None:
    """Ankeret skal stå præcis ét sted, og et tal skal følge lige efter. Ellers er det ikke entydigt (None)."""
    a = anker(hint)
    if not a:
        return None
    steder = [m.start() for m in re.finditer(re.escape(a), tekst, flags=re.IGNORECASE)]
    if len(steder) != 1:
        return None
    efter = steder[0] + len(a)
    m = TAL.search(tekst, efter, efter + SOEG_EFTER_ANKER)
    if m is None:
        return None
    return Fund(dansk_tal(m.group(0)), _uddrag(tekst, steder[0], m.end()), "regel")


SKEMA = {
    "type": "object",
    "properties": {
        "fundet": {"type": "boolean"},
        "vaerdi": {"type": "string", "description": "Tallet præcis som det står på siden, fx '3,79'. Tom hvis ikke fundet."},
        "tekststykke": {"type": "string", "description": "Den sætning/række på siden, tallet står i – ordret."},
        "begrundelse": {"type": "string"},
    },
    "required": ["fundet", "vaerdi", "tekststykke", "begrundelse"],
    "additionalProperties": False,
}


def _modellens_tekst(tekst: str, hint: str) -> str:
    a = anker(hint)
    steder = [m.start() for m in re.finditer(re.escape(a), tekst, flags=re.IGNORECASE)] if a else []
    if steder:
        return "\n…\n".join(tekst[max(0, s - 600):s + 900] for s in steder[:5])
    return tekst[:MODEL_MAKS_TEGN]


def spoerg_model(titel: str, enhed: str | None, hint: str, uddrag: str) -> dict:
    """Lad modellen læse tekststykket og returnere tallet. Kun her kaldes modellen i satskontrollen."""
    import anthropic

    s = get_settings()
    if s.anthropic_api_key is None:
        raise KontrolFejl("Reglen kunne ikke finde værdien entydigt, og Svend er ikke slået til")
    registrer_hemmelighed(s.anthropic_api_key.get_secret_value())
    klient = anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=2, timeout=120.0)
    svar = klient.messages.create(
        model=s.assistant_model,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SKEMA}},
        system=("Du læser en side fra en myndighed og finder ÉN sats. Svar kun ud fra teksten. Gæt aldrig: er "
                "tallet ikke entydigt, så sæt fundet=false. Skriv tallet præcis som det står på siden."),
        messages=[{"role": "user", "content": (
            f"Sats: {titel}\nEnhed: {enhed or 'ukendt'}\nSådan findes værdien: {hint}\n\n"
            f"Tekst fra siden:\n<<<\n{uddrag}\n>>>")}],
    )
    if svar.stop_reason == "refusal":
        raise KontrolFejl("Modellen ville ikke læse siden")
    tekst = next((b.text for b in svar.content if b.type == "text"), "")
    try:
        return json.loads(tekst)
    except json.JSONDecodeError:
        raise KontrolFejl("Modellens svar kunne ikke læses") from None


def find_med_model(tekst: str, dok: KbDocument) -> Fund:
    uddrag = _modellens_tekst(tekst, dok.verify_hint or "")
    data = spoerg_model(dok.title, dok.unit, dok.verify_hint or "", uddrag)
    if not data.get("fundet") or not str(data.get("vaerdi", "")).strip():
        raise KontrolFejl("Værdien kunne ikke findes entydigt på siden" +
                          (f": {data['begrundelse']}" if data.get("begrundelse") else ""))
    vaerdi_tekst = str(data["vaerdi"]).strip()
    # Tallet SKAL stå på siden – modellen må aldrig finde på et tal.
    if vaerdi_tekst not in tekst:
        raise KontrolFejl(f"Modellen fandt '{vaerdi_tekst}', men det tal står ikke på siden")
    stykke = str(data.get("tekststykke", "")).strip() or uddrag[:400]
    return Fund(dansk_tal(vaerdi_tekst), stykke[:1000], "model")


def find_vaerdi(tekst: str, dok: KbDocument) -> Fund:
    if not (dok.verify_hint or "").strip():
        raise KontrolFejl("Satsen har intet hint til, hvordan værdien findes på siden")
    return find_med_regel(tekst, dok.verify_hint) or find_med_model(tekst, dok)


def _udkast(dok: KbDocument, vaerdi: Decimal, nu: datetime) -> KbDocument:
    return KbDocument(title=dok.title, category=dok.category, body=dok.body, value=vaerdi, unit=dok.unit,
                      source_url=dok.source_url, valid_from=None, valid_to=None, status="draft",
                      replaces_id=dok.id, last_verified_at=nu, verify_source_url=dok.verify_source_url,
                      verify_hint=dok.verify_hint, updated_by="satskontrol", updated_at=nu)


def kontroller(session: Session, dok: KbDocument, nu: datetime | None = None) -> KbRateCheck:
    """Kontrollér én sats. Tåler at køre igen: findes der allerede et uafgjort udkast med samme værdi,
    genbruges det (der oprettes ikke et nyt)."""
    nu = nu or datetime.now(timezone.utc)
    kontrol = KbRateCheck(kb_id=dok.id, checked_at=nu, previous_value=dok.value)
    try:
        tekst = hent_side(dok.verify_source_url)
        fund = find_vaerdi(tekst, dok)
    except KontrolFejl as fejl:
        kontrol.outcome, kontrol.error = "failed", str(fejl)
        session.add(kontrol)
        session.flush()
        return kontrol
    fund.vaerdi = fund.vaerdi.quantize(Decimal("0.0001"))  # samme form som i databasen (NUMERIC(15,4))
    kontrol.fetched_value, kontrol.raw_excerpt, kontrol.method = fund.vaerdi, fund.uddrag, fund.metode
    if dok.value is not None and fund.vaerdi == dok.value:
        kontrol.outcome = "unchanged"
        dok.last_verified_at = nu
    else:
        kontrol.outcome = "changed"
        eksisterende = session.scalars(
            select(KbDocument).where(KbDocument.replaces_id == dok.id, KbDocument.status == "draft",
                                     KbDocument.value == fund.vaerdi)
            .order_by(KbDocument.id.desc())
        ).first()
        udkast = eksisterende or _udkast(dok, fund.vaerdi, nu)
        if eksisterende is None:
            session.add(udkast)
            session.flush()
        kontrol.draft_id = udkast.id
    session.add(kontrol)
    session.flush()
    return kontrol


def kontroller_alle(session: Session, nu: datetime | None = None) -> list[KbRateCheck]:
    satser = session.scalars(
        select(KbDocument).where(KbDocument.category == "satser", KbDocument.status == "active",
                                 KbDocument.verify_source_url.is_not(None)).order_by(KbDocument.id)
    ).all()
    return [kontroller(session, d, nu) for d in satser]


# --- Afgørelse (et menneske) ------------------------------------------------------------------------------

class AfgoerelsesFejl(Exception):
    pass


def godkend(session: Session, kontrol: KbRateCheck, staff_id: int, gyldig_fra: date, note: str | None,
            nu: datetime | None = None) -> KbDocument:
    """Den nye sats bliver aktiv fra `gyldig_fra`; den gamle afløses dagen før."""
    nu = nu or datetime.now(timezone.utc)
    if kontrol.outcome != "changed" or kontrol.decision is not None or kontrol.draft_id is None:
        raise AfgoerelsesFejl("Kun en uafgjort ændring kan godkendes")
    udkast = session.get(KbDocument, kontrol.draft_id)
    gammel = session.get(KbDocument, kontrol.kb_id)
    if udkast is None or udkast.status != "draft":
        raise AfgoerelsesFejl("Udkastet er allerede behandlet")
    if gammel is None or gammel.status != "active":
        raise AfgoerelsesFejl("Den gamle sats er ikke længere aktiv – ret i videnbasen i stedet")
    if gammel.valid_from and gyldig_fra <= gammel.valid_from:
        raise AfgoerelsesFejl("Den nye sats skal gælde fra en dag efter, den gamle begyndte at gælde")
    kontrol.decision, kontrol.reviewed_by, kontrol.reviewed_at = "approved", staff_id, nu
    kontrol.review_note = (note or "").strip() or None
    session.flush()  # godkendelsen skal findes, før databasen tillader udkastet at blive aktivt
    udkast.status, udkast.valid_from, udkast.updated_at = "active", gyldig_fra, nu
    gammel.status, gammel.valid_to, gammel.updated_at = "superseded", gyldig_fra - timedelta(days=1), nu
    session.flush()
    return udkast


def afvis(session: Session, kontrol: KbRateCheck, staff_id: int, note: str, nu: datetime | None = None) -> None:
    """Afvis en ny værdi ('rejected') eller kvittér for en fejl ('noted'). Den gamle sats forbliver aktiv."""
    if kontrol.decision is not None:
        raise AfgoerelsesFejl("Kontrollen er allerede afgjort")
    if kontrol.outcome == "unchanged":
        raise AfgoerelsesFejl("En uændret kontrol skal ikke afgøres")
    if not note.strip():
        raise AfgoerelsesFejl("Skriv kort, hvorfor")
    nu = nu or datetime.now(timezone.utc)
    kontrol.decision = "rejected" if kontrol.outcome == "changed" else "noted"
    kontrol.reviewed_by, kontrol.reviewed_at, kontrol.review_note = staff_id, nu, note.strip()
    session.flush()
