"""Hent en kundes kontoplan via AccountingProvider og gem den i `accounts`.

    python -m app.synk.kontoplan --kundenummer 40850635
    python -m app.synk.kontoplan --kunde 12
    python -m app.synk.kontoplan --alle

Virker for ethvert regnskabssystem med en adapter – koden her ved ikke,
hvilket system kunden bruger. Kontoplanen i vores database bliver en kopi af
systemets: nye konti tilføjes, ændrede opdateres, forsvundne fjernes.
Læser kun fra regnskabssystemet – intet bogføres eller ændres dér.

--alle tager alle AKTIVE kunder (aldrig opsagt eller pause) med aktiv adgang
til deres regnskabssystem. Fejler én kunde, fortsætter den med de næste.
Bruges af den daglige kørsel (app/planlaegning/natlig_kontoplan.py).
"""

import argparse
import logging
import sys
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt)
from app.adaptere.adgang import AdgangMangler
from app.adaptere.regnskab.base import (
    AccountingProvider,
    AdapterFejl,
    Konto,
    hent_adapter,
    understoettede_systemer,
)
from app.audit.models import AuditLog
from app.db import ny_session
from app.kunder.models import Client, Credential
from app.regnskab.models import Account
from app.sikkerhed.hemmeligheder import rediger
from app.sikkerhed.kryptering import KrypteringsFejl
from app.synk.models import SyncState
from app.synk.tilstand import SynkFejl, synk_transaktion

log = logging.getLogger(__name__)

RESSOURCE = "accounts"


class KontoplanFejl(Exception):
    pass


FORVENTEDE_FEJL = (KontoplanFejl, AdapterFejl, AdgangMangler, KrypteringsFejl, SynkFejl)


def gem_kontoplan(session: Session, tenant_id: int, system: str, konti: list[Konto]) -> dict:
    """Gem kontoplanen som én samlet ændring: tilføj/opdatér, og fjern forsvundne konti."""
    raekker = [{"tenant_id": tenant_id, "system": system, **asdict(k)} for k in konti]
    if raekker:
        stmt = insert(Account).values(raekker)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_accounts_tenant_konto",
            set_={
                kolonne: stmt.excluded[kolonne]
                for kolonne in raekker[0]
                if kolonne not in ("tenant_id", "system", "kontonummer")
            } | {"hentet": func.now()},
        )
        session.execute(stmt)
    fjernet = session.execute(
        delete(Account).where(
            Account.tenant_id == tenant_id,
            Account.system == system,
            Account.kontonummer.not_in([r["kontonummer"] for r in raekker]),
        )
    ).rowcount
    resultat = {"antal_konti": len(raekker), "fjernet": fjernet}
    session.add(AuditLog(client_id=tenant_id, handling="kontoplan_hentet", detaljer=resultat))
    return resultat


def hent_og_gem_kontoplan(
    session: Session, tenant_id: int, provider: AccountingProvider | None = None
) -> dict:
    """Hent kontoplanen for én kunde og gem den – registreres i sync_state som 'accounts'."""
    with synk_transaktion(session, tenant_id, RESSOURCE, interval=timedelta(days=1)) as synk:
        if provider is None:
            with hent_adapter(session, tenant_id) as p:
                system, konti = p.system, p.fetch_accounts()
        else:
            system, konti = provider.system, provider.fetch_accounts()
        if not konti:
            # En tom kontoplan er næsten altid en fejl – slet ikke den gemte kopi.
            raise KontoplanFejl("Regnskabssystemet returnerede ingen konti – intet er ændret")
        resultat = gem_kontoplan(session, tenant_id, system, konti)
        synk.gennemfoert(None, antal_hentet=len(konti))
    return resultat


def kunder_til_kontoplan(session: Session) -> list[Client]:
    """Aktive kunder med aktiv adgang til deres eget, understøttede regnskabssystem.
    Opsagte, kunder på pause og kunder med kontoplan slået fra køres aldrig."""
    slaaet_fra = select(SyncState.client_id).where(
        SyncState.ressource == RESSOURCE, SyncState.status == "deaktiveret"
    )
    return list(session.scalars(
        select(Client)
        .join(Credential, (Credential.client_id == Client.id)
              & (Credential.system == Client.regnskabssystem))
        .where(
            Credential.status == "aktiv",
            Client.status == "aktiv",
            Client.regnskabssystem.in_(understoettede_systemer()),
            Client.id.not_in(slaaet_fra),
        )
        .order_by(Client.kundenummer)
    ))


def hent_for_alle(session: Session) -> dict:
    """Hent kontoplanen for alle kunder. Én kundes fejl stopper ikke de andre."""
    kunder = [(k.id, k.kundenummer, k.navn) for k in kunder_til_kontoplan(session)]
    ok, fejlede = [], []
    for kunde_id, kundenummer, navn in kunder:
        try:
            resultat = hent_og_gem_kontoplan(session, kunde_id)
        except (*FORVENTEDE_FEJL, SQLAlchemyError) as fejl:
            session.rollback()
            besked = rediger(str(fejl)).splitlines()[0][:300]
            log.error("Kunde %s (%s): kontoplan IKKE hentet – %s", kundenummer, navn, besked)
            fejlede.append(kundenummer)
            continue
        log.info("Kunde %s (%s): %s konti, %s fjernet",
                 kundenummer, navn, resultat["antal_konti"], resultat["fjernet"])
        ok.append(kundenummer)
    return {"antal_kunder": len(kunder), "ok": ok, "fejlede": fejlede}


def _find_kunde(session: Session, kunde_id: int | None, kundenummer: str | None) -> Client:
    if kunde_id is not None:
        kunde = session.get(Client, kunde_id)
    else:
        kunde = session.scalars(select(Client).where(Client.kundenummer == kundenummer)).one_or_none()
    if kunde is None:
        raise KontoplanFejl("Kunden findes ikke")
    return kunde


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hent kontoplan fra kundens regnskabssystem.")
    gruppe = parser.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--kunde", type=int, help="kundens id i clients")
    gruppe.add_argument("--kundenummer", help="kundens kundenummer")
    gruppe.add_argument("--alle", action="store_true", help="alle aktive kunder med aktiv adgang")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    if args.alle:
        with ny_session() as session:
            resultat = hent_for_alle(session)
        log.info(
            "Færdig: %s kunder, %s hentet, %s fejlede%s",
            resultat["antal_kunder"], len(resultat["ok"]), len(resultat["fejlede"]),
            f" ({', '.join(resultat['fejlede'])})" if resultat["fejlede"] else "",
        )
        return 1 if resultat["fejlede"] else 0

    with ny_session() as session:
        try:
            kunde = _find_kunde(session, args.kunde, args.kundenummer)
            resultat = hent_og_gem_kontoplan(session, kunde.id)
        except FORVENTEDE_FEJL as fejl:
            print(f"Fejl: {rediger(str(fejl))}", file=sys.stderr)
            return 1
    print(f"Kontoplan for {kunde.navn} (id {kunde.id}) gemt: "
          f"{resultat['antal_konti']} konti, {resultat['fjernet']} fjernet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
