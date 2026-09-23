"""Synkronisering af data fra kundernes regnskabssystem ind i cache-tabellerne.

Én funktion pr. ressource. Hver funktion:
1. henter bogmærket (cursor) fra sync_state,
2. kalder adapteren for kun det nye siden sidst (åbne poster: altid alt),
3. skriver til cache-tabellen som upsert (samme post giver aldrig to rækker),
4. opdaterer bogmærket –
alt i én transaktion via `synk_transaktion`, så bogmærket aldrig rykkes frem,
uden at data er gemt. Systemet kaldes KUN gennem adapter-laget.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt)
from app.adaptere.regnskab.base import RegnskabsAdapter, hent_adapter
from app.regnskab.models import CustomerCache, EntryCache, OpenEntryCache, SupplierCache
from app.synk.tilstand import synk_transaktion

# Så mange rækker pr. INSERT (PostgreSQL tillader højst 65.535 værdier pr. sætning).
BLOK = 1000


@dataclass(frozen=True)
class SynkResultat:
    ressource: str
    antal: int
    fjernet: int = 0


@contextmanager
def _adapter(session: Session, client_id: int, adapter: RegnskabsAdapter | None) -> Iterator:
    if adapter is not None:
        yield adapter
    else:
        with hent_adapter(session, client_id) as a:
            yield a


def _upsert(session: Session, model, raekker: list[dict], noegle: list[str]) -> None:
    """Indsæt eller opdatér rækker. Findes (client_id, nøgle) allerede, opdateres den."""
    # Samme post to gange i ét svar (fx hvis sider forskydes undervejs): behold den sidste.
    raekker = list({tuple(r[k] for k in ("client_id", *noegle)): r for r in raekker}.values())
    for start in range(0, len(raekker), BLOK):
        blok = raekker[start:start + BLOK]
        stmt = insert(model).values(blok)
        opdater = {k: stmt.excluded[k] for k in blok[0] if k not in ("client_id", *noegle)}
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["client_id", *noegle],
                set_=opdater | {"sidst_set": func.now()},
            )
        )


def synk_customers(session: Session, client_id: int, adapter: RegnskabsAdapter | None = None) -> SynkResultat:
    with synk_transaktion(session, client_id, "customers") as synk:
        with _adapter(session, client_id, adapter) as a:
            kunder = a.hent_customers()
        _upsert(session, CustomerCache,
                [{"client_id": client_id, **asdict(k)} for k in kunder], ["kundenummer"])
        synk.gennemfoert(None, antal_hentet=len(kunder))
    return SynkResultat("customers", len(kunder))


def synk_suppliers(session: Session, client_id: int, adapter: RegnskabsAdapter | None = None) -> SynkResultat:
    with synk_transaktion(session, client_id, "suppliers") as synk:
        with _adapter(session, client_id, adapter) as a:
            leverandoerer = a.hent_suppliers()
        _upsert(session, SupplierCache,
                [{"client_id": client_id, **asdict(le)} for le in leverandoerer], ["leverandoernummer"])
        synk.gennemfoert(None, antal_hentet=len(leverandoerer))
    return SynkResultat("suppliers", len(leverandoerer))


def synk_entries(session: Session, client_id: int, adapter: RegnskabsAdapter | None = None) -> SynkResultat:
    """Inkrementelt: kun poster nyere end bogmærket."""
    with synk_transaktion(session, client_id, "entries") as synk:
        with _adapter(session, client_id, adapter) as a:
            svar = a.hent_entries(synk.cursor)
        _upsert(session, EntryCache,
                [{"client_id": client_id, **asdict(p)} for p in svar.poster], ["bogfoert_id"])
        synk.gennemfoert(svar.ny_cursor, svar.cursor_type, antal_hentet=len(svar.poster))
    return SynkResultat("entries", len(svar.poster))


def synk_open_entries(session: Session, client_id: int, adapter: RegnskabsAdapter | None = None) -> SynkResultat:
    """Altid fuldt: restbeløb ændrer sig på gamle poster. Betalte poster fjernes."""
    with synk_transaktion(session, client_id, "open_entries") as synk:
        with _adapter(session, client_id, adapter) as a:
            poster = a.hent_open_entries()
        _upsert(session, OpenEntryCache,
                [{"client_id": client_id, **asdict(p)} for p in poster], ["bogfoert_id"])
        fjernet = session.execute(
            delete(OpenEntryCache).where(
                OpenEntryCache.client_id == client_id,
                OpenEntryCache.bogfoert_id.not_in([p.bogfoert_id for p in poster]),
            )
        ).rowcount
        _udfyld_partnavne(session, client_id)
        synk.gennemfoert(None, antal_hentet=len(poster))
    return SynkResultat("open_entries", len(poster), fjernet)


def _udfyld_partnavne(session: Session, client_id: int) -> None:
    """Partnavn står ikke på posten – slå det op i de hentede kunder og leverandører."""
    for type_, model, nummer in (
        ("debitor", CustomerCache, CustomerCache.kundenummer),
        ("kreditor", SupplierCache, SupplierCache.leverandoernummer),
    ):
        navn = (
            select(model.navn)
            .where(model.client_id == client_id, nummer == OpenEntryCache.partnummer)
            .scalar_subquery()
        )
        session.execute(
            update(OpenEntryCache)
            .where(OpenEntryCache.client_id == client_id, OpenEntryCache.type == type_)
            .values(partnavn=navn)
            .execution_options(synchronize_session=False)
        )


# Rækkefølge ved fuld kørsel: kunder/leverandører før åbne poster (partnavne).
SYNK_FUNKTIONER = {
    "customers": synk_customers,
    "suppliers": synk_suppliers,
    "entries": synk_entries,
    "open_entries": synk_open_entries,
}
