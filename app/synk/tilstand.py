"""Læs og skriv synkroniseringstilstand (sync_state).

Den gyldne regel: bogmærket (cursor) rykkes KUN frem i samme transaktion
som de data, det dækker over. Brug derfor altid `synk_transaktion`:

    with synk_transaktion(session, client_id, "entries") as synk:
        fra = synk.cursor                      # hvor vi slap sidst (None = forfra)
        data = hent_fra_systemet(fra)
        gem_i_databasen(session, data)         # samme session/transaktion
        synk.gennemfoert(ny_cursor, "dato", antal_hentet=len(data))

Går alt godt, gemmes data OG nyt bogmærke samlet (én commit). Fejler noget –
også selve gemningen – rulles begge tilbage, bogmærket står uændret, og
fejlen registreres i en ny transaktion (fejltæller + udskudt næste kørsel).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.kunder.models import Client
from app.sikkerhed.hemmeligheder import rediger
from app.synk.models import CURSOR_TYPER, RESSOURCER, SyncState

# Efter så mange fejl i træk sættes status til 'fejlet' (kræver handling).
FEJL_GRAENSE = 5
BASIS_VENTETID = timedelta(minutes=5)
MAKS_VENTETID = timedelta(hours=24)
STANDARD_INTERVAL = timedelta(hours=24)


class SynkFejl(Exception):
    pass


class KundeIkkeAktiv(SynkFejl):
    """Kunden er opsagt eller på pause – må aldrig synkroniseres."""


class SynkDeaktiveret(SynkFejl):
    """Synkronisering af ressourcen er slået fra for kunden."""


class SynkIGang(SynkFejl):
    """En anden kørsel er allerede i gang for samme kunde og ressource."""


@dataclass(frozen=True)
class Cursor:
    vaerdi: str | None
    type: str | None


def _tjek_ressource(ressource: str) -> None:
    if ressource not in RESSOURCER:
        raise SynkFejl(f"Ukendt ressource '{ressource}' – kendte: {', '.join(RESSOURCER)}")


def ventetid_efter_fejl(antal_fejl: int) -> timedelta:
    """5 min, 10 min, 20 min, … højst 24 timer."""
    faktor = 2 ** min(max(antal_fejl - 1, 0), 20)  # loft på eksponenten undgår overløb
    return min(BASIS_VENTETID * faktor, MAKS_VENTETID)


# --- Læs --------------------------------------------------------------------


def hent_tilstand(session: Session, client_id: int, ressource: str) -> SyncState:
    """Hent tilstanden – opret den (tom cursor, status ok), hvis den ikke findes."""
    _tjek_ressource(ressource)
    session.execute(
        insert(SyncState)
        .values(client_id=client_id, ressource=ressource)
        .on_conflict_do_nothing(constraint="uq_sync_state_kunde_ressource")
    )
    return session.scalars(
        select(SyncState)
        .where(SyncState.client_id == client_id, SyncState.ressource == ressource)
        .execution_options(populate_existing=True)  # altid friske værdier fra databasen
    ).one()


def hent_cursor(session: Session, client_id: int, ressource: str) -> Cursor:
    """Hvor vi slap sidst. `Cursor(None, …)` betyder: hent alt forfra."""
    _tjek_ressource(ressource)
    raekke = session.execute(
        select(SyncState.cursor, SyncState.cursor_type).where(
            SyncState.client_id == client_id, SyncState.ressource == ressource
        )
    ).first()
    return Cursor(*raekke) if raekke else Cursor(None, None)


def klar_til_koersel(session: Session, ressource: str | None = None) -> list[tuple[int, str]]:
    """(client_id, ressource) der skal køres nu. Kun aktive kunder – aldrig opsagt eller pause."""
    stmt = (
        select(SyncState.client_id, SyncState.ressource)
        .join(Client, Client.id == SyncState.client_id)
        .where(
            Client.status == "aktiv",
            SyncState.status != "deaktiveret",
            SyncState.naeste_koersel <= func.now(),
        )
        .order_by(SyncState.naeste_koersel)
    )
    if ressource is not None:
        stmt = stmt.where(SyncState.ressource == ressource)
    return [tuple(r) for r in session.execute(stmt)]


def kraever_handling(session: Session) -> list[dict]:
    """Kunder, der er bagud eller fejler (fra databasevisningen synk_kraever_handling)."""
    return [dict(r._mapping) for r in session.execute(text("SELECT * FROM synk_kraever_handling"))]


# --- Skriv ------------------------------------------------------------------


def registrer_succes(
    session: Session,
    client_id: int,
    ressource: str,
    ny_cursor: str | None,
    cursor_type: str | None,
    antal_hentet: int,
    interval: timedelta = STANDARD_INTERVAL,
) -> None:
    """Gem nyt bogmærke, sæt sidste_ok, nulstil fejltælleren og planlæg næste kørsel.

    Committer IKKE: skal kaldes i samme transaktion som de data, bogmærket
    dækker over. Brug normalt `synk_transaktion`, der gør det for dig.
    """
    if ny_cursor is not None and cursor_type not in CURSOR_TYPER:
        raise SynkFejl(f"cursor_type skal være en af {', '.join(CURSOR_TYPER)}")
    session.execute(
        update(SyncState)
        .where(SyncState.client_id == client_id, SyncState.ressource == ressource)
        .values(
            cursor=ny_cursor,
            cursor_type=cursor_type if ny_cursor is not None else None,
            sidste_ok=func.now(),
            status="ok",
            antal_fejl_i_traek=0,
            antal_hentet_sidst=antal_hentet,
            naeste_koersel=func.now() + interval,
        )
    )


def registrer_fejl(
    session: Session,
    client_id: int,
    ressource: str,
    fejl: BaseException | str,
    startet: datetime | None = None,
) -> SyncState:
    """Gem fejlbesked, tæl fejl op og udskyd næste kørsel. Rører ALDRIG bogmærket.

    Committer selv (fejlen skal gemmes, selv om kørslens data blev rullet tilbage).
    """
    tilstand = hent_tilstand(session, client_id, ressource)
    besked = fejl if isinstance(fejl, str) else f"{type(fejl).__name__}: {fejl}"
    antal = tilstand.antal_fejl_i_traek + 1
    tilstand.antal_fejl_i_traek = antal
    tilstand.sidste_fejl_tidspunkt = func.now()
    tilstand.sidste_fejl_besked = rediger(besked)[:2000]
    if startet is not None:
        tilstand.sidste_koersel_start = startet
    if tilstand.status != "deaktiveret":
        tilstand.status = "fejlet" if antal >= FEJL_GRAENSE else "forsinket"
    tilstand.naeste_koersel = func.now() + ventetid_efter_fejl(antal)
    session.commit()
    return tilstand


def nulstil_cursor(
    session: Session, client_id: int, ressource: str, staff_id: int | None = None
) -> None:
    """Slet bogmærket, så næste kørsel henter ALT forfra – og planlæg den med det samme."""
    tilstand = hent_tilstand(session, client_id, ressource)
    tilstand.cursor = None
    tilstand.cursor_type = None
    tilstand.naeste_koersel = func.now()
    session.add(AuditLog(
        client_id=client_id, staff_id=staff_id,
        handling="synk_cursor_nulstillet", detaljer={"ressource": ressource},
    ))
    session.commit()


def saet_deaktiveret(session: Session, client_id: int, ressource: str, deaktiveret: bool) -> None:
    """Slå synkronisering af en ressource fra (eller til igen) for en kunde."""
    tilstand = hent_tilstand(session, client_id, ressource)
    tilstand.status = "deaktiveret" if deaktiveret else "ok"
    if not deaktiveret:
        tilstand.antal_fejl_i_traek = 0
        tilstand.naeste_koersel = func.now()
    session.commit()


# --- Samlet kørsel ----------------------------------------------------------


class _Synk:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor = cursor.vaerdi
        self.cursor_type = cursor.type
        self._resultat: tuple | None = None

    def gennemfoert(
        self, ny_cursor: str | None, cursor_type: str | None = None, *, antal_hentet: int
    ) -> None:
        """Markér, at data er gemt. Bogmærket gemmes først, når transaktionen lukkes."""
        self._resultat = (ny_cursor, cursor_type, antal_hentet)


@contextmanager
def synk_transaktion(
    session: Session,
    client_id: int,
    ressource: str,
    interval: timedelta = STANDARD_INTERVAL,
) -> Iterator[_Synk]:
    """Kør én synkronisering: data og nyt bogmærke gemmes samlet – eller slet ikke.

    Kørslens data og bogmærke ligger i et savepoint (et delmærke i transaktionen).
    Fejler kørslen, rulles KUN den tilbage; fejlen registreres og gemmes. Ændringer,
    som den kaldende kode havde lavet forinden, gemmes med (de bliver ikke smidt væk).
    """
    _tjek_ressource(ressource)
    kunde_status = session.scalar(select(Client.status).where(Client.id == client_id))
    if kunde_status != "aktiv":
        raise KundeIkkeAktiv(f"Kunde {client_id} har status '{kunde_status}' – synkroniseres ikke")

    hent_tilstand(session, client_id, ressource)
    startet = session.scalar(select(func.now()))
    kørsel = session.begin_nested()
    # Lås rækken, så to kørsler for samme kunde og ressource ikke kan ske samtidig.
    laast = session.execute(
        select(SyncState.status, SyncState.cursor, SyncState.cursor_type)
        .where(SyncState.client_id == client_id, SyncState.ressource == ressource)
        .with_for_update(skip_locked=True)
    ).first()
    if laast is None or laast.status == "deaktiveret":
        kørsel.rollback()
        session.commit()
        if laast is None:
            raise SynkIGang(f"Kunde {client_id}/{ressource} synkroniseres allerede")
        raise SynkDeaktiveret(f"Synkronisering af {ressource} er slået fra for kunde {client_id}")
    session.execute(
        update(SyncState)
        .where(SyncState.client_id == client_id, SyncState.ressource == ressource)
        .values(sidste_koersel_start=startet)
    )

    synk = _Synk(Cursor(laast.cursor, laast.cursor_type))
    try:
        yield synk
        if synk._resultat is None:
            raise SynkFejl("Kørslen blev ikke markeret som gennemført – bogmærket er ikke flyttet")
        ny_cursor, cursor_type, antal = synk._resultat
        registrer_succes(session, client_id, ressource, ny_cursor, cursor_type, antal, interval)
        kørsel.commit()
    except BaseException as fejl:
        # Rul delmærket tilbage – også når en fejlet skrivning allerede har låst det.
        if session.get_nested_transaction() is kørsel:
            kørsel.rollback()  # hverken kørslens data eller bogmærke gemmes
        # "Vent og prøv igen" (fx rate limit) er ikke en fejl og tælles ikke med.
        if isinstance(fejl, Exception) and not getattr(fejl, "taeller_ikke_som_fejl", False):
            registrer_fejl(session, client_id, ressource, fejl, startet=startet)
        elif session.in_transaction():
            session.commit()
        raise
    try:
        session.commit()  # data + bogmærke gemmes endeligt i ÉN commit
    except Exception as fejl:
        session.rollback()  # selve gemningen fejlede: intet – heller ikke bogmærket – er gemt
        registrer_fejl(session, client_id, ressource, fejl, startet=startet)
        raise
