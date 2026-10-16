"""Opret bilagsindbakkens alias og destinationer for en kunde.

    python -m app.bilag.opret --kunde <client_id>                          # alias kunde-<id>@<INBOX_DOMAIN>
    python -m app.bilag.opret --kundenummer <nr> --corpay faktura@corpay-adresse.dk --kassekladde 3
    python -m app.bilag.opret --vis                                        # alle aliasser og destinationer

- Corpay: den mail, kundens godkendte fakturaer (og rykkere) sendes til.
- Kassekladde: nummeret på kassekladden i kundens e-conomic, hvor kvitteringer lægges som bilag (aldrig bogført).
Kan køres igen uden dubletter. Højst én aktiv destination af hver slags pr. kunde. Alt logges i audit_log.
"""

import argparse
import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.bilag.models import InboxAddress, InboxDestination
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


def _aktiv(session: Session, kunde: Client, art: str) -> InboxDestination | None:
    return session.scalars(select(InboxDestination).where(
        InboxDestination.client_id == kunde.id, InboxDestination.kind == art, InboxDestination.active)).one_or_none()


def opret_corpay(session: Session, kunde: Client, adresse: str, af: str) -> tuple[InboxDestination, bool]:
    adresse = adresse.strip().lower()
    if not EMAIL.match(adresse):
        raise OpretFejl(f"'{adresse}' er ikke en gyldig e-mailadresse")
    if adresse.endswith("@" + get_settings().inbox_domain.lower()):
        raise OpretFejl("Corpay-adressen må ikke være et af bilagsindbakkens egne aliasser (det ville give en løkke)")
    findes = _aktiv(session, kunde, "corpay")
    if findes is not None:
        if findes.address != adresse:
            raise OpretFejl(f"Kunden har allerede Corpay-adressen {findes.address} – slå den fra først")
        return findes, False
    return _ny(session, kunde, InboxDestination(client_id=kunde.id, kind="corpay", address=adresse), af), True


def opret_kassekladde(session: Session, kunde: Client, journal_id: str, af: str) -> tuple[InboxDestination, bool]:
    journal_id = journal_id.strip()
    if not re.fullmatch(r"\d{1,9}", journal_id):
        raise OpretFejl("Kassekladdens nummer skal være et tal (som i e-conomic)")
    findes = _aktiv(session, kunde, "economic_journal")
    if findes is not None:
        if findes.journal_id != journal_id:
            raise OpretFejl(f"Kunden bruger allerede kassekladde {findes.journal_id} – slå den fra først")
        return findes, False
    d = InboxDestination(client_id=kunde.id, kind="economic_journal", journal_id=journal_id)
    return _ny(session, kunde, d, af), True


def _ny(session: Session, kunde: Client, d: InboxDestination, af: str) -> InboxDestination:
    session.add(d)
    session.flush()
    session.add(AuditLog(client_id=kunde.id, handling="bilag_destination_oprettet",
                         detaljer={"type": d.kind, "adresse": d.address, "kassekladde": d.journal_id,
                                   "udfoert_af": af}))
    return d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bilagsindbakken: alias og destinationer for en kunde.")
    hvem = p.add_mutually_exclusive_group(required=True)
    hvem.add_argument("--kunde", type=int, help="kundens id (client_id)")
    hvem.add_argument("--kundenummer")
    hvem.add_argument("--vis", action="store_true")
    p.add_argument("--corpay", help="mailadressen hos Corpay, som godkendte fakturaer sendes til")
    p.add_argument("--kassekladde", help="nummeret på kassekladden i kundens e-conomic (kvitteringer)")
    args = p.parse_args(argv)
    af = getpass.getuser()

    with ny_session() as session:
        if args.vis:
            for a, navn in session.execute(select(InboxAddress, Client.navn).join(Client).order_by(Client.navn)):
                print(f"{navn:<30} {a.address:<40} {'aktiv' if a.active else 'slået fra'}")
                for d in session.scalars(select(InboxDestination).where(InboxDestination.client_id == a.client_id)):
                    hvor = d.address if d.kind == "corpay" else f"kassekladde {d.journal_id}"
                    print(f"{'':<30}   → {d.kind:<16} {hvor}{'' if d.active else '  (slået fra)'}")
            return 0
        kunde = (session.get(Client, args.kunde) if args.kunde is not None else
                 session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none())
        if kunde is None:
            print("Fejl: kunden findes ikke", file=sys.stderr)
            return 2
        try:
            a, ny = opret_alias(session, kunde, af)
            print(f"Alias {'oprettet' if ny else 'findes allerede'}: {a.address}")
            if args.corpay:
                d, ny = opret_corpay(session, kunde, args.corpay, af)
                print(f"Corpay {'oprettet' if ny else 'findes allerede'}: {d.address}")
            if args.kassekladde:
                d, ny = opret_kassekladde(session, kunde, args.kassekladde, af)
                print(f"Kassekladde {'oprettet' if ny else 'findes allerede'}: {d.journal_id}")
        except OpretFejl as fejl:
            session.rollback()
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 2
        session.commit()
        print(f"\nKunden skal nu lave en videresendelsesregel fra sin fakturamail til {a.address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
