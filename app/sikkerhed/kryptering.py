"""Kryptering af kundernes tokens.

Tokens krypteres med Fernet og hovednøglen CREDENTIALS_KEY fra .env, før de
skrives til databasen. Uden hovednøglen kan databasens indhold ikke læses.

Dekryptering sker KUN ét sted: `dekrypter_token_til_adapter()`, som kun
adapter-laget (app/adaptere) må kalde. Den giver et `HemmeligtToken` tilbage,
som aldrig viser sin værdi i logs, fejlbeskeder eller udskrifter.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.sikkerhed.hemmeligheder import HemmeligtToken, registrer_hemmelighed

# Alle Fernet-krypterede værdier starter med dette præfiks. En trigger i
# databasen afviser alt andet (se første migrering).
FERNET_PREFIX = "gAAAAA"


class KrypteringsFejl(Exception):
    """Fejl i krypteringen. Beskeden indeholder aldrig token eller nøgle."""


def generer_hovednoegle() -> str:
    """Lav en ny, tilfældig hovednøgle (til CREDENTIALS_KEY)."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    noegle = get_settings().credentials_key.get_secret_value()
    try:
        return Fernet(noegle.encode())
    except (ValueError, TypeError):
        # Nøglens værdi må ikke komme med i fejlbeskeden.
        raise KrypteringsFejl(
            "CREDENTIALS_KEY er ikke en gyldig Fernet-nøgle. "
            "Lav en ny med: python -m app.sikkerhed.ny_noegle"
        ) from None


def krypter_token(klartekst: str) -> str:
    """Krypter et token, så det kan gemmes i databasen."""
    if not isinstance(klartekst, str) or not klartekst:
        raise KrypteringsFejl("Token skal være en ikke-tom tekst")
    registrer_hemmelighed(klartekst)
    return _fernet().encrypt(klartekst.encode()).decode()


def dekrypter_token_til_adapter(krypteret: str) -> HemmeligtToken:
    """Det ENESTE sted i koden, hvor et token dekrypteres.

    Må kun kaldes fra adapter-laget (app/adaptere), lige før tokenet sendes
    til e-conomic eller Dinero.
    """
    try:
        klartekst = _fernet().decrypt(krypteret.encode()).decode()
    except (InvalidToken, AttributeError, UnicodeDecodeError):
        # Hverken det krypterede token eller nøglen må stå i fejlbeskeden.
        raise KrypteringsFejl(
            "Token kunne ikke dekrypteres – forkert hovednøgle eller ødelagte data"
        ) from None
    return HemmeligtToken(klartekst)
