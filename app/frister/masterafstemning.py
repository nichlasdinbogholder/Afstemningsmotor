"""MASTERAFSTEMNING TIL EKSTERN REVISOR (bygherren 26.10.2026).

Har kunden "Ekstern revisor" som årsafslutning (fanen Frister), står Din Bogholder ikke for årsregnskabet – men den samlede
afstemning skal sendes til kunden. HUB'en booker derfor automatisk 5 timer i ÉN samlet blok på én dag i Outlook-
kalenderen (KALENDER_POSTKASSE – kun bygherren laver masterafstemninger):

- VINDUET: tidligst næstkommende momsfrist efter regnskabsårets slutning (Skats godkendte fristkalender for kundens
  momsordning), senest 60 dage efter den. Er kunden ikke momsregistreret, starter vinduet første hverdag efter
  regnskabsårets slutning. Mangler momsordningen eller Skats datoer, planlægges der ikke (advarsel i AI Planlægning).
- Første ledige blok inden for arbejdstiden (KALENDER_ARBEJDSDAG_FRA/_TIL) på en hverdag. Aftaler markeret "Ledig"
  eller "Arbejder et andet sted" blokerer ikke.
- Én booking pr. kunde pr. regnskabsår (masterafstemning_bookinger). Er der ikke tid i vinduet, gives der besked i
  klokken ÉN gang, og der prøves igen hver dag.
- KALENDER_BOOK=false (standard) er TØRLØB: der vises, hvad der ville blive booket, men intet oprettes i Outlook.
- Kører hver hverdag kl. 06.20 (natkørslens scheduler). Prøv: `python -m app.frister.masterafstemning proeve`.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.config import get_settings
from app.frister.aarsafslutning import er_hverdag, foerste_hverdag
from app.frister.models import MasterafstemningBooking, SkatFrist
from app.kunder.models import I_DRIFT, Client
from app.kunder.tabeller import KundeLog, KundeObligations
from app.notifikationer.tjeneste import administratorer, send_notifikation
from app.outlook import kalender as outlook
from app.personale.models import Staff
from app.regnskab.models import AccountingYearCache
from app.tid import TIDSZONE

TIMER = 5
VINDUE_DAGE = 60
MOMSORDNINGER = ("maaned", "kvartal", "halvaar")


@dataclass
class Vindue:
    tidligst: date | None
    seneste: date | None
    mangler: str | None = None          # hvorfor der ikke kan planlægges


def vindue(session: Session, kunde: Client, regnskabsaar_slut: date) -> Vindue:
    o = session.get(KundeObligations, kunde.id)
    moms = o.vat_period if o else None
    if moms is None:
        return Vindue(None, None, "momsordningen er ikke valgt på fanen Frister")
    if moms == "ingen":
        tidligst = foerste_hverdag(regnskabsaar_slut + timedelta(days=1))
    else:
        frist = session.scalars(select(SkatFrist.frist).where(
            SkatFrist.afgift == "moms", SkatFrist.ordning == moms, SkatFrist.status == "godkendt",
            SkatFrist.frist > regnskabsaar_slut).order_by(SkatFrist.frist)).first()
        if frist is None:
            return Vindue(None, None, "Skats momsfrist efter regnskabsårets slutning er ikke lagt ind i fristkalenderen")
        tidligst = frist
    return Vindue(tidligst, tidligst + timedelta(days=VINDUE_DAGE))


def find_ledig(optagede: list[outlook.Optaget], fra: date, til: date, fra_time: int, til_time: int,
               timer: int = TIMER) -> tuple[datetime, datetime] | None:
    """Første samlede blok på `timer` timer på én hverdag mellem fra og til (begge med) inden for arbejdstiden."""
    laengde = timedelta(hours=timer)
    dag = fra
    while dag <= til:
        if er_hverdag(dag):
            start, dagslut = datetime.combine(dag, time(fra_time)), datetime.combine(dag, time(til_time))
            for o in sorted((o for o in optagede if o.slut > start and o.start < dagslut), key=lambda o: o.start):
                if start + laengde <= o.start:
                    break
                start = max(start, o.slut)
            if start + laengde <= dagslut:
                return start, start + laengde
        dag += timedelta(days=1)
    return None


@dataclass
class Resultat:
    kunde: str
    regnskabsaar_slut: date
    udfald: str                          # booket | ville_booke | ingen_tid | for_sent | mangler
    start: datetime | None = None
    slut: datetime | None = None
    tekst: str = ""


def _kalenderens_ejer(session: Session) -> list[int]:
    postkasse = (get_settings().kalender_postkasse or "").strip().lower()
    ejer = session.scalars(select(Staff.id).where(Staff.email == postkasse, Staff.aktiv.is_(True))).first()
    return [ejer] if ejer else administratorer(session)


def planlaeg(session: Session, nu: datetime, book: bool | None = None, kalender=None) -> list[Resultat]:
    """Book masterafstemninger for kunder med ekstern revisor, hvis regnskabsår er afsluttet. Tørløb når book=False."""
    s = get_settings()
    book = s.kalender_book if book is None else book
    i_dag = nu.astimezone(TIDSZONE).date()
    i_morgen = i_dag + timedelta(days=1)
    ud: list[Resultat] = []
    planlagt: list[outlook.Optaget] = []          # det, der bookes i denne kørsel, må ikke overlappe hinanden
    kunder = list(session.scalars(select(Client).join(KundeObligations, KundeObligations.client_id == Client.id).where(
        Client.status.in_(I_DRIFT), KundeObligations.aarsafslutning == "ekstern_revisor").order_by(Client.navn)))
    for kunde in kunder:
        aarene = session.scalars(select(AccountingYearCache).where(
            AccountingYearCache.client_id == kunde.id, AccountingYearCache.til.is_not(None),
            AccountingYearCache.til < i_dag, AccountingYearCache.til >= i_dag - timedelta(days=400)))
        for aar in aarene:
            booking = session.scalars(select(MasterafstemningBooking).where(
                MasterafstemningBooking.client_id == kunde.id,
                MasterafstemningBooking.regnskabsaar_slut == aar.til)).first()
            if booking is not None and booking.status == "booket":
                continue
            v = vindue(session, kunde, aar.til)
            if v.mangler:
                ud.append(Resultat(kunde.navn, aar.til, "mangler", tekst=v.mangler))
                continue
            if v.seneste < i_morgen:
                ud.append(Resultat(kunde.navn, aar.til, "for_sent", tekst=f"vinduet sluttede {v.seneste:%d.%m.%Y}"))
                continue
            fra = max(v.tidligst, i_morgen)
            optagede = kalender.optaget(datetime.combine(fra, time(0)), datetime.combine(v.seneste, time(23, 59)))
            plads = find_ledig(optagede + planlagt, fra, v.seneste, s.kalender_arbejdsdag_fra, s.kalender_arbejdsdag_til)
            if plads is None:
                ud.append(Resultat(kunde.navn, aar.til, "ingen_tid",
                                   tekst=f"ingen {TIMER} ledige timer {fra:%d.%m.%Y}–{v.seneste:%d.%m.%Y}"))
                if book and booking is None:
                    session.add(MasterafstemningBooking(client_id=kunde.id, regnskabsaar_slut=aar.til,
                                                        tidligst=v.tidligst, seneste=v.seneste, status="ingen_tid"))
                    send_notifikation(session, _kalenderens_ejer(session), "advarsel",
                                      f"{kunde.navn}: ingen ledig tid til masterafstemning",
                                      tekst=f"Der er ikke {TIMER} samlede ledige timer i Outlook mellem "
                                            f"{fra:%d.%m.%Y} og {v.seneste:%d.%m.%Y}. HUB'en prøver igen hver dag.",
                                      link="/ai-planlaegning/", client_id=kunde.id)
                continue
            start, slut = plads
            planlagt.append(outlook.Optaget(start, slut))
            if not book:
                ud.append(Resultat(kunde.navn, aar.til, "ville_booke", start, slut))
                continue
            outlook_id = kalender.opret(
                start, slut, f"Masterafstemning – {kunde.navn}",
                f"Masterafstemning til ekstern revisor for regnskabsåret, der sluttede {aar.til:%d.%m.%Y}. "
                f"Skal være sendt til kunden senest {v.seneste:%d.%m.%Y}. Booket automatisk af HUB'en.")
            if booking is None:
                booking = MasterafstemningBooking(client_id=kunde.id, regnskabsaar_slut=aar.til, tidligst=v.tidligst,
                                                  seneste=v.seneste, status="booket")
                session.add(booking)
            booking.status, booking.start, booking.slut, booking.outlook_id = "booket", start, slut, outlook_id
            booking.tidligst, booking.seneste, booking.opdateret = v.tidligst, v.seneste, nu
            session.add(KundeLog(client_id=kunde.id, event="masterafstemning_booket",
                                 detail=f"{start:%d.%m.%Y %H:%M}–{slut:%H:%M} (regnskabsår slut {aar.til:%d.%m.%Y})"))
            session.flush()
            ud.append(Resultat(kunde.navn, aar.til, "booket", start, slut))
    if ud:
        session.add(AuditLog(handling="masterafstemning_planlagt", detaljer={
            "book": book, "resultater": [{"kunde": r.kunde, "udfald": r.udfald, "regnskabsaar_slut": str(r.regnskabsaar_slut),
                                         "start": r.start.isoformat() if r.start else None, "tekst": r.tekst}
                                        for r in ud]}))
    session.flush()
    return ud


def koer(book: bool | None = None) -> list[Resultat]:
    from app.db import ny_session

    if not outlook.slaaet_til():
        return []
    with ny_session() as session, outlook.Kalender() as kalender:
        ud = planlaeg(session, datetime.now(TIDSZONE), book=book, kalender=kalender)
        session.commit()
    return ud


UDFALD_TEKST = {"booket": "BOOKET", "ville_booke": "VILLE BOOKE (tørløb)", "ingen_tid": "INGEN LEDIG TID",
                "for_sent": "FOR SENT", "mangler": "KAN IKKE PLANLÆGGES"}


def main(argv: list[str] | None = None) -> int:
    import app.models  # noqa: F401

    parser = argparse.ArgumentParser(description="Masterafstemning til ekstern revisor – booking i Outlook")
    under = parser.add_subparsers(dest="kommando", required=True)
    under.add_parser("proeve", help="vis hvad der ville blive booket – opretter INTET i Outlook")
    under.add_parser("koer", help="book nu (kun hvis KALENDER_BOOK=true – ellers tørløb)")
    args = parser.parse_args(argv)
    if not outlook.slaaet_til():
        print("Fejl: Outlook-kalenderen er ikke slået til (se DEPLOY.md \"Outlook-kalender\")", file=sys.stderr)
        return 2
    try:
        ud = koer(book=False if args.kommando == "proeve" else None)
    except outlook.KalenderFejl as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2
    if not ud:
        print("Ingen kunder med ekstern revisor og et afsluttet regnskabsår, der mangler en booking.")
    for r in ud:
        tid = f" {r.start:%a %d.%m.%Y %H:%M}–{r.slut:%H:%M}" if r.start else ""
        print(f"{UDFALD_TEKST[r.udfald]:<22} {r.kunde} (regnskabsår slut {r.regnskabsaar_slut:%d.%m.%Y}){tid}"
              + (f" – {r.tekst}" if r.tekst else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
