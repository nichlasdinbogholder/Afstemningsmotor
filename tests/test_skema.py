"""Kontrollerer databasens opbygning mod de faste krav.

- Alle fremmednøgler er rigtige relationer og har et indeks.
- Alle tabeller med client_id har indeks på den.
- Statusfelter er låst til faste værdier (ikke fri tekst).
- audit_log kan kun tilføjes til, clients.opdateret opdateres automatisk.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.crm.models import Handover, Task
from app.kunder.models import Client, Contact
from app.personale.models import Staff

TABELLER = {
    "staff", "clients", "credentials", "contacts", "tasks", "time_entries",
    "notes", "documents", "handovers", "audit_log",
}


@pytest.fixture
def inspektor(db_session):
    return inspect(db_session.connection())


def _indekserede_kolonner(inspektor, tabel):
    """Kolonner, der står først i et indeks eller en unik regel (kan slås op hurtigt)."""
    forreste = {i["column_names"][0] for i in inspektor.get_indexes(tabel)}
    forreste |= {u["column_names"][0] for u in inspektor.get_unique_constraints(tabel)}
    return forreste


def test_alle_tabeller_findes(inspektor):
    assert TABELLER <= set(inspektor.get_table_names())


def test_alle_fremmednoegler_har_indeks(inspektor):
    mangler = [
        f"{tabel}.{fk['constrained_columns'][0]}"
        for tabel in TABELLER
        for fk in inspektor.get_foreign_keys(tabel)
        if fk["constrained_columns"][0] not in _indekserede_kolonner(inspektor, tabel)
    ]
    assert not mangler, f"Fremmednøgler uden indeks: {mangler}"


def test_alle_client_id_er_relationer_med_indeks(inspektor):
    for tabel in TABELLER - {"clients"}:
        kolonner = {k["name"] for k in inspektor.get_columns(tabel)}
        if "client_id" not in kolonner:
            continue
        fks = [f for f in inspektor.get_foreign_keys(tabel) if f["constrained_columns"] == ["client_id"]]
        assert fks and fks[0]["referred_table"] == "clients", tabel
        assert "client_id" in _indekserede_kolonner(inspektor, tabel), tabel


def test_alle_staff_kolonner_peger_paa_staff(inspektor):
    forventet = {
        "clients": {"ansvarlig_medarbejder_id", "daglig_medarbejder_id"},
        "tasks": {"ansvarlig_id"},
        "time_entries": {"staff_id"},
        "notes": {"staff_id"},
        "documents": {"uploadet_af"},
        "handovers": {"fra_staff_id", "til_staff_id", "kvitteret_af_staff_id"},
        "audit_log": {"staff_id"},
    }
    for tabel, kolonner in forventet.items():
        til_staff = {
            f["constrained_columns"][0]
            for f in inspektor.get_foreign_keys(tabel)
            if f["referred_table"] == "staff"
        }
        assert kolonner <= til_staff, tabel


@pytest.mark.parametrize(
    "tabel, kolonne",
    [
        ("staff", "rolle"),
        ("clients", "status"),
        ("clients", "regnskabssystem"),
        ("clients", "opgave_frekvens"),
        ("clients", "momsperiode"),
        ("clients", "aftaletype"),
        ("credentials", "system"),
        ("credentials", "status"),
        ("tasks", "status"),
        ("tasks", "oprettet_af"),
        ("handovers", "aftalt_ugedag"),
    ],
)
def test_statusfelter_er_laast_til_faste_vaerdier(inspektor, tabel, kolonne):
    regler = [c["sqltext"] for c in inspektor.get_check_constraints(tabel)]
    assert any(kolonne in r for r in regler), f"{tabel}.{kolonne} har ingen fast værdiliste"


def _opret_grunddata(session):
    medarbejder = Staff(navn="Test Testesen", email="test@example.invalid")
    kollega = Staff(navn="Kollega Hansen", email="kollega@example.invalid")
    kunde = Client(navn="Skema ApS", kundenummer="SKEMA-1", regnskabssystem="economic")
    session.add_all([medarbejder, kollega, kunde])
    session.flush()
    return medarbejder, kollega, kunde


def test_ugyldig_status_afvises(db_session):
    _, _, kunde = _opret_grunddata(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Task(client_id=kunde.id, titel="Moms", status="glemt"))
            db_session.flush()


def test_kun_en_primaer_kontakt_pr_kunde(db_session):
    _, _, kunde = _opret_grunddata(db_session)
    db_session.add(Contact(client_id=kunde.id, navn="A", primaer=True))
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Contact(client_id=kunde.id, navn="B", primaer=True))
            db_session.flush()


def test_overlevering_kraever_gyldig_periode(db_session):
    from datetime import date

    fra, til, kunde = _opret_grunddata(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Handover(
                client_id=kunde.id, fra_staff_id=fra.id, til_staff_id=til.id,
                periode_start=date(2026, 7, 20), periode_slut=date(2026, 7, 1),
            ))
            db_session.flush()


def test_audit_log_kan_ikke_aendres_eller_slettes(db_session):
    _, _, kunde = _opret_grunddata(db_session)
    db_session.execute(
        text("INSERT INTO audit_log (client_id, handling) VALUES (:c, 'test')"), {"c": kunde.id}
    )
    for sql in ("UPDATE audit_log SET handling = 'snyd'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="kan ikke ændres eller slettes"):
            with db_session.begin_nested():
                db_session.execute(text(sql))


def test_opdateret_saettes_automatisk(db_session):
    _, _, kunde = _opret_grunddata(db_session)
    foer = db_session.execute(
        text("SELECT opdateret FROM clients WHERE id = :id"), {"id": kunde.id}
    ).scalar_one()
    # now() er fast inden for en transaktion, så sæt en gammel værdi og se den blive overskrevet.
    db_session.execute(
        text("UPDATE clients SET opdateret = '2000-01-01', navn = 'Nyt navn' WHERE id = :id"),
        {"id": kunde.id},
    )
    efter = db_session.execute(
        text("SELECT opdateret FROM clients WHERE id = :id"), {"id": kunde.id}
    ).scalar_one()
    assert efter == foer
