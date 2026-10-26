"""Kontaktpersoner (Kundeoversigten, trin 3 – bygherren 26.10.2026).

- Flere pr. kunde. PRÆCIS én primær blandt dem, der ikke er arkiveret: den første bliver automatisk primær; en ny
  primær afløser den gamle; den primære kan ikke arkiveres, så længe der er andre (vælg en ny først).
- "Underskriver" og "må kontaktes af" er to uafhængige felter. ALLE medarbejdere ser alle kontakter; en kontakt, der
  kun må kontaktes af den ansvarlige, vises tydeligt som sådan.
- Kun kundens ansvarlige, kundens assistent og administratorer kan oprette, rette og arkivere (`maa_rette`).
- Kontakter slettes aldrig – de arkiveres. Alt skrives i kundens log (kunde_log) med, hvem der gjorde det.
- KUNDENS REVISOR (bygherren 26.10.2026): en kontaktperson med `er_revisor` og revisionsfirmaet i `firma` (krævet).
  En revisor er aldrig primær (den primære er kundens egen kontakt) og aldrig underskriver for kunden – også
  håndhævet i databasen. Revisorer tæller derfor ikke med, når den primære vælges eller arkiveres.
"""

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kunder.models import KONTAKTES_AF, Client, Contact
from app.kunder.roller import arbejder_paa
from app.kunder.tabeller import KundeLog
from app.personale.models import Staff
from app.tid import TIDSZONE

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


def json(k: Contact) -> dict:
    return {"id": k.id, "navn": k.navn, "rolle": k.rolle, "telefon": k.telefon, "email": k.email,
            "primaer": k.primaer, "underskriver": k.underskriver, "maa_kontaktes_af": k.maa_kontaktes_af,
            "er_revisor": k.er_revisor, "firma": k.firma,
            "arkiveret": k.arkiveret.isoformat() if k.arkiveret else None}


def liste(session: Session, client_id: int, med_arkiverede: bool = False) -> list[Contact]:
    q = select(Contact).where(Contact.client_id == client_id)
    if not med_arkiverede:
        q = q.where(Contact.arkiveret.is_(None))
    return list(session.scalars(q.order_by(Contact.arkiveret.is_not(None), Contact.primaer.desc(), Contact.er_revisor,
                                           Contact.navn)))


def _egne(kontakter: list[Contact]) -> list[Contact]:
    """Kundens egne kontakter – uden revisorer."""
    return [k for k in kontakter if not k.er_revisor]


def _firma(er_revisor: bool, firma: str | None) -> str | None:
    firma = (firma or "").strip() or None
    if er_revisor and not firma:
        raise KontaktFejl("Skriv revisorens revisionsfirma")
    return firma if er_revisor else None


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


def _logtekst(k: Contact) -> str:
    return k.navn + (f" (revisor, {k.firma})" if k.er_revisor else " (primær)" if k.primaer else "")


def opret(session: Session, af: Staff, client_id: int, navn: str, rolle: str | None, telefon: str | None,
          email: str | None, primaer: bool, underskriver: bool, maa_kontaktes_af: str,
          er_revisor: bool = False, firma: str | None = None) -> Contact:
    kunde = _kontroller(session.get(Client, client_id), af)
    navn, rolle, telefon, email = _rens(navn, rolle, telefon, email, maa_kontaktes_af)
    firma = _firma(er_revisor, firma)
    if er_revisor and (primaer or underskriver):
        raise KontaktFejl("En revisor kan ikke være primær kontaktperson eller underskriver for kunden")
    foerste = not _egne(liste(session, kunde.id))
    kontakt = Contact(client_id=kunde.id, navn=navn, rolle=rolle, telefon=telefon, email=email, primaer=False,
                      underskriver=underskriver, maa_kontaktes_af=maa_kontaktes_af, er_revisor=er_revisor, firma=firma)
    session.add(kontakt)
    session.flush()
    if not er_revisor and (primaer or foerste):  # kundens første egne kontakt er altid primær
        _goer_primaer(session, kontakt)
    _log(session, kunde, af, "kontakt_oprettet", _logtekst(kontakt))
    session.flush()
    return kontakt


def ret(session: Session, af: Staff, kontakt_id: int, navn: str, rolle: str | None, telefon: str | None,
        email: str | None, underskriver: bool, maa_kontaktes_af: str, er_revisor: bool = False,
        firma: str | None = None) -> Contact:
    kontakt = session.get(Contact, kontakt_id)
    if kontakt is None:
        raise KontaktFejl("Kontaktpersonen findes ikke")
    kunde = _kontroller(session.get(Client, kontakt.client_id), af)
    if kontakt.arkiveret is not None:
        raise KontaktFejl("Kontaktpersonen er arkiveret og kan ikke rettes")
    navn, rolle, telefon, email = _rens(navn, rolle, telefon, email, maa_kontaktes_af)
    firma = _firma(er_revisor, firma)
    if er_revisor and kontakt.primaer:
        raise KontaktFejl("Det er den primære kontaktperson – vælg en anden som primær, før den gøres til revisor")
    if er_revisor and underskriver:
        raise KontaktFejl("En revisor kan ikke være underskriver for kunden")
    kontakt.navn, kontakt.rolle, kontakt.telefon, kontakt.email = navn, rolle, telefon, email
    kontakt.underskriver, kontakt.maa_kontaktes_af = underskriver, maa_kontaktes_af
    kontakt.er_revisor, kontakt.firma = er_revisor, firma
    _log(session, kunde, af, "kontakt_rettet", navn + (f" (revisor, {firma})" if er_revisor else ""))
    session.flush()
    return kontakt


def saet_primaer(session: Session, af: Staff, kontakt_id: int) -> Contact:
    kontakt = session.get(Contact, kontakt_id)
    if kontakt is None:
        raise KontaktFejl("Kontaktpersonen findes ikke")
    kunde = _kontroller(session.get(Client, kontakt.client_id), af)
    if kontakt.arkiveret is not None:
        raise KontaktFejl("En arkiveret kontaktperson kan ikke være primær")
    if kontakt.er_revisor:
        raise KontaktFejl("En revisor kan ikke være primær kontaktperson – den primære er kundens egen kontakt")
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
