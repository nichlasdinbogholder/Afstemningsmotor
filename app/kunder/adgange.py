"""Gem en kundes token til e-conomic eller Dinero.

Tokenet krypteres, før det skrives til databasen. Denne del af koden kan
kun kryptere – dekryptering sker udelukkende i adapter-laget
(`app.adaptere.adgang.hent_token`).
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt, fx staff for audit_log)

from app.audit.models import AuditLog
from app.kunder.models import SYSTEMER, Client, Credential


class UgyldigAdgang(Exception):
    """Forkerte oplysninger. Beskeden indeholder aldrig tokenet."""


def gem_token(
    session: Session,
    client_id: int,
    system: str,
    token: str,
    *,
    organisation_id: str | None = None,
    aftale_id: str | None = None,
    staff_id: int | None = None,
) -> Credential:
    """Gem (eller udskift) kundens token til et system. Tokenet krypteres straks.

    Findes der allerede en adgang for kunden og systemet, bliver tokenet
    udskiftet, status sat til 'aktiv' og `sidst_fornyet` opdateret.
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

    credential = session.scalars(
        select(Credential).where(Credential.client_id == client_id, Credential.system == system)
    ).one_or_none()
    ny = credential is None
    if ny:
        credential = Credential(client_id=client_id, system=system)
        session.add(credential)

    credential.saet_token(token)
    credential.status = "aktiv"
    credential.sidst_fornyet = func.now()
    if organisation_id is not None:
        credential.organisation_id = organisation_id
    if aftale_id is not None:
        credential.aftale_id = aftale_id

    session.add(AuditLog(
        client_id=client_id,
        staff_id=staff_id,
        handling="token_gemt" if ny else "token_udskiftet",
        detaljer={"system": system},  # aldrig selve tokenet
    ))
    session.flush()
    return credential
