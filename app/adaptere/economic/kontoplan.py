"""Hent en kundes kontoplan fra e-conomic og gem den i `accounts`.

    python -m app.adaptere.economic.kontoplan --kunde 12
    python -m app.adaptere.economic.kontoplan --kundenummer K-1001
    python -m app.adaptere.economic.kontoplan --alle

--alle henter for alle kunder, der ikke er opsagt og har en aktiv
e-conomic-adgang. Fejler én kunde, fortsætter den med de næste. Bruges af
den natlige kørsel (se app/planlaegning/natlig_kontoplan.py).

Scriptet LÆSER kun fra e-conomic – det bogfører og ændrer intet dér.
Kontoplanen i vores database bliver en kopi af e-conomics: nye konti
tilføjes, ændrede opdateres, og konti der ikke længere findes, fjernes.
"""

import argparse
import logging
import sys
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt, fx staff for audit_log)

from app.adaptere.adgang import AdgangMangler, hent_adgang
from app.adaptere.economic.klient import EconomicFejl, EconomicKlient
from app.audit.models import AuditLog
from app.config import get_settings
from app.db import ny_session
from app.kunder.models import Client, Credential
from app.regnskab.models import Account
from app.sikkerhed.kryptering import KrypteringsFejl
from app.sikkerhed.hemmeligheder import HemmeligtToken

log = logging.getLogger(__name__)

SYSTEM = "economic"


class KontoplanFejl(Exception):
    pass


def _app_secret_token() -> HemmeligtToken:
    vaerdi = get_settings().economic_app_secret_token
    if vaerdi is None or not vaerdi.get_secret_value():
        raise KontoplanFejl("ECONOMIC_APP_SECRET_TOKEN mangler i .env")
    return HemmeligtToken(vaerdi.get_secret_value())


def _decimal(vaerdi) -> Decimal | None:
    return None if vaerdi is None else Decimal(str(vaerdi))


def konto_til_raekke(tenant_id: int, konto: dict) -> dict:
    """Oversæt en konto fra e-conomic til en række i `accounts`."""
    return {
        "tenant_id": tenant_id,
        "system": SYSTEM,
        "kontonummer": konto["accountNumber"],
        "navn": konto.get("name") or "",
        "kontotype": konto.get("accountType"),
        "debet_kredit": konto.get("debitCredit"),
        "momskode": (konto.get("vatAccount") or {}).get("vatCode"),
        "spaerret": bool(konto.get("barred", False)),
        "direkte_posteringer_blokeret": bool(konto.get("blockDirectEntries", False)),
        "saldo": _decimal(konto.get("balance")),
        "kladdesaldo": _decimal(konto.get("draftBalance")),
        "raa_data": konto,
    }


def gem_kontoplan(session: Session, tenant_id: int, konti: list[dict]) -> dict:
    """Gem kontoplanen som én samlet ændring: tilføj/opdatér, og fjern forsvundne konti."""
    raekker = [konto_til_raekke(tenant_id, k) for k in konti]
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
            Account.system == SYSTEM,
            Account.kontonummer.not_in([r["kontonummer"] for r in raekker]),
        )
    ).rowcount
    resultat = {"antal_konti": len(raekker), "fjernet": fjernet}
    session.add(AuditLog(client_id=tenant_id, handling="kontoplan_hentet", detaljer=resultat))
    return resultat


def hent_og_gem_kontoplan(
    session: Session, tenant_id: int, transport=None, vent=None
) -> dict:
    """Hent kontoplanen fra e-conomic for én kunde og gem den."""
    adgang = hent_adgang(session, tenant_id, SYSTEM)
    with EconomicKlient(
        app_secret_token=_app_secret_token(),
        agreement_grant_token=adgang.token,
        base_url=get_settings().economic_api_base_url,
        transport=transport,
        vent=vent,
    ) as klient:
        konti = list(klient.hent_alle("/accounts"))
    if not konti:
        # En tom kontoplan er næsten altid en fejl – slet ikke den gemte kopi.
        raise KontoplanFejl("e-conomic returnerede ingen konti – intet er ændret")
    resultat = gem_kontoplan(session, tenant_id, konti)
    session.commit()
    return resultat


def _find_kunde(session: Session, kunde_id: int | None, kundenummer: str | None) -> Client:
    if kunde_id is not None:
        kunde = session.get(Client, kunde_id)
    else:
        kunde = session.scalars(select(Client).where(Client.kundenummer == kundenummer)).one_or_none()
    if kunde is None:
        raise KontoplanFejl("Kunden findes ikke")
    return kunde


FORVENTEDE_FEJL = (KontoplanFejl, EconomicFejl, AdgangMangler, KrypteringsFejl)


def kunder_med_economic(session: Session) -> list[Client]:
    """Kunder, der ikke er opsagt og har en aktiv e-conomic-adgang."""
    return list(session.scalars(
        select(Client)
        .join(Credential, Credential.client_id == Client.id)
        .where(
            Credential.system == SYSTEM,
            Credential.status == "aktiv",
            Client.status != "opsagt",
        )
        .order_by(Client.kundenummer)
    ))


def hent_for_alle(session: Session, transport=None, vent=None) -> dict:
    """Hent kontoplanen for alle e-conomic-kunder. Én kundes fejl stopper ikke de andre."""
    _app_secret_token()  # stop straks, hvis app-nøglen mangler – så fejler alle alligevel
    kunder = [(k.id, k.kundenummer, k.navn) for k in kunder_med_economic(session)]
    ok, fejlede = [], []
    for kunde_id, kundenummer, navn in kunder:
        try:
            resultat = hent_og_gem_kontoplan(session, kunde_id, transport=transport, vent=vent)
        except (*FORVENTEDE_FEJL, SQLAlchemyError) as fejl:
            session.rollback()
            besked = str(fejl).splitlines()[0][:300]
            log.error("Kunde %s (%s): kontoplan IKKE hentet – %s", kundenummer, navn, besked)
            fejlede.append(kundenummer)
            continue
        log.info(
            "Kunde %s (%s): %s konti, %s fjernet",
            kundenummer, navn, resultat["antal_konti"], resultat["fjernet"],
        )
        ok.append(kundenummer)
    return {"antal_kunder": len(kunder), "ok": ok, "fejlede": fejlede}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hent kontoplan fra e-conomic.")
    gruppe = parser.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--kunde", type=int, help="kundens id i clients")
    gruppe.add_argument("--kundenummer", help="kundens kundenummer")
    gruppe.add_argument("--alle", action="store_true", help="alle kunder med aktiv e-conomic-adgang")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.alle:
        with ny_session() as session:
            try:
                resultat = hent_for_alle(session)
            except KontoplanFejl as fejl:
                log.error("Natlig kørsel stoppet: %s", fejl)
                return 1
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
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 1
    print(
        f"Kontoplan for {kunde.navn} (id {kunde.id}) gemt: "
        f"{resultat['antal_konti']} konti, {resultat['fjernet']} fjernet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
