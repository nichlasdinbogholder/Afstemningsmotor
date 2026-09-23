"""Beviser, at synkroniseringstilstanden er sikker.

1. Bogmærket (cursor) rykkes ikke frem, hvis skrivningen til databasen fejler.
2. Fejl hos én kunde påvirker ikke de andres tilstand.
3. Kombinationen client_id + ressource kan ikke oprettes to gange.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.kunder.models import Client
from app.regnskab.models import Account
from app.synk.models import SyncState
from app.synk.tilstand import (
    FEJL_GRAENSE,
    KundeIkkeAktiv,
    SynkDeaktiveret,
    SynkFejl,
    hent_cursor,
    hent_tilstand,
    klar_til_koersel,
    kraever_handling,
    nulstil_cursor,
    registrer_fejl,
    saet_deaktiveret,
    synk_transaktion,
    ventetid_efter_fejl,
)


def _kunde(session, nr, status="aktiv"):
    k = Client(navn=f"Synk {nr}", kundenummer=nr, regnskabssystem="economic", status=status)
    session.add(k)
    session.commit()  # synk_transaktion kræver en session uden ugemte ændringer
    return k


def _konto(kunde, nr, kontotype="status"):
    return Account(
        tenant_id=kunde.id, system="economic", kontonummer=nr, navn=f"Konto {nr}",
        kontotype=kontotype, raa_data={},
    )


def _tilstand(session, kunde, ressource="entries") -> SyncState:
    return hent_tilstand(session, kunde.id, ressource)


def _synk_ok(session, kunde, cursor, antal=1, ressource="entries"):
    with synk_transaktion(session, kunde.id, ressource) as synk:
        session.add(_konto(kunde, 1000 + antal))
        synk.gennemfoert(cursor, "dato", antal_hentet=antal)


# --- 1. Bogmærket rykkes ikke frem, hvis skrivningen fejler -----------------


def test_cursor_rykkes_ikke_hvis_skrivning_til_databasen_fejler(db_session):
    kunde = _kunde(db_session, "S-1")
    _synk_ok(db_session, kunde, "2026-09-01")

    with pytest.raises(IntegrityError):
        with synk_transaktion(db_session, kunde.id, "entries") as synk:
            assert synk.cursor == "2026-09-01"
            db_session.add(_konto(kunde, 2000))           # gyldig række …
            synk.gennemfoert("2026-09-23", "dato", antal_hentet=2)
            db_session.add(_konto(kunde, 2001, kontotype="ugyldig"))  # … men denne afvises
            db_session.flush()

    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "2026-09-01"
    # Heller ikke den gyldige række fra den fejlede kørsel er gemt.
    assert db_session.scalar(select(Account.id).where(Account.kontonummer == 2000)) is None
    t = _tilstand(db_session, kunde)
    assert (t.status, t.antal_fejl_i_traek) == ("forsinket", 1)
    assert "IntegrityError" in t.sidste_fejl_besked


def test_cursor_rykkes_ikke_hvis_kørslen_fejler_efter_data_er_skrevet(db_session):
    kunde = _kunde(db_session, "S-2")
    _synk_ok(db_session, kunde, "2026-09-01")

    with pytest.raises(RuntimeError):
        with synk_transaktion(db_session, kunde.id, "entries") as synk:
            db_session.add(_konto(kunde, 3000))
            db_session.flush()  # data ER skrevet til databasen – men ikke gemt endeligt
            synk.gennemfoert("2026-09-23", "dato", antal_hentet=1)
            raise RuntimeError("forbindelsen døde lige før afslutning")

    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "2026-09-01"
    assert db_session.scalar(select(Account.id).where(Account.kontonummer == 3000)) is None


def test_cursor_rykkes_ikke_hvis_koerslen_glemmer_at_melde_gennemfoert(db_session):
    kunde = _kunde(db_session, "S-3")
    _synk_ok(db_session, kunde, "2026-09-01")
    with pytest.raises(SynkFejl, match="ikke markeret som gennemført"):
        with synk_transaktion(db_session, kunde.id, "entries"):
            db_session.add(_konto(kunde, 4000))
    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "2026-09-01"
    assert db_session.scalar(select(Account.id).where(Account.kontonummer == 4000)) is None


def test_succes_gemmer_data_og_cursor_samlet_og_nulstiller_fejl(db_session):
    kunde = _kunde(db_session, "S-4")
    registrer_fejl(db_session, kunde.id, "entries", "midlertidig fejl")
    _synk_ok(db_session, kunde, "2026-09-23", antal=7)

    t = _tilstand(db_session, kunde)
    assert (t.cursor, t.cursor_type, t.status, t.antal_fejl_i_traek, t.antal_hentet_sidst) == (
        "2026-09-23", "dato", "ok", 0, 7)
    assert t.sidste_ok is not None
    nu = db_session.scalar(select(func.now()))
    assert timedelta(hours=23) < t.naeste_koersel - nu <= timedelta(hours=24)
    assert db_session.scalar(select(func.count()).select_from(Account)
                             .where(Account.tenant_id == kunde.id)) == 1


# --- 2. Fejl hos én kunde påvirker ikke de andre ----------------------------


def test_fejl_hos_en_kunde_paavirker_ikke_andres_tilstand(db_session):
    a, b, c = (_kunde(db_session, nr) for nr in ("A-1", "B-1", "C-1"))
    for k in (a, b, c):
        _synk_ok(db_session, k, "2026-09-01")

    # A fejler, B lykkes, C fejler – blandet rækkefølge i samme session.
    with pytest.raises(RuntimeError):
        with synk_transaktion(db_session, a.id, "entries") as synk:
            db_session.add(_konto(a, 5000))
            raise RuntimeError("e-conomic svarede 500")
    with synk_transaktion(db_session, b.id, "entries") as synk:
        db_session.add(_konto(b, 5000))
        synk.gennemfoert("2026-09-23", "dato", antal_hentet=1)
    with pytest.raises(RuntimeError):
        with synk_transaktion(db_session, c.id, "entries"):
            raise RuntimeError("timeout")

    ta, tb, tc = (_tilstand(db_session, k) for k in (a, b, c))
    assert (ta.status, ta.antal_fejl_i_traek, ta.cursor) == ("forsinket", 1, "2026-09-01")
    assert (tb.status, tb.antal_fejl_i_traek, tb.cursor) == ("ok", 0, "2026-09-23")
    assert tb.sidste_fejl_besked is None
    assert (tc.status, tc.antal_fejl_i_traek, tc.cursor) == ("forsinket", 1, "2026-09-01")
    assert db_session.scalar(select(Account.tenant_id).where(Account.kontonummer == 5000)) == b.id


def test_samme_kunde_andre_ressourcer_paavirkes_ikke(db_session):
    k = _kunde(db_session, "R-1")
    _synk_ok(db_session, k, "2026-09-01", ressource="invoices")
    registrer_fejl(db_session, k.id, "entries", "fejl")
    assert _tilstand(db_session, k, "invoices").status == "ok"
    assert _tilstand(db_session, k, "entries").status == "forsinket"


# --- 3. client_id + ressource kun én gang -----------------------------------


def test_kunde_og_ressource_kan_ikke_oprettes_to_gange(db_session):
    k = _kunde(db_session, "U-1")
    db_session.add(SyncState(client_id=k.id, ressource="entries"))
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(SyncState(client_id=k.id, ressource="entries"))
            db_session.flush()


def test_hent_tilstand_opretter_kun_en_raekke(db_session):
    k = _kunde(db_session, "U-2")
    foerste = hent_tilstand(db_session, k.id, "entries")
    anden = hent_tilstand(db_session, k.id, "entries")
    assert foerste.id == anden.id
    assert db_session.scalar(select(func.count()).select_from(SyncState)
                             .where(SyncState.client_id == k.id)) == 1


# --- Fejl i træk, ventetid og oversigt --------------------------------------


def test_fejl_i_traek_giver_stigende_ventetid_og_til_sidst_fejlet(db_session):
    k = _kunde(db_session, "F-1")
    forrige = timedelta(0)
    for n in range(1, FEJL_GRAENSE + 1):
        t = registrer_fejl(db_session, k.id, "entries", RuntimeError(f"fejl {n}"))
        nu = db_session.scalar(select(func.now()))
        ventetid = t.naeste_koersel - nu
        assert ventetid > forrige
        forrige = ventetid
        assert t.status == ("fejlet" if n >= FEJL_GRAENSE else "forsinket")

    handling = {(r["kundenummer"], r["ressource"]): r for r in kraever_handling(db_session)}
    assert ("F-1", "entries") in handling
    assert "kræver handling" in handling[("F-1", "entries")]["aarsag"]


def test_ventetid_fordobles_med_loft():
    assert [ventetid_efter_fejl(n) for n in (1, 2, 3)] == [
        timedelta(minutes=5), timedelta(minutes=10), timedelta(minutes=20)]
    assert ventetid_efter_fejl(50) == timedelta(hours=24)


def test_oversigt_viser_ikke_kunder_der_er_ok(db_session):
    k = _kunde(db_session, "O-1")
    _synk_ok(db_session, k, "2026-09-23")
    assert ("O-1", "entries") not in {(r["kundenummer"], r["ressource"])
                                      for r in kraever_handling(db_session)}


def test_oversigt_viser_kunde_der_ikke_er_hentet_i_48_timer(db_session):
    k = _kunde(db_session, "O-2")
    _synk_ok(db_session, k, "2026-09-01")
    db_session.execute(text(
        "UPDATE sync_state SET sidste_ok = now() - interval '3 days' WHERE client_id = :k"
    ), {"k": k.id})
    raekker = [r for r in kraever_handling(db_session) if r["kundenummer"] == "O-2"]
    assert raekker and raekker[0]["aarsag"] == "Ikke hentet i over 48 timer"


# --- Opsagt og pause kommer aldrig med --------------------------------------


@pytest.mark.parametrize("status", ["opsagt", "pause"])
def test_opsagt_og_pause_koeres_aldrig(db_session, status):
    k = _kunde(db_session, f"P-{status}", status=status)
    hent_tilstand(db_session, k.id, "entries")  # tilstand findes og er "klar"

    assert (k.id, "entries") not in klar_til_koersel(db_session)
    with pytest.raises(KundeIkkeAktiv):
        with synk_transaktion(db_session, k.id, "entries"):
            pytest.fail("kørslen må aldrig starte")
    assert all(r["kundenummer"] != k.kundenummer for r in kraever_handling(db_session))


def test_aktiv_kunde_er_klar_til_koersel(db_session):
    k = _kunde(db_session, "K-1")
    hent_tilstand(db_session, k.id, "entries")
    assert (k.id, "entries") in klar_til_koersel(db_session, "entries")


def test_deaktiveret_ressource_koeres_ikke(db_session):
    k = _kunde(db_session, "D-1")
    saet_deaktiveret(db_session, k.id, "entries", True)
    assert (k.id, "entries") not in klar_til_koersel(db_session)
    with pytest.raises(SynkDeaktiveret):
        with synk_transaktion(db_session, k.id, "entries"):
            pytest.fail("kørslen må aldrig starte")


# --- Nulstil ----------------------------------------------------------------


def test_nulstil_cursor_tvinger_fuld_genindlaesning(db_session):
    k = _kunde(db_session, "N-1")
    _synk_ok(db_session, k, "2026-09-23")
    db_session.execute(text(
        "UPDATE sync_state SET naeste_koersel = now() + interval '1 day' WHERE client_id = :k"
    ), {"k": k.id})

    nulstil_cursor(db_session, k.id, "entries")

    assert hent_cursor(db_session, k.id, "entries").vaerdi is None
    assert (k.id, "entries") in klar_til_koersel(db_session)
    with synk_transaktion(db_session, k.id, "entries") as synk:
        assert synk.cursor is None  # næste kørsel starter forfra
        synk.gennemfoert("2026-09-23", "dato", antal_hentet=0)


def test_ukendt_ressource_afvises(db_session):
    k = _kunde(db_session, "X-1")
    with pytest.raises(SynkFejl, match="Ukendt ressource"):
        hent_tilstand(db_session, k.id, "kaffe")


def test_fejlbesked_indeholder_ikke_tokens(db_session, token):
    from app.sikkerhed.hemmeligheder import HemmeligtToken

    HemmeligtToken(token)  # registrerer tokenet som hemmeligt
    k = _kunde(db_session, "T-1")
    t = registrer_fejl(db_session, k.id, "entries", RuntimeError(f"afvist nøgle {token}"))
    assert token not in t.sidste_fejl_besked


def test_fejlet_koersel_smider_ikke_kalderens_egne_aendringer_vaek(db_session):
    k = _kunde(db_session, "G-1")
    _synk_ok(db_session, k, "2026-09-01")
    k.navn = "Nyt navn gemt før kørslen"
    db_session.flush()
    with pytest.raises(RuntimeError):
        with synk_transaktion(db_session, k.id, "entries") as synk:
            db_session.add(_konto(k, 6000))
            synk.gennemfoert("2026-09-23", "dato", antal_hentet=1)
            raise RuntimeError("fejl")
    db_session.expire_all()
    assert db_session.get(Client, k.id).navn == "Nyt navn gemt før kørslen"
    assert hent_cursor(db_session, k.id, "entries").vaerdi == "2026-09-01"
    assert db_session.scalar(select(Account.id).where(Account.kontonummer == 6000)) is None


def test_cursor_rykkes_ikke_hvis_selve_commit_fejler(db_session, monkeypatch):
    k = _kunde(db_session, "C-9")
    _synk_ok(db_session, k, "2026-09-01")
    rigtig_commit = db_session.commit
    kald = {"n": 0}

    def fejlende_commit():
        kald["n"] += 1
        if kald["n"] == 1:
            raise OSError("databasen forsvandt under gemning")
        rigtig_commit()

    monkeypatch.setattr(db_session, "commit", fejlende_commit)
    with pytest.raises(OSError):
        with synk_transaktion(db_session, k.id, "entries") as synk:
            db_session.add(_konto(k, 7000))
            synk.gennemfoert("2026-09-23", "dato", antal_hentet=1)
    monkeypatch.undo()
    assert hent_cursor(db_session, k.id, "entries").vaerdi == "2026-09-01"
    assert db_session.scalar(select(Account.id).where(Account.kontonummer == 7000)) is None
    assert _tilstand(db_session, k).antal_fejl_i_traek == 1
