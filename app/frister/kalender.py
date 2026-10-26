"""Skats fristkalender og listen i AI Planlægning (bygherren 26.10.2026).

- Fristerne slås op – de beregnes aldrig. Kun GODKENDTE frister bruges.
- KUN administratorer lægger frister ind (godkendt med det samme, kilden på skat.dk skal angives) og tilbagekalder
  dem. Senere henter Svend dem fra skat.dk som udkast, som en administrator godkender (`godkend`).
- Listen (`kommende`): for hver kunde i drift (aktiv/onboarding) med en ordning på fanen Frister findes de godkendte
  frister i perioden. Mangler Skats datoer for en ordning, eller mangler en kunde sit valg, vises det som advarsel –
  så intet stille forsvinder fra planlægningen.
- Alt skrives i audit_log.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.frister.models import ORDNINGER, SkatFrist
from app.kunder.models import I_DRIFT, Client
from app.kunder.tabeller import KundeObligations
from app.personale.models import Staff
from app.tid import TIDSZONE

AFGIFT_TEKST = {"moms": "Moms", "loensumsafgift": "Lønsumsafgift"}
ORDNING_TEKST = {"maaned": "Månedlig", "kvartal": "Kvartalsvis", "halvaar": "Halvårlig", "metode_1": "Metode 1",
                 "metode_2": "Metode 2", "metode_3": "Metode 3", "metode_4": "Metode 4"}
MAANEDER = ("januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober",
            "november", "december")
# Kolonnen i kunde_obligations for hver afgift.
KOLONNE = {"moms": "vat_period", "loensumsafgift": "loensumsafgift_metode"}


class FristFejl(Exception):
    """Fejl ved indtastning. Beskeden er dansk og kan vises direkte."""


class IkkeTilladt(Exception):
    pass


def periode_tekst(fra: date, til: date) -> str:
    """Fx "juni 2026", "2. kvartal 2026", "1. halvår 2026" – ellers datoerne."""
    maaneder = (til.year - fra.year) * 12 + til.month - fra.month + 1
    hele = fra.day == 1 and (til + timedelta(days=1)).day == 1      # fra den 1. til sidste dag i en måned
    if hele and maaneder == 1:
        return f"{MAANEDER[fra.month - 1]} {fra.year}"
    if hele and maaneder == 3 and fra.month in (1, 4, 7, 10):
        return f"{(fra.month - 1) // 3 + 1}. kvartal {fra.year}"
    if hele and maaneder == 6 and fra.month in (1, 7):
        return f"{1 if fra.month == 1 else 2}. halvår {fra.year}"
    if hele and maaneder == 12 and fra.month == 1:
        return f"år {fra.year}"
    return f"{fra:%d.%m.%Y}–{til:%d.%m.%Y}"


def json(f: SkatFrist) -> dict:
    return {"id": f.id, "afgift": f.afgift, "afgift_tekst": AFGIFT_TEKST[f.afgift], "ordning": f.ordning,
            "ordning_tekst": ORDNING_TEKST[f.ordning], "periode_fra": f.periode_fra.isoformat(),
            "periode_til": f.periode_til.isoformat(), "periode": periode_tekst(f.periode_fra, f.periode_til),
            "frist": f.frist.isoformat(), "kilde": f.kilde, "status": f.status}


def kalender(session: Session, med_tilbagekaldte: bool = False) -> list[SkatFrist]:
    q = select(SkatFrist)
    if not med_tilbagekaldte:
        q = q.where(SkatFrist.status != "afvist")
    return list(session.scalars(q.order_by(SkatFrist.afgift, SkatFrist.ordning, SkatFrist.periode_fra)))


def tilfoej(session: Session, af: Staff, afgift: str, ordning: str, periode_fra: date, periode_til: date,
            frist: date, kilde: str) -> SkatFrist:
    """En administrator lægger en frist ind fra Skats fristkalender – godkendt med det samme."""
    if af.rolle != "admin":
        raise IkkeTilladt()
    if afgift not in ORDNINGER or ordning not in ORDNINGER[afgift]:
        raise FristFejl("Vælg afgift og ordning")
    if periode_til < periode_fra:
        raise FristFejl("Periodens slutning ligger før dens start")
    if frist <= periode_til:
        raise FristFejl("Fristen skal ligge efter periodens slutning")
    kilde = (kilde or "").strip()
    if not kilde.startswith("https://skat.dk/") and not kilde.startswith("https://info.skat.dk/"):
        raise FristFejl("Angiv siden på skat.dk, hvor fristen står (adressen skal begynde med https://skat.dk/)")
    findes = session.scalars(select(SkatFrist).where(
        SkatFrist.afgift == afgift, SkatFrist.ordning == ordning, SkatFrist.periode_fra == periode_fra,
        SkatFrist.status == "godkendt")).first()
    if findes is not None:
        raise FristFejl(f"Der er allerede en godkendt frist for {AFGIFT_TEKST[afgift].lower()} "
                        f"{periode_tekst(periode_fra, periode_til)} – tilbagekald den først")
    nu = datetime.now(TIDSZONE)
    f = SkatFrist(afgift=afgift, ordning=ordning, periode_fra=periode_fra, periode_til=periode_til, frist=frist,
                  kilde=kilde, status="godkendt", oprettet_af=af.id, godkendt_af=af.id, godkendt_at=nu)
    session.add(f)
    session.flush()
    session.add(AuditLog(staff_id=af.id, handling="skat_frist_tilfoejet", detaljer=json(f) | {"udfoert_af": af.email}))
    session.flush()
    return f


def godkend(session: Session, af: Staff, frist_id: int) -> SkatFrist:
    """Et udkast (fx hentet af Svend) godkendes af en administrator."""
    if af.rolle != "admin":
        raise IkkeTilladt()
    f = session.get(SkatFrist, frist_id)
    if f is None or f.status != "udkast":
        raise FristFejl("Kun et udkast kan godkendes")
    f.status, f.godkendt_af, f.godkendt_at = "godkendt", af.id, datetime.now(TIDSZONE)
    session.add(AuditLog(staff_id=af.id, handling="skat_frist_godkendt", detaljer=json(f) | {"udfoert_af": af.email}))
    session.flush()
    return f


def tilbagekald(session: Session, af: Staff, frist_id: int) -> SkatFrist:
    if af.rolle != "admin":
        raise IkkeTilladt()
    f = session.get(SkatFrist, frist_id)
    if f is None or f.status == "afvist":
        raise FristFejl("Fristen findes ikke eller er allerede tilbagekaldt")
    f.status, f.afvist_af, f.afvist_at = "afvist", af.id, datetime.now(TIDSZONE)
    session.add(AuditLog(staff_id=af.id, handling="skat_frist_tilbagekaldt", detaljer=json(f) | {"udfoert_af": af.email}))
    session.flush()
    return f


@dataclass
class Filtre:
    mine: bool = False
    ansvarlig_id: int | None = None
    afgift: str | None = None


def kommende(session: Session, medarbejder: Staff, fra: date, til: date, f: Filtre) -> dict:
    """Godkendte frister med fristdato fra..til for kunderne i drift – nærmeste først – samt advarsler."""
    kunder = list(session.scalars(select(Client).where(Client.status.in_(I_DRIFT)).order_by(Client.navn)))
    if f.mine:
        kunder = [k for k in kunder if medarbejder.id in (k.ansvarlig_medarbejder_id, k.assistent_id)]
    if f.ansvarlig_id is not None:
        kunder = [k for k in kunder if k.ansvarlig_medarbejder_id == f.ansvarlig_id]
    afgifter = [f.afgift] if f.afgift in AFGIFT_TEKST else list(AFGIFT_TEKST)
    navne = dict(session.execute(select(Staff.id, Staff.navn)).all())
    frister = list(session.scalars(select(SkatFrist).where(
        SkatFrist.status == "godkendt", SkatFrist.frist >= fra, SkatFrist.frist <= til)))
    # Seneste godkendte frist pr. ordning – viser, om Skats datoer er lagt ind så langt frem.
    seneste: dict[tuple[str, str], date] = {}
    for s in session.scalars(select(SkatFrist).where(SkatFrist.status == "godkendt")):
        noegle = (s.afgift, s.ordning)
        seneste[noegle] = max(seneste.get(noegle, s.frist), s.frist)

    raekker, ikke_valgt, brugte = [], {a: [] for a in afgifter}, set()
    for k in kunder:
        o = session.get(KundeObligations, k.id)
        for afgift in afgifter:
            ordning = getattr(o, KOLONNE[afgift]) if o else None
            if ordning is None:
                ikke_valgt[afgift].append(k.navn)
                continue
            if ordning == "ingen":
                continue
            brugte.add((afgift, ordning))
            for s in frister:
                if s.afgift == afgift and s.ordning == ordning:
                    raekker.append({
                        "kunde_id": k.id, "kunde": k.navn, "kundenummer": k.kundenummer,
                        "ansvarlig": navne.get(k.ansvarlig_medarbejder_id), "assistent": navne.get(k.assistent_id),
                        "afgift": afgift, "afgift_tekst": AFGIFT_TEKST[afgift], "ordning_tekst": ORDNING_TEKST[ordning],
                        "periode": periode_tekst(s.periode_fra, s.periode_til), "frist": s.frist.isoformat(),
                        "dage_til": (s.frist - fra).days, "kilde": s.kilde})
    raekker.sort(key=lambda r: (r["frist"], r["kunde"].lower(), r["afgift"]))
    advarsler = []
    for afgift, ordning in sorted(brugte):
        if seneste.get((afgift, ordning), date.min) < til:
            advarsler.append(f"Skats frister for {AFGIFT_TEKST[afgift].lower()} ({ORDNING_TEKST[ordning].lower()}) er "
                             f"ikke lagt ind helt frem til {til:%d.%m.%Y} – listen kan mangle frister.")
    for afgift, navne_ in ikke_valgt.items():
        if navne_:
            advarsler.append(f"{len(navne_)} kunde(r) mangler valg for {AFGIFT_TEKST[afgift].lower()} på fanen "
                             f"Frister: {', '.join(navne_[:10])}{' …' if len(navne_) > 10 else ''}")
    return {"raekker": raekker, "advarsler": advarsler}
