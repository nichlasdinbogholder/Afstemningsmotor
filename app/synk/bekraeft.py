"""Bekræft, at synkroniseringen virker – mod det RIGTIGE regnskabssystem.

    python -m app.synk.bekraeft --kundenummer 40850635

Kommandoen:
1. tjekker forudsætninger (database opdateret, kunde aktiv, adgang og app-nøgle),
2. kører en synkronisering af alle ressourcer,
3. henter data fra systemet IGEN (direkte via adapteren) og sammenligner med
   det, der ligger i databasen,
4. kører posteringer én gang til og tjekker, at intet bliver dobbelt, og at
   bogmærket står stille.

Den skriver ✔ eller ✘ ud for hvert tjek og slutter med
"BEKRÆFTET" (afslutningskode 0) eller "IKKE BEKRÆFTET" (afslutningskode 1).
Den skriver kun til vores egen database – aldrig til regnskabssystemet.
"""

import argparse
import sys

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.adaptere.regnskab.base import hent_adapter, understoettede_systemer
from app.db import ny_session
from app.kunder.models import Client, Credential
from app.regnskab.models import CustomerCache, EntryCache, OpenEntryCache, SupplierCache
from app.sikkerhed.hemmeligheder import rediger
from app.synk.models import SyncState
from app.synk.ressourcer import SYNK_FUNKTIONER
from app.synk.tilstand import hent_cursor


class _Tjek:
    def __init__(self) -> None:
        self.fejl = 0

    def __call__(self, ok: bool, tekst: str, detalje: str = "") -> bool:
        print(f"  {'✔' if ok else '✘'} {tekst}{f'  ({detalje})' if detalje else ''}")
        self.fejl += not ok
        return ok


def _database_opdateret(session: Session) -> tuple[bool, str]:
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    aktuel = MigrationContext.configure(session.connection()).get_current_revision()
    return aktuel == head, f"databasen: {aktuel}, nyeste: {head}"


def _antal(session: Session, model, client_id: int) -> int:
    return session.scalar(select(func.count()).select_from(model).where(model.client_id == client_id))


def _fejltekst(fejl: Exception) -> str:
    return rediger(f"{type(fejl).__name__}: {fejl}").splitlines()[0][:200]


def bekraeft(session: Session, kundenummer: str, adapter_fabrik=hent_adapter,
             tjek_database: bool = True) -> bool:
    tjek = _Tjek()

    print("1. Forudsætninger")
    if tjek_database:
        ok, detalje = _database_opdateret(session)
        tjek(ok, "Databasen er opdateret (alembic upgrade head)", detalje)
    kunde = session.scalars(select(Client).where(Client.kundenummer == kundenummer)).one_or_none()
    if not tjek(kunde is not None, f"Kunde {kundenummer} findes"):
        return _slut(tjek)
    tjek(kunde.status == "aktiv", "Kunden har status 'aktiv'", f"status: {kunde.status}")
    tjek(kunde.regnskabssystem in understoettede_systemer(), "Regnskabssystemet understøttes",
         f"{kunde.regnskabssystem}")
    adgang = session.scalar(select(func.count()).select_from(Credential).where(
        Credential.client_id == kunde.id, Credential.system == kunde.regnskabssystem,
        Credential.status == "aktiv"))
    tjek(adgang == 1, "Kunden har en aktiv adgang (token) til systemet")
    if tjek.fejl:
        return _slut(tjek)

    print("2. Synkronisering af alle ressourcer")
    for ressource, funktion in SYNK_FUNKTIONER.items():
        try:
            with adapter_fabrik(session, kunde.id) as adapter:
                resultat = funktion(session, kunde.id, adapter)
            tjek(True, f"{ressource}: hentet", f"{resultat.antal} rækker"
                 + (f", {resultat.fjernet} betalte fjernet" if resultat.fjernet else ""))
        except Exception as fejl:  # noqa: BLE001
            tjek(False, f"{ressource}: hentet", _fejltekst(fejl))
    if tjek.fejl:
        return _slut(tjek)

    print("3. Databasen stemmer med systemet (hentet igen direkte fra systemet)")
    try:
        with adapter_fabrik(session, kunde.id) as adapter:
            kunder = {k.kundenummer for k in adapter.fetch_customers()}
            leverandoerer = {le.leverandoernummer for le in adapter.fetch_suppliers()}
            aabne = {p.bogfoert_id: p.restbeloeb for p in adapter.fetch_open_entries()}
    except Exception as fejl:  # noqa: BLE001
        tjek(False, "Hent data fra systemet til sammenligning", _fejltekst(fejl))
        return _slut(tjek)

    db_kunder = set(session.scalars(select(CustomerCache.kundenummer).where(CustomerCache.client_id == kunde.id)))
    db_lev = set(session.scalars(select(SupplierCache.leverandoernummer).where(SupplierCache.client_id == kunde.id)))
    db_aabne = dict(session.execute(select(OpenEntryCache.bogfoert_id, OpenEntryCache.restbeloeb)
                                    .where(OpenEntryCache.client_id == kunde.id)).all())
    tjek(kunder <= db_kunder, "Alle kunder fra systemet ligger i databasen",
         f"systemet: {len(kunder)}, databasen: {len(db_kunder)}")
    tjek(leverandoerer <= db_lev, "Alle leverandører fra systemet ligger i databasen",
         f"systemet: {len(leverandoerer)}, databasen: {len(db_lev)}")
    tjek(aabne == db_aabne, "Åbne poster og restbeløb er præcis de samme som i systemet",
         f"systemet: {len(aabne)}, databasen: {len(db_aabne)}")

    cursor = hent_cursor(session, kunde.id, "entries").vaerdi
    hoejeste = session.scalar(select(func.max(EntryCache.bogfoert_id)).where(EntryCache.client_id == kunde.id))
    tjek(cursor == (str(hoejeste) if hoejeste is not None else None),
         "Bogmærket for posteringer = højeste gemte post", f"bogmærke: {cursor}, højeste: {hoejeste}")

    print("4. Ingen dubletter, og bogmærket står stille ved ny kørsel")
    foer = _antal(session, EntryCache, kunde.id)
    try:
        with adapter_fabrik(session, kunde.id) as adapter:
            igen = SYNK_FUNKTIONER["entries"](session, kunde.id, adapter)
        tjek(True, "Posteringer kørt igen", f"{igen.antal} nye")
    except Exception as fejl:  # noqa: BLE001
        tjek(False, "Posteringer kørt igen", _fejltekst(fejl))
        return _slut(tjek)
    efter = _antal(session, EntryCache, kunde.id)
    tjek(efter == foer + igen.antal, "Antal posteringer steg kun med de nye", f"før: {foer}, efter: {efter}")
    nyt_cursor = hent_cursor(session, kunde.id, "entries").vaerdi
    tjek(igen.antal > 0 or nyt_cursor == cursor, "Bogmærket rykkede sig ikke uden nye poster",
         f"før: {cursor}, efter: {nyt_cursor}")
    for model, noegle in ((CustomerCache, CustomerCache.kundenummer), (SupplierCache, SupplierCache.leverandoernummer),
                          (EntryCache, EntryCache.bogfoert_id), (OpenEntryCache, OpenEntryCache.bogfoert_id)):
        antal = _antal(session, model, kunde.id)
        unikke = session.scalar(select(func.count(func.distinct(noegle))).where(model.client_id == kunde.id))
        tjek(antal == unikke, f"Ingen dubletter i {model.__tablename__}", f"{antal} rækker")

    tilstande = session.scalars(select(SyncState).where(
        SyncState.client_id == kunde.id, SyncState.ressource.in_(list(SYNK_FUNKTIONER)))).all()
    tjek(len(tilstande) == len(SYNK_FUNKTIONER) and all(t.status == "ok" and t.antal_fejl_i_traek == 0 for t in tilstande),
         "sync_state: alle ressourcer står som 'ok' uden fejl",
         ", ".join(f"{t.ressource}={t.status}" for t in tilstande))
    return _slut(tjek)


def _slut(tjek: _Tjek) -> bool:
    print()
    print("BEKRÆFTET – synkroniseringen virker." if not tjek.fejl
          else f"IKKE BEKRÆFTET – {tjek.fejl} tjek fejlede (se ✘ ovenfor).")
    return not tjek.fejl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bekræft synkroniseringen mod det rigtige system.")
    parser.add_argument("--kundenummer", required=True)
    args = parser.parse_args(argv)
    with ny_session() as session:
        return 0 if bekraeft(session, args.kundenummer) else 1


if __name__ == "__main__":
    sys.exit(main())
