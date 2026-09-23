"""Kryptering og maskering af kunde-tokens.

Tokens krypteres med Fernet (AES + integritetstjek) og nøglen
TOKEN_ENCRYPTION_KEY fra .env. Uden nøglen kan databasens indhold ikke
læses i klartekst.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

# Alle Fernet-krypterede værdier starter med dette præfiks. Databasen har en
# regel, der afviser alt andet, så et token i klartekst ikke kan gemmes ved en fejl.
FERNET_PREFIX = "gAAAAA"


class TokenDekrypteringsfejl(Exception):
    """Token kunne ikke dekrypteres (forkert nøgle eller ødelagte data)."""


def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.get_secret_value().encode())


def krypter_token(klartekst: str) -> str:
    if not klartekst:
        raise ValueError("Token må ikke være tomt")
    return _fernet().encrypt(klartekst.encode()).decode()


def dekrypter_token(krypteret: str) -> str:
    try:
        return _fernet().decrypt(krypteret.encode()).decode()
    except InvalidToken:
        # Fejlbeskeden må ikke indeholde hverken token eller nøgle.
        raise TokenDekrypteringsfejl("Token kunne ikke dekrypteres") from None


def maskér(værdi: str | None, synlige: int = 4) -> str:
    """Vis kun de sidste tegn, fx '****abcd'."""
    if not værdi:
        return "****"
    if len(værdi) <= synlige * 2:
        return "****"
    return "****" + værdi[-synlige:]
