"""Fælles databaseopsætning (SQLAlchemy)."""

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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


def make_engine():
    # hide_parameters=True: værdier i SQL-sætninger kommer aldrig med i
    # fejlbeskeder eller logs – heller ikke krypterede tokens.
    return create_engine(
        get_settings().database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
    )


SessionLocal = sessionmaker(bind=make_engine(), expire_on_commit=False)
