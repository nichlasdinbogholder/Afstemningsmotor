"""Synkronisering af data fra kundernes regnskabssystem ind i cache-tabellerne.

Én funktion pr. ressource. Hver funktion:
1. henter bogmærket (cursor) fra sync_state,
2. kalder adapteren for kun det nye siden sidst (åbne poster: altid alt),
3. skriver til cache-tabellen som upsert (samme post giver aldrig to rækker),
4. opdaterer bogmærket –
alt i én transaktion via `synk_transaktion`, så bogmærket aldrig rykkes frem,
uden at data er gemt. Systemet kaldes KUN gennem adapter-laget.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from sqlalchemy import delete, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt)
from app.adaptere.regnskab.base import AccountingProvider, DelvisHentet, PosteringsSvar, hent_adapter
from app.regnskab.models import AccountingYearCache, CustomerCache, EntryCache, JournalEntryCache, OpenEntryCache, SupplierCache
from app.synk.tilstand import registrer_fejl, synk_transaktion

log = logging.getLogger(__name__)

# Efter så mange nye poster i én kørsel beder vi databasen opdatere sin viden om
# tabellen (ANALYZE). Ellers kan den vælge en meget langsom fremgangsmåde for de
# regler, der køres lige bagefter (set: over 60 sek. i stedet for få sek.).
ANALYZE_EFTER = 1000

# Så mange rækker pr. INSERT (PostgreSQL tillader højst 65.535 værdier pr. sætning).
BLOK = 1000


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


def _upsert(session: Session, model, raekker: list[dict], noegle: list[str]) -> tuple[int, int]:
    """Indsæt eller opdatér rækker. Findes (client_id, nøgle) allerede, opdateres den.

    Returnerer (antal nye, antal opdaterede).
    """
    # Samme post to gange i ét svar (fx hvis sider forskydes undervejs): behold den sidste.
    raekker = list({tuple(r[k] for k in ("client_id", *noegle)): r for r in raekker}.values())
    nye = opdaterede = 0
    for start in range(0, len(raekker), BLOK):
        blok = raekker[start:start + BLOK]
        stmt = insert(model).values(blok)
        opdater = {k: stmt.excluded[k] for k in blok[0] if k not in ("client_id", *noegle)}
        # xmax = 0 betyder i PostgreSQL, at rækken lige er indsat (ikke opdateret).
        ny_raekke = literal_column("xmax = 0")
        for (er_ny,) in session.execute(
            stmt.on_conflict_do_update(
                index_elements=["client_id", *noegle],
                set_=opdater | {"sidst_set": func.now()},
            ).returning(ny_raekke)
        ):
            nye += bool(er_ny)
            opdaterede += not er_ny
    return nye, opdaterede


def synk_customers(session: Session, client_id: int, adapter: AccountingProvider | None = None) -> SynkResultat:
    with synk_transaktion(session, client_id, "customers") as synk:
        with _adapter(session, client_id, adapter) as a:
            kunder = a.fetch_customers()
        _upsert(session, CustomerCache,
                [{"client_id": client_id, **asdict(k)} for k in kunder], ["kundenummer"])
        synk.gennemfoert(None, antal_hentet=len(kunder))
    return SynkResultat("customers", len(kunder))


def synk_suppliers(session: Session, client_id: int, adapter: AccountingProvider | None = None) -> SynkResultat:
    with synk_transaktion(session, client_id, "suppliers") as synk:
        with _adapter(session, client_id, adapter) as a:
            leverandoerer = a.fetch_suppliers()
        _upsert(session, SupplierCache,
                [{"client_id": client_id, **asdict(le)} for le in leverandoerer], ["leverandoernummer"])
        synk.gennemfoert(None, antal_hentet=len(leverandoerer))
    return SynkResultat("suppliers", len(leverandoerer))


def synk_entries(session: Session, client_id: int, adapter: AccountingProvider | None = None) -> SynkResultat:
    """Inkrementelt: kun poster nyere end bogmærket.

    Stopper hentningen midtvejs (`DelvisHentet`), gemmes de poster, der nåede at
    komme, sammen med adapterens SIKRE bogmærke – og den oprindelige fejl rejses
    bagefter, så jobbet prøves igen og fortsætter derfra.
    """
    delvis: DelvisHentet | None = None
    with synk_transaktion(session, client_id, "entries") as synk:
        with _adapter(session, client_id, adapter) as a:
            try:
                svar = a.fetch_entries(synk.cursor)
            except DelvisHentet as fejl:
                delvis = fejl
                svar = PosteringsSvar(fejl.poster, fejl.sikker_cursor, fejl.cursor_type)
        nye, opdaterede = _upsert(session, EntryCache,
                                  [{"client_id": client_id, **asdict(p)} for p in svar.poster],
                                  ["bogfoert_id"])
        synk.gennemfoert(svar.ny_cursor, svar.cursor_type, antal_hentet=len(svar.poster))
    if delvis is not None:
        log.warning("Kunde %s: entries stoppede midtvejs – %s poster og bogmærke %s er gemt",
                    client_id, len(svar.poster), svar.ny_cursor)
        if not getattr(delvis.aarsag, "taeller_ikke_som_fejl", False):
            registrer_fejl(session, client_id, "entries", delvis.aarsag)
        raise delvis.aarsag
    if nye >= ANALYZE_EFTER:
        session.execute(text("ANALYZE entries"))
        session.commit()
    return SynkResultat("entries", len(svar.poster), nye=nye, opdaterede=opdaterede,
                        cursor=svar.ny_cursor)


def synk_open_entries(session: Session, client_id: int, adapter: AccountingProvider | None = None) -> SynkResultat:
    """Altid fuldt: restbeløb ændrer sig på gamle poster. Betalte poster fjernes."""
    with synk_transaktion(session, client_id, "open_entries") as synk:
        with _adapter(session, client_id, adapter) as a:
            poster = a.fetch_open_entries()
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


def synk_journals(session: Session, client_id: int, adapter: AccountingProvider | None = None) -> SynkResultat:
    """Kassekladdernes linjer (ikke bogført). Altid fuldt: alt for kunden erstattes."""
    with synk_transaktion(session, client_id, "journals") as synk:
        with _adapter(session, client_id, adapter) as a:
            linjer = []
            for kladde in a.fetch_journals():
                linjer += [{"client_id": client_id, "kladde_navn": kladde.navn, **asdict(p)}
                           for p in a.fetch_journal_entries(kladde.nummer)]
        fjernet = session.execute(delete(JournalEntryCache).where(JournalEntryCache.client_id == client_id)).rowcount
        if linjer:
            session.execute(insert(JournalEntryCache), linjer)
        synk.gennemfoert(None, antal_hentet=len(linjer))
    return SynkResultat("journals", len(linjer), fjernet)


def synk_accounting_years(session: Session, client_id: int,
                          adapter: AccountingProvider | None = None) -> SynkResultat:
    """Regnskabsår og om de er afsluttet. Altid fuldt."""
    with synk_transaktion(session, client_id, "accounting_years") as synk:
        with _adapter(session, client_id, adapter) as a:
            aar = a.fetch_accounting_years()
        _upsert(session, AccountingYearCache, [{"client_id": client_id, **asdict(x)} for x in aar], ["navn"])
        fjernet = session.execute(delete(AccountingYearCache).where(
            AccountingYearCache.client_id == client_id,
            AccountingYearCache.navn.not_in([x.navn for x in aar]))).rowcount
        synk.gennemfoert(None, antal_hentet=len(aar))
    return SynkResultat("accounting_years", len(aar), fjernet)


# Rækkefølge ved fuld kørsel: kunder/leverandører før åbne poster (partnavne).
SYNK_FUNKTIONER = {
    "accounting_years": synk_accounting_years,
    "customers": synk_customers,
    "suppliers": synk_suppliers,
    "entries": synk_entries,
    "open_entries": synk_open_entries,
    "journals": synk_journals,
}
