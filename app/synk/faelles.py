"""Fælles hjælpere til synkroniseringen (bruges af ressourcer.py og opkraevning.py)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.adaptere.regnskab.base import AccountingProvider, hent_adapter


@dataclass(frozen=True)
class SynkResultat:
    ressource: str
    antal: int
    fjernet: int = 0
    nye: int = 0
    opdaterede: int = 0
    cursor: str | None = None


@contextmanager
def _adapter(session: Session, client_id: int, adapter: AccountingProvider | None) -> Iterator:
    if adapter is not None:
        yield adapter
    else:
        with hent_adapter(session, client_id) as a:
            yield a
