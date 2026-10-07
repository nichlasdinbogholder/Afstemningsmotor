"""Medarbejdere, der må logge ind (med deres Microsoft-konto).

    python -m app.personale.bruger vis
    python -m app.personale.bruger opret --email navn@dinbogholder.dk --navn "Navn" --rolle admin
    python -m app.personale.bruger rolle --email navn@dinbogholder.dk --rolle medarbejder
    python -m app.personale.bruger deaktiver --email navn@dinbogholder.dk
    python -m app.personale.bruger aktiver --email navn@dinbogholder.dk

Roller: admin (administrator) og medarbejder (almindelig bruger). Medarbejdere slettes
aldrig – de deaktiveres og mister så adgangen med det samme. Alt logges i audit_log.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.db import ny_session
from app.personale.models import STAFF_ROLLER, Staff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Medarbejdere, der må logge ind.")
    under = parser.add_subparsers(dest="kommando", required=True)
    under.add_parser("vis")
    o = under.add_parser("opret")
    o.add_argument("--email", required=True)
    o.add_argument("--navn", required=True)
    o.add_argument("--rolle", choices=STAFF_ROLLER, default="medarbejder")
    r = under.add_parser("rolle")
    r.add_argument("--email", required=True)
    r.add_argument("--rolle", choices=STAFF_ROLLER, required=True)
    for navn in ("deaktiver", "aktiver"):
        under.add_parser(navn).add_argument("--email", required=True)
    args = parser.parse_args(argv)
    udfoerer = getpass.getuser()

    with ny_session() as session:
        if args.kommando == "vis":
            for m in session.scalars(select(Staff).order_by(Staff.navn)):
                sidst = f"{m.sidst_logget_ind:%d.%m.%Y %H:%M}" if m.sidst_logget_ind else "aldrig"
                print(f"{m.email:40} {m.navn:30} {m.rolle:12} {'aktiv' if m.aktiv else 'DEAKTIVERET':12} "
                      f"sidst logget ind: {sidst}")
            return 0
        email = args.email.strip().lower()
        m = session.scalars(select(Staff).where(Staff.email == email)).one_or_none()
        if args.kommando == "opret":
            if m is not None:
                print(f"Fejl: {email} findes allerede", file=sys.stderr)
                return 2
            m = Staff(email=email, navn=args.navn, rolle=args.rolle, aktiv=True)
            session.add(m)
            session.flush()
        elif m is None:
            print(f"Fejl: {email} findes ikke", file=sys.stderr)
            return 2
        elif args.kommando == "rolle":
            m.rolle = args.rolle
        else:
            m.aktiv = args.kommando == "aktiver"
        session.add(AuditLog(handling=f"medarbejder_{args.kommando}",
                             detaljer={"medarbejder_id": m.id, "email": email, "rolle": m.rolle,
                                       "aktiv": m.aktiv, "udfoert_af": udfoerer}))
        session.commit()
        print(f"OK: {m.navn} <{email}> – {m.rolle}, {'aktiv' if m.aktiv else 'deaktiveret'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
