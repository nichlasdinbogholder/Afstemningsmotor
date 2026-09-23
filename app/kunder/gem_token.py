"""Læg et token ind for en kunde.

    python -m app.kunder.gem_token --kundenummer K-1001 --system economic
    python -m app.kunder.gem_token --kunde 12 --system dinero --organisation-id 123456

Tokenet skrives IKKE på kommandolinjen (så havner det i terminalens historik).
Kommandoen spørger efter det bagefter, og det, du indsætter, vises ikke på skærmen.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import ny_session
from app.kunder.adgange import UgyldigAdgang, gem_token
from app.kunder.models import SYSTEMER, Client
from app.sikkerhed.kryptering import KrypteringsFejl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gem et krypteret token for en kunde.")
    hvem = parser.add_mutually_exclusive_group(required=True)
    hvem.add_argument("--kunde", type=int, help="kundens id i clients")
    hvem.add_argument("--kundenummer", help="kundens kundenummer")
    parser.add_argument("--system", required=True, choices=SYSTEMER)
    parser.add_argument("--organisation-id", help="Dinero: organisationens id")
    parser.add_argument("--aftale-id", help="e-conomic: aftalenummer")
    args = parser.parse_args(argv)

    with ny_session() as session:
        if args.kunde is not None:
            kunde = session.get(Client, args.kunde)
        else:
            kunde = session.scalars(
                select(Client).where(Client.kundenummer == args.kundenummer)
            ).one_or_none()
        if kunde is None:
            print("Fejl: Kunden findes ikke", file=sys.stderr)
            return 1

        token = getpass.getpass(f"Indsæt token til {args.system} for {kunde.navn} (vises ikke): ")
        try:
            credential = gem_token(
                session, kunde.id, args.system, token,
                organisation_id=args.organisation_id, aftale_id=args.aftale_id,
            )
            session.commit()
        except (UgyldigAdgang, KrypteringsFejl) as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 1
        finally:
            del token

    print(f"Token til {args.system} gemt krypteret for {kunde.navn} (adgang id {credential.id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
