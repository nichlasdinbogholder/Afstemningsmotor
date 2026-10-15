"""Opret bilagsindbakkens alias og destination for en kunde.

    python -m app.bilag.opret --kunde <client_id>                         # alias kunde-<id>@<INBOX_DOMAIN>
    python -m app.bilag.opret --kundenummer <nr> --destination scan@x.dk --type economic
    python -m app.bilag.opret --vis                                       # alle aliasser og destinationer

Kan køres igen uden at skabe dubletter: findes aliasset/destinationen, vises den bare.
Destinationen er kundens scanningsindbakke (e-conomic/Dinero) eller en anden mail – vi skriver aldrig
via API til regnskabssystemet, bilagene leveres udelukkende som mail. Alt logges i audit_log.
"""

import argparse
import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.bilag.models import DESTINATIONSTYPER, InboxAddress, InboxDestination
from app.config import get_settings
from app.db import ny_session
from app.kunder.models import Client

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OpretFejl(Exception):
    pass


def alias_for(client_id: int) -> str:
    return f"kunde-{client_id}@{get_settings().inbox_domain}".lower()


def opret_alias(session: Session, kunde: Client, af: str) -> tuple[InboxAddress, bool]:
    adresse = alias_for(kunde.id)
    findes = session.scalars(select(InboxAddress).where(InboxAddress.address == adresse)).one_or_none()
    if findes is not None:
        if findes.client_id != kunde.id:
            raise OpretFejl(f"{adresse} tilhører en anden kunde (id {findes.client_id})")
        return findes, False
    a = InboxAddress(client_id=kunde.id, address=adresse)
    session.add(a)
    session.flush()
    session.add(AuditLog(client_id=kunde.id, handling="bilag_alias_oprettet",
                         detaljer={"adresse": adresse, "udfoert_af": af}))
    return a, True


def opret_destination(session: Session, kunde: Client, adresse: str, type_: str, af: str
                      ) -> tuple[InboxDestination, bool]:
    adresse = adresse.strip().lower()
    if not EMAIL.match(adresse):
        raise OpretFejl(f"'{adresse}' er ikke en gyldig e-mailadresse")
    if type_ not in DESTINATIONSTYPER:
        raise OpretFejl(f"Typen skal være {', '.join(DESTINATIONSTYPER)}")
    if adresse.endswith("@" + get_settings().inbox_domain.lower()):
        raise OpretFejl("Destinationen må ikke være et af bilagsindbakkens egne aliasser (det ville give en løkke)")
    findes = session.scalars(select(InboxDestination).where(InboxDestination.client_id == kunde.id,
                                                            InboxDestination.address == adresse)).one_or_none()
    if findes is not None:
        return findes, False
    d = InboxDestination(client_id=kunde.id, kind=type_, address=adresse)
    session.add(d)
    session.flush()
    session.add(AuditLog(client_id=kunde.id, handling="bilag_destination_oprettet",
                         detaljer={"adresse": adresse, "type": type_, "udfoert_af": af}))
    return d, True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bilagsindbakken: alias og destination for en kunde.")
    hvem = p.add_mutually_exclusive_group(required=True)
    hvem.add_argument("--kunde", type=int, help="kundens id (client_id)")
    hvem.add_argument("--kundenummer")
    hvem.add_argument("--vis", action="store_true")
    p.add_argument("--destination", help="kundens scanningsindbakke, fx ...@e-conomic-adresse")
    p.add_argument("--type", choices=DESTINATIONSTYPER, default="email")
    args = p.parse_args(argv)
    af = getpass.getuser()

    with ny_session() as session:
        if args.vis:
            for a, navn in session.execute(select(InboxAddress, Client.navn).join(Client).order_by(Client.navn)):
                dest = session.scalars(select(InboxDestination).where(InboxDestination.client_id == a.client_id)).all()
                print(f"{navn:<30} {a.address:<40} {'aktiv' if a.active else 'slået fra'}")
                for d in dest:
                    print(f"{'':<30}   → {d.kind:<8} {d.address}{'' if d.active else '  (slået fra)'}")
            return 0
        kunde = (session.get(Client, args.kunde) if args.kunde is not None else
                 session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none())
        if kunde is None:
            print("Fejl: kunden findes ikke", file=sys.stderr)
            return 2
        try:
            a, ny = opret_alias(session, kunde, af)
            print(f"Alias {'oprettet' if ny else 'findes allerede'}: {a.address}")
            if args.destination:
                d, ny = opret_destination(session, kunde, args.destination, args.type, af)
                print(f"Destination {'oprettet' if ny else 'findes allerede'}: {d.kind} {d.address}")
        except OpretFejl as fejl:
            session.rollback()
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 2
        session.commit()
        print(f"\nKunden skal nu lave en videresendelsesregel fra sin fakturamail til {a.address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
