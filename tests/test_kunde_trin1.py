"""Kundeoversigten, trin 1: de regler, databasen selv håndhæver (spec 26.10.2026, test 7, 8, 10, 13, 15, 16, 19,
31-34, 39 m.fl.). Mod testdatabasen – ingen efterligning af egen kode."""

from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.adaptere.adgang import AdgangMangler, hent_adgang
from app.crm.models import Note
from app.db import saet_aktoer
from app.kunder import kartotek
from app.kunder.models import Client, Contact, Credential
from app.kunder.tabeller import KundeLedger, KundeLog, KundeObligations, KundeOwnerHistory
from app.personale.models import Staff
from tests.hjaelp_token import laeg_token, token_admin

TOKEN = "trin1-hemmeligt-token-abcd"


@pytest.fixture
def folk(db_session):
    a = Staff(navn="Trin1 Admin", email="trin1-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    m = Staff(navn="Trin1 Ansvarlig", email="trin1-m@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    m2 = Staff(navn="Trin1 Ny", email="trin1-m2@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add_all([a, m, m2])
    db_session.flush()
    return a, m, m2


@pytest.fixture
def kunde(db_session, folk):
    k = Client(navn="Trin1 VVS ApS", kundenummer="TRIN1-1", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=folk[1].id, assistent_id=folk[2].id)
    db_session.add(k)
    db_session.flush()
    return k


def _afvist(db_session, sql, params=None, match=None):
    with pytest.raises(DBAPIError, match=match):
        with db_session.begin_nested():
            db_session.execute(text(sql), params or {})


# --- Tokens ---------------------------------------------------------------------------------------------------

def test_kun_administrator_kan_saette_et_token(db_session, kunde, folk):
    ledger = KundeLedger(client_id=kunde.id, system="economic", label="e-conomic")
    db_session.add(ledger)
    db_session.flush()
    raekke = {"k": kunde.id, "l": ledger.id, "t": "gAAAAA-ikke-et-rigtigt-token"}
    sql = ("INSERT INTO credentials (client_id, ledger_id, system, token_krypteret, status) "
           "VALUES (:k, :l, 'economic', :t, 'aktiv')")
    _afvist(db_session, sql, raekke, match="Kun en administrator")  # ingen aktør
    saet_aktoer(db_session, folk[1].id)                             # medarbejder
    _afvist(db_session, sql, raekke, match="Kun en administrator")


def test_gemt_token_kan_ikke_aendres_og_udskiftning_er_ny_raekke(db_session, kunde):
    c = laeg_token(db_session, kunde.id, "economic", TOKEN)
    _afvist(db_session, "UPDATE credentials SET token_krypteret = token_krypteret || 'x' WHERE id = :id",
            {"id": c.id}, match="kan ikke ændres")
    ny = laeg_token(db_session, kunde.id, "economic", TOKEN + "-nyt")
    db_session.refresh(c)
    assert (c.status, c.revoked_at is not None) == ("tilbagekaldt", True)
    assert ny.ledger_id == c.ledger_id and ny.revoked_at is None and ny.fingerprint == "-nyt"
    assert ny.set_by == token_admin(db_session).id                    # sat af triggeren
    # Et tilbagekaldt token kan ikke genoplives, og tokens slettes ikke.
    _afvist(db_session, "UPDATE credentials SET revoked_at = NULL, status = 'aktiv' WHERE id = :id",
            {"id": c.id}, match="tilbagekaldt")
    _afvist(db_session, "DELETE FROM credentials WHERE id = :id", {"id": c.id}, match="slettes ikke")
    # Kun ét gældende token pr. aftale – også i databasen.
    assert hent_adgang(db_session, kunde.id, "economic").token.klartekst() == TOKEN + "-nyt"


def test_tokenet_staar_aldrig_i_kundens_log(db_session, kunde):
    laeg_token(db_session, kunde.id, "economic", TOKEN)
    laeg_token(db_session, kunde.id, "economic", TOKEN + "-nyt")
    log = db_session.scalars(select(KundeLog).where(KundeLog.client_id == kunde.id).order_by(KundeLog.id)).all()
    assert [r.event for r in log] == ["token_indsat", "token_tilbagekaldt", "token_indsat"]
    assert log[0].detail == "e-conomic · ••••abcd"
    alt = " ".join((r.detail or "") for r in log)
    assert TOKEN not in alt and "hemmeligt" not in alt


def test_en_kunde_kan_have_to_aftaler_men_adapteren_gaetter_ikke(db_session, kunde):
    laeg_token(db_session, kunde.id, "economic", TOKEN)
    anden = KundeLedger(client_id=kunde.id, system="economic", label="Datterselskab")
    db_session.add(anden)
    db_session.flush()
    saet_aktoer(db_session, token_admin(db_session).id)
    c = Credential(client_id=kunde.id, ledger_id=anden.id, system="economic", status="aktiv")
    c.saet_token(TOKEN + "-2")
    db_session.add(c)
    db_session.flush()
    aktive = db_session.scalars(select(Credential).where(Credential.client_id == kunde.id,
                                                         Credential.revoked_at.is_(None))).all()
    assert len({a.ledger_id for a in aktive}) == 2
    with pytest.raises(AdgangMangler, match="flere aktive aftaler"):
        hent_adgang(db_session, kunde.id, "economic")


# --- Livscyklus og ejerskab -----------------------------------------------------------------------------------

def test_kunder_kan_ikke_slettes(db_session, kunde):
    _afvist(db_session, "DELETE FROM clients WHERE id = :id", {"id": kunde.id}, match="slettes aldrig")


def test_ophoert_kunde_genaabnes_kun_bevidst(db_session, kunde, folk):
    kartotek.arkiver(db_session, folk[0], kunde.id, "engangsopgave", date.today())
    _afvist(db_session, "UPDATE clients SET status = 'aktiv' WHERE id = :id", {"id": kunde.id},
            match="genåbnes bevidst")
    assert kartotek.genaktiver(db_session, folk[0], kunde.id).status == "aktiv"


def test_skift_af_ansvarlig_skriver_historik_uden_overlap(db_session, kunde, folk):
    _, m, m2 = folk
    historik = db_session.scalars(select(KundeOwnerHistory).where(KundeOwnerHistory.client_id == kunde.id)).all()
    assert [(h.owner_staff_id, h.valid_to) for h in historik] == [(m.id, None)]
    # Første række er fra i dag – i går flyttes den, så skiftet i dag kan lukke den.
    db_session.execute(text("UPDATE kunde_owner_history SET valid_from = valid_from - 30 WHERE client_id = :k"),
                       {"k": kunde.id})
    kartotek.saet_ansvarlig(db_session, folk[0], kunde.id, m2.id)
    db_session.expire_all()
    historik = db_session.scalars(select(KundeOwnerHistory).where(KundeOwnerHistory.client_id == kunde.id)
                                  .order_by(KundeOwnerHistory.id)).all()
    assert [(h.owner_staff_id, h.valid_to is None) for h in historik] == [(m.id, False), (m2.id, True)]
    assert historik[0].valid_to == historik[1].valid_from and historik[1].changed_by == folk[0].id
    # Samme dag igen: dagens række rettes – ingen tom periode og intet overlap.
    kartotek.saet_ansvarlig(db_session, folk[0], kunde.id, m.id)
    db_session.expire_all()
    aabne = db_session.scalars(select(KundeOwnerHistory).where(KundeOwnerHistory.client_id == kunde.id,
                                                               KundeOwnerHistory.valid_to.is_(None))).all()
    assert [h.owner_staff_id for h in aabne] == [m.id]
    # Databasen afviser overlappende perioder.
    _afvist(db_session, "INSERT INTO kunde_owner_history (client_id, owner_staff_id, valid_from) "
                        "VALUES (:k, :m, '2000-01-01')", {"k": kunde.id, "m": m2.id}, match="overlap")
    log = db_session.scalars(select(KundeLog.detail).where(KundeLog.client_id == kunde.id,
                                                           KundeLog.event == "ansvarlig_skiftet")).all()
    assert log == ["Trin1 Ansvarlig → Trin1 Ny", "Trin1 Ny → Trin1 Ansvarlig"]


def test_emne_er_ikke_i_kundeoversigten(db_session, kunde):
    emne = Client(navn="Trin1 Emne ApS", kundenummer="TRIN1-E", regnskabssystem="economic", status="emne")
    db_session.add(emne)
    db_session.flush()
    ider = [k["id"] for k in kartotek.liste(db_session, False, date.today())]
    assert kunde.id in ider and emne.id not in ider


def test_assistent_er_det_tidligere_felt_daglig_medarbejder(db_session, kunde, folk):
    from app.kunder.roller import arbejder_paa, er_ansvarlig, er_assistent

    assert er_ansvarlig(kunde, folk[1]) and er_assistent(kunde, folk[2])
    assert arbejder_paa(kunde, folk[2]) and not arbejder_paa(kunde, folk[0])


# --- Log, noter og kontaktpersoner ----------------------------------------------------------------------------

def test_loggen_kan_kun_tilfoejes_til(db_session, kunde):
    r = KundeLog(client_id=kunde.id, event="ringet", detail="Ringet om bilag")
    db_session.add(r)
    db_session.flush()
    assert r.actor_staff_id is None                                   # systemet / ingen aktør
    _afvist(db_session, "UPDATE kunde_log SET detail = 'rettet' WHERE id = :id", {"id": r.id}, match="kan ikke")
    _afvist(db_session, "DELETE FROM kunde_log WHERE id = :id", {"id": r.id}, match="kan ikke")
    _afvist(db_session, "TRUNCATE kunde_log", match="kan ikke")
    # En note kan derimod rettes.
    n = Note(client_id=kunde.id, tekst="Første udgave")
    db_session.add(n)
    db_session.flush()
    n.tekst = "Rettet"
    db_session.flush()


def test_en_primaer_kontakt_blandt_dem_der_ikke_er_arkiveret(db_session, kunde):
    gammel = Contact(client_id=kunde.id, navn="Gammel", primaer=True)
    db_session.add(gammel)
    db_session.flush()
    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(Contact(client_id=kunde.id, navn="Ny", primaer=True))
            db_session.flush()
    db_session.execute(text("UPDATE contacts SET arkiveret = now() WHERE id = :id"), {"id": gammel.id})
    db_session.add(Contact(client_id=kunde.id, navn="Ny", primaer=True, underskriver=True,
                           maa_kontaktes_af="ansvarlig"))
    db_session.flush()
    _afvist(db_session, "UPDATE contacts SET maa_kontaktes_af = 'nogen' WHERE id = :id", {"id": gammel.id})


def test_fristparametre_ligger_i_kunde_obligations(db_session, kunde):
    db_session.add(KundeObligations(client_id=kunde.id, vat_period="ingen", fiscal_year_end="30-06", payroll_day=25))
    db_session.flush()
    _afvist(db_session, "UPDATE kunde_obligations SET fiscal_year_end = '31-13' WHERE client_id = :k",
            {"k": kunde.id})
    assert not hasattr(Client, "momsperiode")
