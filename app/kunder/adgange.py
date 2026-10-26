"""Gem en kundes token til e-conomic eller Dinero.

Tokenet krypteres, før det skrives til databasen. Denne del af koden kan
kun kryptere – dekryptering sker udelukkende i adapter-laget
(`app.adaptere.adgang.hent_token`).
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt, fx staff for audit_log)

from app.audit.models import AuditLog
from app.db import saet_aktoer
from app.kunder.models import SYSTEMER, Client, Credential
from app.kunder.tabeller import KundeLedger
from app.personale.models import Staff

SYSTEMNAVNE = {"economic": "e-conomic", "dinero": "Dinero"}


class UgyldigAdgang(Exception):
    """Forkerte oplysninger. Beskeden indeholder aldrig tokenet."""


def find_eller_opret_aftale(session: Session, client_id: int, system: str) -> KundeLedger:
    """Kundens regnskabsaftale i systemet. Har kunden flere, skal det vælges, hvilken (kommer i trin 7) – der gættes
    aldrig."""
    aftaler = list(session.scalars(select(KundeLedger).where(
        KundeLedger.client_id == client_id, KundeLedger.system == system, KundeLedger.archived_at.is_(None))))
    if len(aftaler) > 1:
        raise UgyldigAdgang("Kunden har flere regnskabsaftaler i systemet – vælg aftalen på kundekortet")
    if aftaler:
        return aftaler[0]
    aftale = KundeLedger(client_id=client_id, system=system, label=SYSTEMNAVNE[system])
    session.add(aftale)
    session.flush()
    return aftale


def gem_token(
    session: Session,
    client_id: int,
    system: str,
    token: str,
    *,
    staff_id: int,
    organisation_id: str | None = None,
    aftale_id: str | None = None,
) -> Credential:
    """Gem (eller udskift) kundens token til et system. Tokenet krypteres straks.

    KUN en administrator (staff_id) må – det håndhæves også af databasen (triggeren kunde_credential_guard).
    Et gemt token ændres aldrig: findes der et gældende token for aftalen, tilbagekaldes det, og der indsættes en
    ny række. Begge dele skrives i kundens log (med fingeraftryk – aldrig tokenet).
    Ændringen skal gemmes med `session.commit()` af den, der kalder.
    """
    if system not in SYSTEMER:
        raise UgyldigAdgang(f"Ukendt system '{system}' – brug {' eller '.join(SYSTEMER)}")
    if not isinstance(token, str) or not token.strip():
        raise UgyldigAdgang("Tokenet er tomt")
    if token != token.strip():
        raise UgyldigAdgang("Tokenet har mellemrum eller linjeskift i starten eller slutningen")
    if session.get(Client, client_id) is None:
        raise UgyldigAdgang(f"Kunde {client_id} findes ikke")
    af = session.get(Staff, staff_id)
    if af is None or not af.aktiv or af.rolle != "admin":
        raise UgyldigAdgang("Kun en administrator kan sætte et token")
    saet_aktoer(session, af.id)

    aftale = find_eller_opret_aftale(session, client_id, system)
    gammel = session.scalars(select(Credential).where(
        Credential.ledger_id == aftale.id, Credential.revoked_at.is_(None))).one_or_none()
    if gammel is not None:
        gammel.status, gammel.revoked_at = "tilbagekaldt", func.now()
        session.flush()

    credential = Credential(client_id=client_id, ledger_id=aftale.id, system=system, status="aktiv",
                            organisation_id=organisation_id if organisation_id is not None
                            else (gammel.organisation_id if gammel else None),
                            aftale_id=aftale_id if aftale_id is not None else (gammel.aftale_id if gammel else None),
                            sidst_fornyet=func.now(), set_by=af.id)
    credential.saet_token(token)
    session.add(credential)
    aftale.status = "aktiv"

    session.add(AuditLog(
        client_id=client_id,
        staff_id=af.id,
        handling="token_gemt" if gammel is None else "token_udskiftet",
        detaljer={"system": system, "aftale": aftale.id},  # aldrig selve tokenet
    ))
    session.flush()
    return credential
