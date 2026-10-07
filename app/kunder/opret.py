"""Opret en kunde i kundekartoteket (clients).

    python -m app.kunder.opret --navn "Connect El ApS" --kundenummer 1045 --cvr 12345678 --system economic
    python -m app.kunder.opret --vis

Tokenet lægges ind bagefter med app.kunder.gem_token (skjult indtastning – aldrig som argument).
Kunder slettes aldrig; brug status 'opsagt'. Oprettelsen logges i audit_log.
"""

import argparse
import getpass
import sys

from sqlalchemy import or_, select

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.db import ny_session
from app.kunder.models import SYSTEMER, Client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opret en kunde.")
    parser.add_argument("--vis", action="store_true", help="vis alle kunder")
    parser.add_argument("--navn")
    parser.add_argument("--kundenummer", help="jeres kundenummer for kunden")
    parser.add_argument("--cvr", help="8 cifre")
    parser.add_argument("--system", choices=SYSTEMER, help="kundens regnskabssystem")
    args = parser.parse_args(argv)

    with ny_session() as session:
        if args.vis:
            for k in session.scalars(select(Client).order_by(Client.navn)):
                print(f"id {k.id:<6} kundenr. {k.kundenummer:<12} CVR {k.cvr or '-':<9} {k.status:<8} "
                      f"{k.regnskabssystem or '-':<9} {k.navn}")
            return 0
        if not (args.navn and args.kundenummer and args.system):
            parser.error("angiv --navn, --kundenummer og --system")
        cvr = (args.cvr or "").replace(" ", "") or None
        if cvr and not (cvr.isdigit() and len(cvr) == 8):
            parser.error("--cvr skal være 8 cifre")
        findes = session.scalars(select(Client).where(or_(
            Client.kundenummer == args.kundenummer, Client.cvr == cvr if cvr else False))).first()
        if findes:
            print(f"Fejl: Kunden findes allerede (id {findes.id}, {findes.navn})", file=sys.stderr)
            return 2
        kunde = Client(navn=args.navn, kundenummer=args.kundenummer, cvr=cvr, regnskabssystem=args.system,
                       status="aktiv")
        session.add(kunde)
        session.flush()
        session.add(AuditLog(client_id=kunde.id, handling="kunde_oprettet",
                             detaljer={"udfoert_af": getpass.getuser(), "system": args.system}))
        session.commit()
        print(f"Oprettet: {kunde.navn} (id {kunde.id}, kundenr. {kunde.kundenummer}).")
        print(f"Læg tokenet ind: python -m app.kunder.gem_token --kundenummer {kunde.kundenummer} "
              f"--system {args.system}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
