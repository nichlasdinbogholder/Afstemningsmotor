"""Læg et token ind for en kunde.

    python -m app.kunder.gem_token --kundenummer K-1001 --system economic --af <administratorens e-mail>
    python -m app.kunder.gem_token --kunde 12 --system dinero --organisation-id 123456 --af <e-mail>

KUN en administrator kan sætte et token (--af skal være en aktiv administrator – databasen kontrollerer det også).
Findes der et token i forvejen, tilbagekaldes det, og det nye gemmes som en ny række.

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
from app.personale.models import Staff
from app.sikkerhed.kryptering import KrypteringsFejl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gem et krypteret token for en kunde.")
    hvem = parser.add_mutually_exclusive_group(required=True)
    hvem.add_argument("--kunde", type=int, help="kundens id i clients")
    hvem.add_argument("--kundenummer", help="kundens kundenummer")
    parser.add_argument("--system", required=True, choices=SYSTEMER)
    parser.add_argument("--organisation-id", help="Dinero: organisationens id")
    parser.add_argument("--aftale-id", help="e-conomic: aftalenummer")
    parser.add_argument("--af", required=True, help="e-mail på administratoren, der lægger tokenet ind")
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
        af = session.scalars(select(Staff).where(Staff.email == args.af.strip().lower())).one_or_none()
        if af is None or not af.aktiv or af.rolle != "admin":
            print("Fejl: --af skal være en aktiv administrator", file=sys.stderr)
            return 1

        try:
            token = getpass.getpass(f"Indsæt token til {args.system} for {kunde.navn} (vises ikke): ")
        except (KeyboardInterrupt, EOFError):
            print("\nAfbrudt – intet er gemt.", file=sys.stderr)
            return 1
        try:
            credential = gem_token(
                session, kunde.id, args.system, token, staff_id=af.id,
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
