"""Nationalbankens udlånsrente (referencesatsen) – sættes af en medarbejder 1. januar og 1. juli.

    python -m app.opkraevning.referencesats vis
    python -m app.opkraevning.referencesats saet --fra 2026-07-01 --sats 1.60 --kilde "Nationalbanken, udlånsrente pr. 1.7.2026"

Satsen er i procent. Morarenten = satsen + 8 procentpoint. Mangler satsen for et halvår, fejler
renteberegningen højlydt – den gætter aldrig og bruger aldrig sidste halvårs sats.
En sats, der allerede er brugt, kan ikke ændres her (ret den i en ny migrering efter aftale).
"""

import argparse
import getpass
import sys
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.db import ny_session
from app.opkraevning.lov import RENTETILLAEG_PROCENTPOINT
from app.opkraevning.models import ReferenceRate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nationalbankens udlånsrente til morarenten.")
    under = parser.add_subparsers(dest="kommando", required=True)
    under.add_parser("vis")
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


if __name__ == "__main__":
    sys.exit(main())
