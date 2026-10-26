"""CVR-registret (Kundeoversigten, trin 2): stamdata hentes fra Erhvervsstyrelsens "CVR system-til-system"-adgang.

- CVR er KILDEN til stamdata. Forskelle skrives i `kunde_cvr_changes` – de skrives ALDRIG ind over kundens felter
  (fast regel 10). Det seneste svar gemmes i `kunde_cvr_snapshot`.
- Opslag, når en kunde oprettes med CVR (job i kø), og dagligt for alle aktive kunder (KUNDE_CVR_SYNC_CRON).
- Status med konkurs eller tvangsopløsning er 'kritisk' og sendes samme dag som notifikation til den ansvarlige og
  alle administratorer. Tidligere navne lægges i `kunde_aliases`, så søgningen finder dem.
- Kun læsning. Uden KUNDE_CVR_API_BRUGER/KUNDE_CVR_API_KODE er opslag slået fra (intet hentes, intet fejler).
- Gætter aldrig: et felt, der ikke er i svaret, bliver tomt. Et svar med et andet CVR-nummer eller flere
  virksomheder afvises. Reelle ejere læses først i trin 15 – når der er et rigtigt svar at bygge på.

Kommando:
    python -m app.kunder.cvr proeve --cvr 12345678 [--felter]   (henter og viser – gemmer intet)
    python -m app.kunder.cvr opdater --kundenummer <nr>          (henter og sammenligner nu)
    python -m app.kunder.cvr aendringer [--kundenummer <nr>]     (forskelle, der ikke er håndteret)
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.jobs.koe import laeg_i_koe
from app.jobs.register import JobKontekst, jobtype
from app.kunder.models import I_DRIFT, I_OVERSIGTEN, Client
from app.kunder.tabeller import KundeAlias, KundeCvrChange, KundeCvrSnapshot, KundeLog
from app.notifikationer.tjeneste import administratorer, send_notifikation
from app.sikkerhed.hemmeligheder import registrer_hemmelighed
from app.tid import TIDSZONE

JOBTYPE = "kunde_cvr_opdater"

# Felterne, der sammenlignes, deres navn på dansk og hvor alvorlig en ændring er. Status med konkurs eller
# tvangsopløsning er altid 'kritisk' (spec 26.10.2026); de øvrige niveauer kan rettes her.
FELTER = {
    "name": ("Navn", "info"),
    "address": ("Adresse", "info"),
    "company_form": ("Virksomhedsform", "advarsel"),
    "industry_code": ("Branchekode", "info"),
    "fiscal_year_end": ("Regnskabsårets slutning", "advarsel"),
    "status": ("Status", "advarsel"),
}
KRITISK_STATUS = re.compile(r"konkurs|tvangsopl", re.IGNORECASE)


class CvrFejl(Exception):
    """Opslaget kunne ikke gennemføres. Beskeden er dansk og indeholder aldrig adgangskoden."""


class _ProevIgen(Exception):
    pass


@dataclass(frozen=True)
class CvrData:
    cvr: str
    name: str | None
    address: str | None
    company_form: str | None
    industry_code: str | None
    fiscal_year_end: str | None          # 'dd-mm'
    status: str | None
    tidligere_navne: list[str] = field(default_factory=list)

    def felter(self) -> dict[str, str | None]:
        return {f: getattr(self, f) for f in FELTER}


def cvr_slaaet_til() -> bool:
    s = get_settings()
    return bool(s.kunde_cvr_api_bruger and s.kunde_cvr_api_kode)


# --- Hentning ---------------------------------------------------------------------------------------------------

def hent_raat(cvr: str, transport: httpx.BaseTransport | None = None, vent=None) -> dict:
    """Svaret fra CVR for ét CVR-nummer (søgning efter præcis det nummer). Prøver 3 gange ved 429/5xx/netværk."""
    if not re.fullmatch(r"[0-9]{8}", cvr or ""):
        raise CvrFejl("CVR-nummeret skal være 8 cifre")
    s = get_settings()
    if not cvr_slaaet_til():
        raise CvrFejl("CVR-opslag er ikke slået til (KUNDE_CVR_API_BRUGER/KUNDE_CVR_API_KODE mangler)")
    kode = s.kunde_cvr_api_kode.get_secret_value()
    registrer_hemmelighed(kode)
    foresp = {"query": {"term": {"Vrvirksomhed.cvrNummer": int(cvr)}}, "size": 2}
    try:
        with httpx.Client(timeout=30.0, transport=transport, auth=(s.kunde_cvr_api_bruger, kode)) as http:
            for forsoeg in Retrying(retry=retry_if_exception(lambda f: isinstance(f, _ProevIgen)),
                                    wait=vent or wait_exponential(multiplier=1, max=20),
                                    stop=stop_after_attempt(3), reraise=True):
                with forsoeg:
                    try:
                        svar = http.post(s.kunde_cvr_api_url, json=foresp)
                    except httpx.TransportError:
                        raise _ProevIgen() from None
                    if svar.status_code == 429 or svar.status_code >= 500:
                        raise _ProevIgen()
    except _ProevIgen:
        raise CvrFejl("CVR-registret svarede ikke – prøvet 3 gange") from None
    if svar.status_code in (401, 403):
        raise CvrFejl(f"CVR-registret afviste brugernavn eller adgangskode (status {svar.status_code})")
    if svar.status_code != 200:
        raise CvrFejl(f"CVR-registret svarede med fejl (status {svar.status_code})")
    try:
        return svar.json()
    except ValueError:
        raise CvrFejl("CVR-registret sendte et svar, der ikke kunne læses") from None


def _tekst(v) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _adresse(a: dict | None) -> str | None:
    if not a:
        return None
    nummer = "".join(str(x) for x in (a.get("husnummerFra"), a.get("bogstavFra")) if x not in (None, ""))
    gade = " ".join(x for x in (_tekst(a.get("vejnavn")), nummer or None) if x)
    etage = ", ".join(x for x in (_tekst(a.get("etage")), _tekst(a.get("sidedoer"))) if x)
    by = " ".join(x for x in (_tekst(a.get("postnummer")), _tekst(a.get("postdistrikt"))) if x)
    dele = [x for x in (_tekst(a.get("conavn")) and f"c/o {a['conavn']}", gade or None, etage or None, by or None)
            if x]
    return ", ".join(dele) or None


def _regnskabsaar_slut(attributter: list | None) -> str | None:
    """Attributten REGNSKABSÅR_SLUT – den gældende værdi, fx '--12-31' -> '31-12'. Ukendt form -> tom."""
    for a in attributter or []:
        if a.get("type") != "REGNSKABSÅR_SLUT":
            continue
        gaeldende = [v for v in a.get("vaerdier") or [] if not (v.get("periode") or {}).get("gyldigTil")]
        if len(gaeldende) != 1:
            return None
        m = re.fullmatch(r"--(\d{2})-(\d{2})", str(gaeldende[0].get("vaerdi") or ""))
        return f"{m[2]}-{m[1]}" if m else None
    return None


def laes(svar: dict, cvr: str) -> CvrData | None:
    """Svaret fra CVR → CvrData. None = CVR-nummeret findes ikke. Flere virksomheder eller et andet nummer: fejl."""
    hits = (svar.get("hits") or {}).get("hits")
    if hits is None:
        raise CvrFejl("Svaret fra CVR-registret havde ikke den forventede form")
    if not hits:
        return None
    if len(hits) > 1:
        raise CvrFejl("CVR-registret returnerede flere virksomheder for samme nummer")
    v = ((hits[0].get("_source") or {}).get("Vrvirksomhed")) or {}
    if str(v.get("cvrNummer")) != cvr:
        raise CvrFejl("CVR-registret returnerede en anden virksomhed end den, der blev spurgt efter")
    meta = v.get("virksomhedMetadata") or {}
    nuvaerende = _tekst((meta.get("nyesteNavn") or {}).get("navn"))
    tidligere = []
    for n in v.get("navne") or []:
        navn = _tekst(n.get("navn"))
        if navn and (n.get("periode") or {}).get("gyldigTil") and navn != nuvaerende and navn not in tidligere:
            tidligere.append(navn)
    return CvrData(
        cvr=cvr,
        name=nuvaerende,
        address=_adresse(meta.get("nyesteBeliggenhedsadresse")),
        company_form=_tekst((meta.get("nyesteVirksomhedsform") or {}).get("langBeskrivelse")),
        industry_code=_tekst((meta.get("nyesteHovedbranche") or {}).get("branchekode")),
        fiscal_year_end=_regnskabsaar_slut(v.get("attributter")),
        status=_tekst(meta.get("sammensatStatus")),
        tidligere_navne=tidligere,
    )


def hent(cvr: str, transport: httpx.BaseTransport | None = None, vent=None) -> CvrData | None:
    return laes(hent_raat(cvr, transport, vent), cvr)


# --- Sammenligning ----------------------------------------------------------------------------------------------

def _alvor(felt: str, ny: str | None) -> str:
    if felt == "status" and ny and KRITISK_STATUS.search(ny):
        return "kritisk"
    return FELTER[felt][1]


def _registrer(session: Session, kunde: Client, felt: str, gammel: str | None, ny: str | None, alvor: str,
               fund: list) -> None:
    """Skriv én forskel – men ikke igen, hvis præcis den samme forskel allerede venter på at blive håndteret."""
    findes = session.scalars(select(KundeCvrChange.id).where(
        KundeCvrChange.client_id == kunde.id, KundeCvrChange.field == felt,
        KundeCvrChange.new_value.is_not_distinct_from(ny), KundeCvrChange.handled_at.is_(None))).first()
    if findes is not None:
        return
    session.add(KundeCvrChange(client_id=kunde.id, field=felt, old_value=gammel, new_value=ny, severity=alvor))
    navn = FELTER[felt][0] if felt in FELTER else felt
    session.add(KundeLog(client_id=kunde.id, event="cvr_aendring",
                         detail=f"{navn}: {gammel or '(tom)'} → {ny or '(tom)'}"))
    fund.append((felt, gammel, ny, alvor))


def sammenlign(session: Session, kunde: Client, data: CvrData | None) -> list[tuple]:
    """Sammenlign CVR's svar med det seneste svar, gem det nye svar og de tidligere navne. Kundens egne felter
    røres ALDRIG. Returnerer forskellene (felt, gammel, ny, alvor)."""
    fund: list[tuple] = []
    if data is None:
        _registrer(session, kunde, "status", None, "Findes ikke i CVR", "advarsel", fund)
        session.flush()
        return fund
    snap = session.get(KundeCvrSnapshot, kunde.id)
    nye = data.felter()
    tidligere_navne = list(data.tidligere_navne)
    if snap is None:
        # Første opslag: intet at sammenligne med – men en kritisk status meldes med det samme.
        if _alvor("status", nye["status"]) == "kritisk":
            _registrer(session, kunde, "status", None, nye["status"], "kritisk", fund)
        snap = KundeCvrSnapshot(client_id=kunde.id)
        session.add(snap)
    else:
        for felt, ny in nye.items():
            gammel = getattr(snap, felt)
            if gammel != ny:
                _registrer(session, kunde, felt, gammel, ny, _alvor(felt, ny), fund)
                if felt == "name" and gammel and gammel not in tidligere_navne:
                    tidligere_navne.append(gammel)
    for felt, ny in nye.items():
        setattr(snap, felt, ny)
    snap.fetched_at = datetime.now(TIDSZONE)
    for navn in tidligere_navne:
        if session.scalars(select(KundeAlias.id).where(KundeAlias.client_id == kunde.id,
                                                       KundeAlias.alias == navn)).first() is None:
            session.add(KundeAlias(client_id=kunde.id, alias=navn, source="cvr"))
    session.flush()
    kritiske = [f for f in fund if f[3] == "kritisk"]
    if kritiske:
        send_notifikation(session, [kunde.ansvarlig_medarbejder_id, *administratorer(session)], "kritisk",
                          f"{kunde.navn}: {kritiske[0][2]}",
                          tekst="CVR-registret viser en kritisk status for kunden. Tjek kunden i dag.",
                          link="/kunder/", client_id=kunde.id)
    return fund


def opdater(session: Session, kunde: Client, transport: httpx.BaseTransport | None = None, vent=None) -> list:
    if not kunde.cvr:
        return []
    return sammenlign(session, kunde, hent(kunde.cvr, transport, vent))


# --- Job og planlægning -----------------------------------------------------------------------------------------

def laeg_opslag_i_koe(session: Session, kunde: Client, dag, prioritet: int = 100) -> bool:
    if not kunde.cvr or not cvr_slaaet_til():
        return False
    return laeg_i_koe(session, JOBTYPE, client_id=kunde.id, idempotens_noegle=f"cvr:{kunde.id}:{dag.isoformat()}",
                      prioritet=prioritet).ny


def planlaeg(session: Session, nu: datetime) -> int:
    """Dagligt: ét opslag pr. aktiv kunde med CVR-nummer (idempotent pr. dag)."""
    if not cvr_slaaet_til():
        return 0
    dag = nu.astimezone(TIDSZONE).date()
    kunder = session.scalars(select(Client).where(Client.status.in_(I_DRIFT), Client.cvr.is_not(None)))
    return sum(laeg_opslag_i_koe(session, k, dag) for k in kunder)


@jobtype(JOBTYPE)
def cvr_job(job: JobKontekst) -> None:
    if not cvr_slaaet_til():
        return
    kunde = job.session.get(Client, job.client_id)
    if kunde is None or kunde.status not in I_OVERSIGTEN or not kunde.cvr:
        return
    opdater(job.session, kunde)


# --- Kommando ---------------------------------------------------------------------------------------------------

def _stier(v, praefiks: str = "") -> list[str]:
    """Feltnavnene i et svar (uden værdier) – til at se, hvordan CVR's svar faktisk ser ud."""
    if isinstance(v, dict):
        return [s for k, u in v.items() for s in _stier(u, f"{praefiks}.{k}" if praefiks else k)]
    if isinstance(v, list):
        return _stier(v[0], praefiks + "[]") if v else [praefiks + "[]"]
    return [praefiks]


def main(argv: list[str] | None = None) -> int:
    import app.models  # noqa: F401
    from app.db import ny_session

    parser = argparse.ArgumentParser(description="CVR-opslag for kunderne (kun læsning).")
    under = parser.add_subparsers(dest="kommando", required=True)
    p = under.add_parser("proeve", help="hent og vis ét CVR-nummer – gemmer intet")
    p.add_argument("--cvr", required=True)
    p.add_argument("--felter", action="store_true", help="vis også feltnavnene i CVR's svar")
    o = under.add_parser("opdater", help="hent og sammenlign én kunde nu")
    o.add_argument("--kundenummer", required=True)
    a = under.add_parser("aendringer", help="vis forskelle, der ikke er håndteret")
    a.add_argument("--kundenummer")
    args = parser.parse_args(argv)
    try:
        if args.kommando == "proeve":
            raat = hent_raat(args.cvr)
            data = laes(raat, args.cvr)
            if data is None:
                print(f"CVR {args.cvr} findes ikke i CVR-registret.")
                return 1
            for felt, (navn, _) in FELTER.items():
                print(f"{navn:<25} {getattr(data, felt) or '(tom – ikke i svaret)'}")
            print(f"{'Tidligere navne':<25} {', '.join(data.tidligere_navne) or '(ingen)'}")
            if args.felter:
                print("\nFeltnavne i svaret:")
                for sti in sorted(set(_stier(raat))):
                    print("  " + sti)
            return 0
        with ny_session() as session:
            q = select(Client)
            if args.kundenummer:
                q = q.where(Client.kundenummer == args.kundenummer)
            if args.kommando == "opdater":
                kunde = session.scalars(q).one_or_none()
                if kunde is None:
                    print("Fejl: Kunden findes ikke", file=sys.stderr)
                    return 1
                fund = opdater(session, kunde)
                session.commit()
                print(f"{kunde.navn}: {len(fund)} ny(e) forskel(le)")
                for felt, gammel, ny, alvor in fund:
                    print(f"  [{alvor}] {FELTER.get(felt, (felt,))[0]}: {gammel or '(tom)'} → {ny or '(tom)'}")
                return 0
            aendringer = session.execute(
                select(KundeCvrChange, Client.navn).join(Client, Client.id == KundeCvrChange.client_id)
                .where(KundeCvrChange.handled_at.is_(None),
                       *([Client.kundenummer == args.kundenummer] if args.kundenummer else []))
                .order_by(KundeCvrChange.detected_at.desc())).all()
            for c, navn in aendringer:
                print(f"{c.detected_at:%d.%m.%Y} [{c.severity}] {navn} – {FELTER.get(c.field, (c.field,))[0]}: "
                      f"{c.old_value or '(tom)'} → {c.new_value or '(tom)'}")
            if not aendringer:
                print("Ingen forskelle, der venter.")
            return 0
    except CvrFejl as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
