"""Fanen Frister (bygherren 26.10.2026): hvor ofte kunden indberetter MOMS og LØNSUMSAFGIFT til Skat.

- Hver især: månedligt, kvartalsvis, halvårligt, årligt – eller "ikke tilmeldt". Tomt = ikke valgt endnu.
- Gemmes i kunde_obligations (ét sted). Grundlaget for planlægningen senere – fristdatoerne BEREGNES ud fra det.
- Kun kundens ansvarlige, assistent og administratorer retter. Alle ser. Hver ændring skrives i kundens log med fra/til.
"""

from sqlalchemy.orm import Session

from app.kunder.kontakter import maa_rette
from app.kunder.models import Client
from app.kunder.tabeller import LOENSUM_PERIODER, MOMSPERIODER, KundeLog, KundeObligations
from app.personale.models import Staff

PERIODE_TEKST = {"maaned": "Månedligt", "kvartal": "Kvartalsvis", "halvaar": "Halvårligt", "aar": "Årligt",
                 "ingen": "Ikke tilmeldt", None: "Ikke valgt"}
# Felterne på fanen: kode -> (dansk navn, kolonne i kunde_obligations, tilladte værdier).
FRISTER = {"moms": ("Moms", "vat_period", MOMSPERIODER),
           "loensumsafgift": ("Lønsumsafgift", "loensumsafgift_period", LOENSUM_PERIODER)}


class FristFejl(Exception):
    """Fejl ved valg. Beskeden er dansk og kan vises direkte."""


class IkkeTilladt(Exception):
    pass


def json(session: Session, kunde: Client, medarbejder: Staff) -> dict:
    o = session.get(KundeObligations, kunde.id)
    return {"frister": [{"kode": kode, "navn": navn, "periode": getattr(o, kolonne) if o else None,
                         "valg": [{"kode": v, "tekst": PERIODE_TEKST[v]} for v in vaerdier]}
                        for kode, (navn, kolonne, vaerdier) in FRISTER.items()],
            "kan_rette": maa_rette(kunde, medarbejder)}


def saet(session: Session, af: Staff, client_id: int, frist: str, periode: str | None) -> Client:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise FristFejl("Kunden findes ikke")
    if not maa_rette(kunde, af):
        raise IkkeTilladt()
    if frist not in FRISTER:
        raise FristFejl("Ukendt frist")
    navn, kolonne, vaerdier = FRISTER[frist]
    if periode is not None and periode not in vaerdier:
        raise FristFejl(f"Vælg, hvor ofte der indberettes {navn.lower()}")
    o = session.get(KundeObligations, kunde.id)
    if o is None:
        o = KundeObligations(client_id=kunde.id)
        session.add(o)
    gammel = getattr(o, kolonne)
    if gammel != periode:
        setattr(o, kolonne, periode)
        session.add(KundeLog(client_id=kunde.id, event="frist_aendret", actor_staff_id=af.id,
                             detail=f"{navn}: {PERIODE_TEKST[gammel]} → {PERIODE_TEKST[periode]}"))
    session.flush()
    return kunde
