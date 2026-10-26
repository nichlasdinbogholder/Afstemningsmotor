"""Kontaktpersoner (Kundeoversigten, trin 3 – bygherren 26.10.2026).

- Flere pr. kunde. PRÆCIS én primær blandt dem, der ikke er arkiveret: den første bliver automatisk primær; en ny
  primær afløser den gamle; den primære kan ikke arkiveres, så længe der er andre (vælg en ny først).
- "Underskriver" og "må kontaktes af" er to uafhængige felter. ALLE medarbejdere ser alle kontakter; en kontakt, der
  kun må kontaktes af den ansvarlige, vises tydeligt som sådan.
- Kun kundens ansvarlige, kundens assistent og administratorer kan oprette, rette og arkivere (`maa_rette`).
- Kontakter slettes aldrig – de arkiveres. Alt skrives i kundens log (kunde_log) med, hvem der gjorde det.
- KUNDENS REVISOR OG BANKRÅDGIVER (bygherren 26.10.2026): vælges fra det fælles RÅDGIVERREGISTER (`Raadgiver`), da
  mange kunder har den samme. Findes rådgiveren ikke, kan ansvarlig/assistent/admin oprette den, når den tilknyttes.
  KUN administratorer retter en rådgiver – rettelsen gælder alle kunder med rådgiveren. En rådgiver er aldrig primær
  (den primære er kundens egen kontakt) og aldrig underskriver – også håndhævet i databasen – og tæller derfor ikke
  med, når den primære vælges eller arkiveres. Fjernes rådgiveren fra en kunde, arkiveres tilknytningen; registret
  røres ikke.
"""

import re
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.kunder.models import KONTAKTES_AF, RAADGIVER_TYPER, Client, Contact, Raadgiver
from app.kunder.roller import arbejder_paa
from app.kunder.tabeller import KundeLog
from app.personale.models import Staff
from app.tid import TIDSZONE

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RAADGIVER_TEKST = {"revisor": "revisor", "bank": "bankrådgiver"}
FIRMA_TEKST = {"revisor": "revisionsfirma", "bank": "bank"}


class KontaktFejl(Exception):
    """Fejl ved indtastning. Beskeden er dansk og kan vises direkte."""


class IkkeTilladt(Exception):
    pass


def maa_rette(kunde: Client, medarbejder: Staff) -> bool:
    return medarbejder.rolle == "admin" or arbejder_paa(kunde, medarbejder)


def _kontroller(kunde: Client | None, af: Staff) -> Client:
    if kunde is None:
        raise KontaktFejl("Kunden findes ikke")
    if not maa_rette(kunde, af):
        raise IkkeTilladt()
    return kunde


def raadgiver_json(r: Raadgiver) -> dict:
    return {"id": r.id, "type": r.type, "navn": r.navn, "firma": r.firma, "telefon": r.telefon, "email": r.email}


def json(k: Contact) -> dict:
    """En rådgivers navn, firma, telefon og mail kommer fra registret – så en rettelse ses hos alle kunder."""
    r = k.raadgiver
    return {"id": k.id, "navn": r.navn if r else k.navn, "rolle": None if r else k.rolle,
            "telefon": r.telefon if r else k.telefon, "email": r.email if r else k.email,
            "primaer": k.primaer, "underskriver": k.underskriver, "maa_kontaktes_af": k.maa_kontaktes_af,
            "raadgiver": raadgiver_json(r) if r else None,
            "arkiveret": k.arkiveret.isoformat() if k.arkiveret else None}


def liste(session: Session, client_id: int, med_arkiverede: bool = False) -> list[Contact]:
    q = select(Contact).where(Contact.client_id == client_id)
    if not med_arkiverede:
        q = q.where(Contact.arkiveret.is_(None))
    return list(session.scalars(q.order_by(Contact.arkiveret.is_not(None), Contact.primaer.desc(),
                                           Contact.raadgiver_id.is_not(None),
                                           Contact.navn)))


def _egne(kontakter: list[Contact]) -> list[Contact]:
    """Kundens egne kontakter – uden revisorer og bankrådgivere."""
    return [k for k in kontakter if k.raadgiver_id is None]


def _rens(navn, rolle, telefon, email, maa_kontaktes_af) -> tuple:
    navn = (navn or "").strip()
    if not navn:
        raise KontaktFejl("Skriv kontaktpersonens navn")
    email = (email or "").strip().lower() or None
    if email and not EMAIL.match(email):
        raise KontaktFejl("Det er ikke en gyldig e-mailadresse")
    if maa_kontaktes_af not in KONTAKTES_AF:
        raise KontaktFejl("Vælg, hvem der må kontakte personen")
    return navn, (rolle or "").strip() or None, (telefon or "").strip() or None, email


def _log(session: Session, kunde: Client, af: Staff, event: str, detail: str) -> None:
    session.add(KundeLog(client_id=kunde.id, event=event, detail=detail, actor_staff_id=af.id))


def _goer_primaer(session: Session, kontakt: Contact) -> None:
    """Den gamle primære afløses først – databasen tillader kun én ad gangen."""
    for anden in liste(session, kontakt.client_id):
        if anden.primaer and anden.id != kontakt.id:
            anden.primaer = False
    session.flush()
    kontakt.primaer = True


def opret(session: Session, af: Staff, client_id: int, navn: str, rolle: str | None, telefon: str | None,
          email: str | None, primaer: bool, underskriver: bool, maa_kontaktes_af: str) -> Contact:
    """Kundens egen kontaktperson (rådgivere tilknyttes med `tilknyt_raadgiver`)."""
    kunde = _kontroller(session.get(Client, client_id), af)
    navn, rolle, telefon, email = _rens(navn, rolle, telefon, email, maa_kontaktes_af)
    foerste = not _egne(liste(session, kunde.id))
    kontakt = Contact(client_id=kunde.id, navn=navn, rolle=rolle, telefon=telefon, email=email, primaer=False,
                      underskriver=underskriver, maa_kontaktes_af=maa_kontaktes_af)
    session.add(kontakt)
    session.flush()
    if primaer or foerste:                       # kundens første egne kontakt er altid primær
        _goer_primaer(session, kontakt)
    _log(session, kunde, af, "kontakt_oprettet", navn + (" (primær)" if kontakt.primaer else ""))
    session.flush()
    return kontakt


# --- Rådgiverregistret (revisorer og bankrådgivere) ------------------------------------------------------------

def raadgivere(session: Session, type_: str, soeg: str | None = None) -> list[Raadgiver]:
    q = select(Raadgiver).where(Raadgiver.type == type_)
    if soeg and soeg.strip():
        m = f"%{soeg.strip().lower()}%"
        q = q.where(or_(func.lower(Raadgiver.navn).like(m), func.lower(Raadgiver.firma).like(m)))
    return list(session.scalars(q.order_by(Raadgiver.firma, Raadgiver.navn).limit(50)))


def _rens_raadgiver(session: Session, type_: str, navn, firma, telefon, email, undtagen: int | None = None) -> tuple:
    if type_ not in RAADGIVER_TYPER:
        raise KontaktFejl("Ukendt type rådgiver")
    navn, _, telefon, email = _rens(navn, None, telefon, email, "alle")
    firma = (firma or "").strip()
    if not firma:
        raise KontaktFejl(f"Skriv {RAADGIVER_TEKST[type_]}ens {FIRMA_TEKST[type_]}")
    findes = session.scalars(select(Raadgiver).where(
        Raadgiver.type == type_, func.lower(Raadgiver.navn) == navn.lower(),
        func.lower(Raadgiver.firma) == firma.lower(), Raadgiver.id != (undtagen or 0))).first()
    if findes is not None:
        raise KontaktFejl(f"{navn} ({firma}) findes allerede i registret – vælg {RAADGIVER_TEKST[type_]}en på listen")
    return navn, firma, telefon, email


def tilknyt_raadgiver(session: Session, af: Staff, client_id: int, type_: str, raadgiver_id: int | None,
                      ny: dict | None, maa_kontaktes_af: str) -> Contact:
    """Tilknyt en rådgiver fra registret – eller opret den i registret først (`ny`: navn, firma, telefon, email)."""
    kunde = _kontroller(session.get(Client, client_id), af)
    if maa_kontaktes_af not in KONTAKTES_AF:
        raise KontaktFejl("Vælg, hvem der må kontakte personen")
    if raadgiver_id is not None:
        r = session.get(Raadgiver, raadgiver_id)
        if r is None or r.type != type_:
            raise KontaktFejl(f"Vælg en {RAADGIVER_TEKST[type_]} fra listen")
    elif ny is not None:
        navn, firma, telefon, email = _rens_raadgiver(session, type_, ny.get("navn"), ny.get("firma"),
                                                      ny.get("telefon"), ny.get("email"))
        r = Raadgiver(type=type_, navn=navn, firma=firma, telefon=telefon, email=email, oprettet_af=af.id)
        session.add(r)
        session.flush()
        session.add(AuditLog(staff_id=af.id, handling="raadgiver_oprettet", detaljer={
            "raadgiver_id": r.id, "type": type_, "navn": navn, "firma": firma, "udfoert_af": af.email}))
    else:
        raise KontaktFejl(f"Vælg en {RAADGIVER_TEKST[type_]} fra listen, eller opret en ny")
    if any(k.raadgiver_id == r.id for k in liste(session, kunde.id)):
        raise KontaktFejl(f"{r.navn} er allerede tilknyttet kunden")
    kontakt = Contact(client_id=kunde.id, navn=r.navn, raadgiver_id=r.id, primaer=False, underskriver=False,
                      maa_kontaktes_af=maa_kontaktes_af)
    kontakt.raadgiver = r
    session.add(kontakt)
    _log(session, kunde, af, "kontakt_oprettet", f"{r.navn} ({RAADGIVER_TEKST[type_]}, {r.firma})")
    session.flush()
    return kontakt


def ret_raadgiver(session: Session, af: Staff, raadgiver_id: int, navn: str, firma: str, telefon: str | None,
                  email: str | None) -> Raadgiver:
    """KUN administratorer: rettelsen gælder alle kunder med rådgiveren."""
    if af.rolle != "admin":
        raise IkkeTilladt()
    r = session.get(Raadgiver, raadgiver_id)
    if r is None:
        raise KontaktFejl("Rådgiveren findes ikke")
    fra = raadgiver_json(r)
    r.navn, r.firma, r.telefon, r.email = _rens_raadgiver(session, r.type, navn, firma, telefon, email,
                                                          undtagen=r.id)
    for k in session.scalars(select(Contact).where(Contact.raadgiver_id == r.id)):
        k.navn = r.navn                          # holdes ens med registret, så listerne kan sorteres
    session.add(AuditLog(staff_id=af.id, handling="raadgiver_aendret", detaljer={
        "raadgiver_id": r.id, "fra": fra, "til": raadgiver_json(r), "udfoert_af": af.email}))
    session.flush()
    return r


def ret(session: Session, af: Staff, kontakt_id: int, navn: str, rolle: str | None, telefon: str | None,
        email: str | None, underskriver: bool, maa_kontaktes_af: str) -> Contact:
    kontakt = session.get(Contact, kontakt_id)
    if kontakt is None:
        raise KontaktFejl("Kontaktpersonen findes ikke")
    kunde = _kontroller(session.get(Client, kontakt.client_id), af)
    if kontakt.arkiveret is not None:
        raise KontaktFejl("Kontaktpersonen er arkiveret og kan ikke rettes")
    if kontakt.raadgiver_id is not None:
        raise KontaktFejl("En rådgivers oplysninger rettes i rådgiverregistret (kun administratorer)")
    navn, rolle, telefon, email = _rens(navn, rolle, telefon, email, maa_kontaktes_af)
    kontakt.navn, kontakt.rolle, kontakt.telefon, kontakt.email = navn, rolle, telefon, email
    kontakt.underskriver, kontakt.maa_kontaktes_af = underskriver, maa_kontaktes_af
    _log(session, kunde, af, "kontakt_rettet", navn)
    session.flush()
    return kontakt


def saet_primaer(session: Session, af: Staff, kontakt_id: int) -> Contact:
    kontakt = session.get(Contact, kontakt_id)
    if kontakt is None:
        raise KontaktFejl("Kontaktpersonen findes ikke")
    kunde = _kontroller(session.get(Client, kontakt.client_id), af)
    if kontakt.arkiveret is not None:
        raise KontaktFejl("En arkiveret kontaktperson kan ikke være primær")
    if kontakt.raadgiver_id is not None:
        raise KontaktFejl("En rådgiver kan ikke være primær kontaktperson – den primære er kundens egen kontakt")
    if not kontakt.primaer:
        _goer_primaer(session, kontakt)
        _log(session, kunde, af, "kontakt_primaer", kontakt.navn)
        session.flush()
    return kontakt


def arkiver(session: Session, af: Staff, kontakt_id: int) -> Contact:
    kontakt = session.get(Contact, kontakt_id)
    if kontakt is None:
        raise KontaktFejl("Kontaktpersonen findes ikke")
    kunde = _kontroller(session.get(Client, kontakt.client_id), af)
    if kontakt.arkiveret is not None:
        return kontakt
    andre = [k for k in _egne(liste(session, kunde.id)) if k.id != kontakt.id]
    if kontakt.primaer and andre:
        raise KontaktFejl("Det er den primære kontaktperson – vælg en anden som primær først")
    kontakt.primaer = False
    kontakt.arkiveret = datetime.now(TIDSZONE)
    _log(session, kunde, af, "kontakt_arkiveret", kontakt.navn)
    session.flush()
    return kontakt
