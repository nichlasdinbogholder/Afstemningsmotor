"""Hent en kundes kontoplan fra e-conomic og gem den i `accounts`.

    python -m app.adaptere.economic.kontoplan --kunde 12
    python -m app.adaptere.economic.kontoplan --kundenummer K-1001

Scriptet LÆSER kun fra e-conomic – det bogfører og ændrer intet dér.
Kontoplanen i vores database bliver en kopi af e-conomics: nye konti
tilføjes, ændrede opdateres, og konti der ikke længere findes, fjernes.
"""

import argparse
import logging
import sys
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adaptere.adgang import AdgangMangler, hent_adgang
from app.adaptere.economic.klient import EconomicFejl, EconomicKlient
from app.audit.models import AuditLog
from app.config import get_settings
from app.db import ny_session
from app.kunder.models import Client
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hent kontoplan fra e-conomic for én kunde.")
    gruppe = parser.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--kunde", type=int, help="kundens id i clients")
    gruppe.add_argument("--kundenummer", help="kundens kundenummer")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with ny_session() as session:
        try:
            kunde = _find_kunde(session, args.kunde, args.kundenummer)
            resultat = hent_og_gem_kontoplan(session, kunde.id)
        except (KontoplanFejl, EconomicFejl, AdgangMangler, KrypteringsFejl) as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 1
    print(
        f"Kontoplan for {kunde.navn} (id {kunde.id}) gemt: "
        f"{resultat['antal_konti']} konti, {resultat['fjernet']} fjernet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
