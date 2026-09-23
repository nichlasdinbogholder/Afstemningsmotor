"""Adapter-lagets adgang til kundernes tokens.

Adaptere til e-conomic og Dinero henter en kundes adgang her. Det er det
eneste sted, der kalder `dekrypter_token_til_adapter()`.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kunder.models import Credential
from app.sikkerhed.hemmeligheder import HemmeligtToken
from app.sikkerhed.kryptering import dekrypter_token_til_adapter


class AdgangMangler(Exception):
    """Kunden har ingen aktiv adgang til systemet."""


@dataclass(frozen=True)
class Adgang:
    client_id: int
    system: str
    organisation_id: str | None
    token: HemmeligtToken  # vises altid maskeret


def hent_adgang(session: Session, client_id: int, system: str) -> Adgang:
    """Hent en kundes aktive adgang med dekrypteret (men beskyttet) token."""
    credential = session.scalars(
        select(Credential).where(
            Credential.client_id == client_id,
            Credential.system == system,
            Credential.status == "aktiv",
        )
    ).one_or_none()
    if credential is None:
        raise AdgangMangler(f"Kunde {client_id} har ingen aktiv adgang til {system}")
    return Adgang(
        client_id=credential.client_id,
        system=credential.system,
        organisation_id=credential.organisation_id,
        token=dekrypter_token_til_adapter(credential.token_krypteret),
    )
