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
    # Kun gældende tokens (ikke tilbagekaldte). Har kunden flere aftaler i systemet, gættes der aldrig.
    credentials = session.scalars(
        select(Credential).where(
            Credential.client_id == client_id,
            Credential.system == system,
            Credential.status == "aktiv",
            Credential.revoked_at.is_(None),
        )
    ).all()
    if not credentials:
        raise AdgangMangler(f"Kunde {client_id} har ingen aktiv adgang til {system}")
    if len(credentials) > 1:
        raise AdgangMangler(f"Kunde {client_id} har flere aktive aftaler i {system} – aftalen skal vælges")
    credential = credentials[0]
    return Adgang(
        client_id=credential.client_id,
        system=credential.system,
        organisation_id=credential.organisation_id,
        token=dekrypter_token_til_adapter(credential.token_krypteret),
    )


def hent_token(session: Session, client_id: int, system: str) -> HemmeligtToken:
    """Hent kundens dekrypterede token til et system – lige før kaldet til systemet.

    Tokenet kommer pakket ind i et `HemmeligtToken`, som aldrig viser sin
    værdi ved print, logning eller i fejlbeskeder. Brug `.klartekst()` kun
    direkte i kaldet til e-conomic/Dinero.
    """
    return hent_adgang(session, client_id, system).token
