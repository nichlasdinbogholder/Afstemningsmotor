"""Fælles testopsætning.

Testene bruger altid en frisk, midlertidig hovednøgle – aldrig den rigtige
CREDENTIALS_KEY fra .env. Miljøvariabler vinder over .env.
"""

import os
import secrets

import pytest
from cryptography.fernet import Fernet

os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture
def token() -> str:
    """Et tilfældigt, falsk token, der kun findes under testen."""
    return "testtoken-" + secrets.token_urlsafe(32)


@pytest.fixture
def db_session():
    """En databasesession, hvor alt rulles tilbage efter testen."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from app.db import get_engine

    try:
        forbindelse = get_engine().connect()
    except OperationalError:
        pytest.skip("PostgreSQL kører ikke – start den med: docker compose up -d")
    transaktion = forbindelse.begin()
    session = Session(bind=forbindelse, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaktion.rollback()
        forbindelse.close()
