"""Fælles hjælp til testene: læg et token ind som en administrator – ad samme vej som i drift (gem_token).

Databasen afviser tokens, der ikke sættes af en aktiv administrator (triggeren kunde_credential_guard)."""

from sqlalchemy import select

from app.kunder.adgange import gem_token
from app.kunder.models import Credential
from app.personale.models import Staff

TEST_ADMIN = "token-admin-test@dinbogholder.dk"


def token_admin(session) -> Staff:
    admin = session.scalars(select(Staff).where(Staff.email == TEST_ADMIN)).one_or_none()
    if admin is None:
        admin = Staff(navn="Token Admin", email=TEST_ADMIN, rolle="admin", aktiv=True)
        session.add(admin)
        session.flush()
    return admin


def laeg_token(session, client_id: int, system: str, token: str, organisation_id: str | None = None) -> Credential:
    return gem_token(session, client_id, system, token, staff_id=token_admin(session).id,
                     organisation_id=organisation_id)
