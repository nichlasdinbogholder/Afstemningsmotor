"""Fælles databaseopsætning (SQLAlchemy)."""

from functools import lru_cache

from sqlalchemy import CheckConstraint, Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import get_settings

# Faste navne på nøgler og regler i databasen, så migreringer bliver forudsigelige.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache
def get_engine() -> Engine:
    # hide_parameters=True: værdier i SQL-sætninger kommer aldrig med i
    # fejlbeskeder eller logs – heller ikke krypterede tokens.
    return create_engine(
        get_settings().database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
    )


def ny_session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


def kun_vaerdier(kolonne: str, vaerdier: tuple[str, ...], navn: str | None = None) -> CheckConstraint:
    """Regel i databasen: kolonnen må kun indeholde de faste værdier (eller være tom)."""
    liste = ", ".join(f"'{v}'" for v in vaerdier)
    return CheckConstraint(f"{kolonne} IN ({liste})", name=navn or f"{kolonne}_gyldig")
