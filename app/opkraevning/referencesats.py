"""Nationalbankens udlånsrente (referencesatsen) for hvert halvår (1. januar og 1. juli).

Hentes AUTOMATISK hver morgen kl. 6.10 (job `hent_referencesats`, app/opkraevning/nationalbanken.py).
Kan prøves og – hvis kilden svigter – sættes i hånden:

    python -m app.opkraevning.referencesats vis
    python -m app.opkraevning.referencesats hent [--dato 2026-07-01] [--gem]   # uden --gem gemmes intet
    python -m app.opkraevning.referencesats saet --fra 2026-07-01 --sats 1.60 --kilde "Nationalbanken, udlånsrente pr. 1.7.2026"

Satsen er i procent. Morarenten = satsen + 8 procentpoint. Mangler satsen for et halvår, fejler
renteberegningen højlydt – den gætter aldrig og bruger aldrig sidste halvårs sats.
En sats, der allerede er brugt, kan ikke ændres her (ret den i en ny migrering efter aftale).
"""

import argparse
import getpass
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.db import ny_session
from app.opkraevning.lov import RENTETILLAEG_PROCENTPOINT
from app.opkraevning.models import ReferenceRate
from app.tid import TIDSZONE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nationalbankens udlånsrente til morarenten.")
    under = parser.add_subparsers(dest="kommando", required=True)
    under.add_parser("vis")
    h = under.add_parser("hent", help="hent fra Nationalbanken (via Danmarks Statistik) – vis, gem kun med --gem")
    h.add_argument("--dato", type=date.fromisoformat, default=None, help="dag i halvåret (standard: i dag)")
    h.add_argument("--gem", action="store_true")
    s = under.add_parser("saet")
    s.add_argument("--fra", required=True, type=date.fromisoformat, help="1. januar eller 1. juli, fx 2026-07-01")
    s.add_argument("--sats", required=True, help="i procent, fx 1.60")
    s.add_argument("--kilde", required=True, help="hvor satsen er fundet")
    args = parser.parse_args(argv)

    with ny_session() as session:
        if args.kommando == "vis":
            satser = session.scalars(select(ReferenceRate).order_by(ReferenceRate.valid_from)).all()
            if not satser:
                print("Ingen satser sat endnu.")
            for r in satser:
                print(f"fra {r.valid_from:%d.%m.%Y}: {r.rate:.4f} %  → morarente {r.rate + RENTETILLAEG_PROCENTPOINT:.4f} %"
                      f"   ({r.source})")
            return 0
        if args.kommando == "hent":
            return _hent(session, args.dato or datetime.now(TIDSZONE).date(), args.gem)
        if (args.fra.month, args.fra.day) not in ((1, 1), (7, 1)):
            print("Fejl: satsen gælder altid fra 1. januar eller 1. juli", file=sys.stderr)
            return 2
        try:
            sats = Decimal(args.sats.replace(",", "."))
        except InvalidOperation:
            print(f"Fejl: '{args.sats}' er ikke et tal", file=sys.stderr)
            return 2
        if session.scalar(select(ReferenceRate).where(ReferenceRate.valid_from == args.fra)):
            print(f"Fejl: satsen fra {args.fra:%d.%m.%Y} er allerede sat og kan ikke ændres her", file=sys.stderr)
            return 2
        session.add(ReferenceRate(valid_from=args.fra, rate=sats, source=args.kilde))
        session.add(AuditLog(handling="referencesats_sat",
                             detaljer={"fra": args.fra.isoformat(), "sats": str(sats), "kilde": args.kilde,
                                       "udfoert_af": getpass.getuser()}))
        session.commit()
        print(f"Sat: fra {args.fra:%d.%m.%Y} {sats} % → morarente {sats + RENTETILLAEG_PROCENTPOINT} %")
    return 0


def _hent(session, dag: date, gem: bool) -> int:
    from app.opkraevning.lov import halvaar_start
    from app.opkraevning.nationalbanken import HentFejl, hent_sats, opdater_referencesats

    fra = halvaar_start(dag)
    try:
        s = hent_sats(fra)
    except HentFejl as fejl:
        print(f"Kunne ikke hente satsen pr. {fra:%d.%m.%Y}: {fejl}", file=sys.stderr)
        return 2
    print(f"Satsen pr. {fra:%d.%m.%Y}: {s.sats} %  → morarente {s.sats + RENTETILLAEG_PROCENTPOINT} %")
    print(f"Kilde: {s.kilde}")
    findes = session.scalar(select(ReferenceRate).where(ReferenceRate.valid_from == fra))
    if findes is not None:
        tekst = "samme" if findes.rate == s.sats else f"AFVIGER: gemt {findes.rate} %"
        print(f"Allerede gemt for halvåret ({tekst}) – intet ændret.")
        return 0 if findes.rate == s.sats else 1
    if not gem:
        print("Intet gemt (prøvekørsel). Kør igen med --gem for at gemme.")
        return 0
    opdater_referencesats(session, dag)
    session.commit()
    print("Gemt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
