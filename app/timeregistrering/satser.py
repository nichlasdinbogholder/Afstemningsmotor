"""Periodeopslag: de satser, der gælder på en bestemt dato (fase 1, trin 2).

ALLE rapporter og beregninger i timeregistreringen slår satser op her (eller i de samme SQL-funktioner i databasen:
time_salgspris, time_kostpris, time_normtid, time_afregning, time_abonnementspris). Datoen er altid
registreringsdatoen – aldrig dags dato – så en rapport for marts giver samme tal, uanset hvornår den køres.

Findes der ingen sats på datoen, rejses `ManglerSats` med en tydelig besked. Der regnes ALDRIG med 0 i stedet.

Kommando (kun læsning, undtagen når en ny pris/vilkår lægges ind):
    python -m app.timeregistrering.satser vis --dato 2026-10-19 [--medarbejder <e-mail>] [--kundenummer <nr>]
    python -m app.timeregistrering.satser salgspris --fra 2027-01-01 --pris 725,00 --af <admin-e-mail>
    python -m app.timeregistrering.satser vilkaar --kundenummer <nr> --fra 2026-10-01 --model abonnement --pris 1500 \
        --af <admin-e-mail>
    python -m app.timeregistrering.satser vilkaar --kundenummer <nr> --fra 2026-10-01 --model intern --af <admin-e-mail>
        (intern = vores eget firma; kun på kunder med "Kun admin hos Svend")
Ændringer kræver --af: en aktiv administrator. Den står som aktør i time_audit og i audit_log.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.timeregistrering.models import AFREGNINGER, TimeClientTerms, TimeSalesRate


class ManglerSats(Exception):
    """Der findes ingen gældende sats på datoen. Beskeden er dansk og siger, hvad der mangler."""


@dataclass(frozen=True)
class Satser:
    dato: date
    salgspris: Decimal                   # pr. time, ekskl. moms
    kostpris: Decimal | None = None      # pr. time (kun når der er spurgt om en medarbejder)
    normtid: Decimal | None = None       # timer pr. uge (kan være tom for en medarbejder)
    afregning: str | None = None         # 'abonnement' | 'timeafregning' | 'intern' (kun når der er spurgt om en kunde)
    abonnementspris: Decimal | None = None


def satser_paa(session: Session, dato: date, employee_id: int | None = None,
               client_id: int | None = None) -> Satser:
    """Salgspris – og kostpris/normtid for medarbejderen og vilkår for kunden, hvis de er angivet – på datoen."""
    if not isinstance(dato, date):
        raise ManglerSats("Der skal altid angives en dato – satser slås aldrig op på dags dato")
    r = session.execute(text(
        "SELECT time_salgspris(:d) AS salgspris, time_kostpris(:e, :d) AS kostpris, time_normtid(:e, :d) AS normtid, "
        "time_afregning(:k, :d) AS afregning, time_abonnementspris(:k, :d) AS abonnementspris"),
        {"d": dato, "e": employee_id, "k": client_id}).one()
    if r.salgspris is None:
        raise ManglerSats(f"Der er ingen salgspris, der gælder {dato:%d.%m.%Y}")
    if employee_id is not None and r.kostpris is None:
        raise ManglerSats(f"Medarbejderen har ingen kostpris, der gælder {dato:%d.%m.%Y} – læg en sats ind under "
                          "Medarbejdere")
    if client_id is not None and r.afregning is None:
        raise ManglerSats(f"Kunden har ingen afregningsform, der gælder {dato:%d.%m.%Y} – læg vilkår ind for kunden")
    return Satser(dato=dato, salgspris=r.salgspris,
                  kostpris=r.kostpris if employee_id is not None else None,
                  normtid=r.normtid if employee_id is not None else None,
                  afregning=r.afregning if client_id is not None else None,
                  abonnementspris=r.abonnementspris if client_id is not None else None)


# --- Nye priser og vilkår (samme princip som kostpris: ny periode afslutter den forrige) ---------


def placer_periode(raekker: list, fra: date):
    """Hvor en ny periode fra `fra` skal ligge mellem de eksisterende rækker (med valid_from/valid_to).
    Returnerer (samme, foer, efter): rækken med samme startdato (= rettelse), rækken der gælder på `fra` og skal
    afsluttes, og startdatoen for den næste række (= den nye periodes slutdato) – None, hvis den ikke findes."""
    samme = next((r for r in raekker if r.valid_from == fra), None)
    foer = next((r for r in raekker if r.valid_from < fra and (r.valid_to is None or r.valid_to > fra)), None)
    efter = min((r.valid_from for r in raekker if r.valid_from > fra), default=None)
    return samme, foer, efter


def ny_salgspris(session: Session, fra: date, pris: Decimal) -> TimeSalesRate:
    if pris < 0:
        raise ManglerSats("Salgsprisen kan ikke være negativ")
    alle = list(session.scalars(select(TimeSalesRate)))
    samme, foer, efter = placer_periode(alle, fra)
    if samme is not None:
        samme.rate_per_hour = pris
        session.flush()
        return samme
    if foer is not None:
        foer.valid_to = fra
        session.flush()
    r = TimeSalesRate(valid_from=fra, valid_to=efter, rate_per_hour=pris)
    session.add(r)
    session.flush()
    return r


def nye_vilkaar(session: Session, client_id: int, fra: date, afregning: str, abonnementspris: Decimal | None,
                af_staff_id: int | None) -> TimeClientTerms:
    """Kundens afregningsform fra en dato. Allerede registrerede linjer ændres ikke – de har deres egen kopi."""
    if afregning not in AFREGNINGER:
        raise ManglerSats("Afregningsformen skal være abonnement, timeafregning eller intern")
    if afregning == "abonnement" and abonnementspris is None:
        raise ManglerSats("Et abonnement kræver en abonnementspris")
    if afregning == "intern":
        from app.kunder.models import Client  # her, ellers importerer kunder og timeregistrering hinanden i ring

        kunde = session.get(Client, client_id)
        if kunde is None or not kunde.kun_admin:
            raise ManglerSats("Afregningsformen Intern kan kun bruges på interne kunder (markeret \"Kun admin hos "
                              "Svend\"), så en rigtig kunde aldrig ved en fejl bliver gratis")
    if afregning in ("timeafregning", "intern"):
        abonnementspris = None
    alle = list(session.scalars(select(TimeClientTerms).where(TimeClientTerms.client_id == client_id)))
    samme, foer, efter = placer_periode(alle, fra)
    if samme is not None:
        samme.billing_model, samme.subscription_price, samme.created_by = afregning, abonnementspris, af_staff_id
        session.flush()
        return samme
    if foer is not None:
        foer.valid_to = fra
        session.flush()
    v = TimeClientTerms(client_id=client_id, valid_from=fra, valid_to=efter, billing_model=afregning,
                        subscription_price=abonnementspris, created_by=af_staff_id)
    session.add(v)
    session.flush()
    return v


# --- Kommandoen -------------------------------------------------------------------------------


def _beloeb(tekst: str) -> Decimal:
    """Dansk skrivemåde (1.250,50) – '725.5' afvises, så et beløb aldrig gættes."""
    t = tekst.strip()
    if "." in t and "," not in t and len(t.split(".")[-1]) != 3:
        raise argparse.ArgumentTypeError("skriv beløbet med komma som decimaltegn, fx 725,00")
    try:
        return Decimal(t.replace(".", "").replace(",", "."))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"'{tekst}' er ikke et beløb") from None


def _kr(v: Decimal | None) -> str:
    return "-" if v is None else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kr."


def main(argv: list[str] | None = None) -> int:
    import app.models  # noqa: F401
    from app.audit.models import AuditLog
    from app.db import ny_session
    from app.kunder.models import Client
    from app.personale.models import Staff
    from app.timeregistrering.stamdata import ansat, saet_aktoer

    parser = argparse.ArgumentParser(description="Satser i timeregistreringen.")
    under = parser.add_subparsers(dest="kommando", required=True)
    vis = under.add_parser("vis", help="vis satserne, der gælder på en dato")
    vis.add_argument("--dato", type=date.fromisoformat, required=True)
    vis.add_argument("--medarbejder", help="e-mail")
    vis.add_argument("--kundenummer")
    sp = under.add_parser("salgspris", help="ny salgspris (ekskl. moms) fra en dato")
    sp.add_argument("--fra", type=date.fromisoformat, required=True)
    sp.add_argument("--pris", type=_beloeb, required=True)
    sp.add_argument("--af", required=True, help="e-mail på administratoren, der laver ændringen")
    vk = under.add_parser("vilkaar", help="kundens afregningsform fra en dato")
    vk.add_argument("--kundenummer", required=True)
    vk.add_argument("--fra", type=date.fromisoformat, required=True)
    vk.add_argument("--model", choices=AFREGNINGER, required=True)
    vk.add_argument("--pris", type=_beloeb, help="abonnementspris pr. måned ekskl. moms (kun abonnement)")
    vk.add_argument("--af", required=True, help="e-mail på administratoren, der laver ændringen")
    args = parser.parse_args(argv)

    with ny_session() as session:
        kunde = None
        if getattr(args, "kundenummer", None):
            kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
            if kunde is None:
                print("Fejl: Kunden findes ikke", file=sys.stderr)
                return 2
        if args.kommando == "vis":
            e = None
            if args.medarbejder:
                m = session.scalars(select(Staff).where(Staff.email == args.medarbejder.strip().lower())).one_or_none()
                e = ansat(session, m.id) if m else None
                if e is None:
                    print("Fejl: Medarbejderen er ikke sat op til timeregistrering", file=sys.stderr)
                    return 2
            try:
                s = satser_paa(session, args.dato, e.id if e else None, kunde.id if kunde else None)
            except ManglerSats as fejl:
                print(f"Fejl: {fejl}", file=sys.stderr)
                return 1
            print(f"Satser {args.dato:%d.%m.%Y}:")
            print(f"  Salgspris pr. time: {_kr(s.salgspris)} (ekskl. moms)")
            if e is not None:
                print(f"  Kostpris pr. time:  {_kr(s.kostpris)}")
                print(f"  Normtid:            {'-' if s.normtid is None else str(s.normtid).replace('.', ',')} t/uge")
            if kunde is not None:
                print(f"  {kunde.navn}: {s.afregning}" + (f", {_kr(s.abonnementspris)} pr. måned"
                                                         if s.afregning == "abonnement" else ""))
                print("  Afregningsperioder:")
                for v in session.scalars(select(TimeClientTerms).where(TimeClientTerms.client_id == kunde.id)
                                         .order_by(TimeClientTerms.valid_from)):
                    til = f"{v.valid_to:%d.%m.%Y}" if v.valid_to else "og frem"
                    print(f"    fra {v.valid_from:%d.%m.%Y} {til}: {v.billing_model}")
            return 0
        bruger = session.scalars(select(Staff).where(Staff.email == args.af.strip().lower())).one_or_none()
        if bruger is None or not bruger.aktiv or bruger.rolle != "admin":
            print("Fejl: --af skal være en aktiv administrator", file=sys.stderr)
            return 2
        saet_aktoer(session, bruger.id)
        try:
            if args.kommando == "salgspris":
                ny_salgspris(session, args.fra, args.pris)
                tekst = f"Salgsprisen er {_kr(args.pris)} pr. time fra {args.fra:%d.%m.%Y}."
                session.add(AuditLog(staff_id=bruger.id, handling="salgspris_aendret",
                                     detaljer={"fra": args.fra.isoformat(), "pris": str(args.pris),
                                               "udfoert_af": bruger.email}))
            else:
                nye_vilkaar(session, kunde.id, args.fra, args.model, args.pris, bruger.id)
                tekst = (f"{kunde.navn}: {args.model} fra {args.fra:%d.%m.%Y}"
                         + (f", {_kr(args.pris)} pr. måned." if args.model == "abonnement" else "."))
                session.add(AuditLog(client_id=kunde.id, staff_id=bruger.id,
                                     handling="kundevilkaar_aendret",
                                     detaljer={"fra": args.fra.isoformat(), "model": args.model,
                                               "pris": None if args.pris is None else str(args.pris),
                                               "udfoert_af": bruger.email}))
        except ManglerSats as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 1
        session.commit()
        print(tekst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
