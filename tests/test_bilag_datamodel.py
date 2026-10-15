"""Bilagsindbakken, trin 1: tabellerne og databasens regler – samt opret-kommandoen."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditLog
from app.bilag import opret
from app.bilag.models import (
    InboxAddress,
    InboxDelivery,
    InboxDestination,
    InboxDocument,
    InboxHealth,
    InboxMessage,
    leveringsnoegle,
)
from app.kunder.models import Client

NU = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
HASH = "a" * 64


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Bilag ApS", kundenummer="BIL-1", regnskabssystem="economic", status="aktiv")
    db_session.add(k)
    db_session.flush()
    return k


@pytest.fixture
def besked(db_session, kunde):
    m = InboxMessage(client_id=kunde.id, graph_message_id="AAMk-1", received_at=NU, raw_object_key="raa/1.eml",
                     status="received", to_address="kunde-1@bilag.dinbogholder.dk")
    db_session.add(m)
    db_session.flush()
    return m


def _afvises(db_session, obj):
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(obj)
            db_session.flush()


def _dok(besked, **felter):
    std = dict(message_id=besked.id, client_id=besked.client_id, filename="faktura.pdf",
               content_type="application/pdf", size_bytes=1000, sha256=HASH, object_key="bilag/1.pdf",
               status="pending")
    return InboxDocument(**{**std, **felter})


def test_ukendt_alias_kan_gemmes_uden_kunde(db_session):
    m = InboxMessage(client_id=None, graph_message_id="AAMk-ukendt", received_at=NU, raw_object_key="raa/2.eml",
                     status="review", ignore_reason="ukendt_modtager")
    db_session.add(m)
    db_session.flush()
    assert m.id is not None


def test_samme_graph_besked_kun_en_gang(db_session, besked):
    _afvises(db_session, InboxMessage(client_id=besked.client_id, graph_message_id="AAMk-1", received_at=NU,
                                      raw_object_key="raa/1b.eml", status="received"))


def test_status_og_aarsager_er_faste_vaerdier(db_session, kunde, besked):
    _afvises(db_session, InboxMessage(client_id=kunde.id, graph_message_id="x1", received_at=NU,
                                      raw_object_key="r", status="smidt_ud"))
    _afvises(db_session, InboxMessage(client_id=kunde.id, graph_message_id="x2", received_at=NU,
                                      raw_object_key="r", status="review", ignore_reason="fordi"))
    _afvises(db_session, _dok(besked, doc_type="regning"))
    _afvises(db_session, InboxDestination(client_id=kunde.id, kind="ftp", address="a@b.dk"))


def test_dublettjek_samme_fil_samme_kunde(db_session, besked):
    original = _dok(besked)
    db_session.add(original)
    db_session.flush()
    _afvises(db_session, _dok(besked, object_key="bilag/2.pdf"))                 # samme fil, ikke markeret
    dublet = _dok(besked, object_key="bilag/2.pdf", status="duplicate", duplicate_of=original.id)
    db_session.add(dublet)
    db_session.flush()
    _afvises(db_session, _dok(besked, object_key="bilag/3.pdf", status="duplicate"))   # dublet uden original


def test_samme_fil_til_to_kunder_er_ikke_dublet(db_session, besked):
    anden = Client(navn="Anden ApS", kundenummer="BIL-2", regnskabssystem="economic", status="aktiv")
    db_session.add(anden)
    db_session.flush()
    m2 = InboxMessage(client_id=anden.id, graph_message_id="AAMk-2", received_at=NU, raw_object_key="r2",
                      status="received")
    db_session.add_all([_dok(besked), m2])
    db_session.flush()
    db_session.add(_dok(m2, object_key="bilag/x.pdf"))
    db_session.flush()


def test_beloeb_er_numeric_og_sikkerhed_mellem_0_og_1(db_session, besked):
    d = _dok(besked, amount=Decimal("1234.56"), confidence=Decimal("0.925"), currency="DKK")
    db_session.add(d)
    db_session.flush()
    db_session.refresh(d)
    assert d.amount == Decimal("1234.56") and isinstance(d.amount, Decimal)
    _afvises(db_session, _dok(besked, sha256="b" * 64, confidence=Decimal("1.5")))
    _afvises(db_session, _dok(besked, sha256="ikke-en-hash"))


def test_menneskelig_afgoerelse_kraever_navn_og_tid(db_session, besked):
    _afvises(db_session, _dok(besked, classifier="human"))


def test_levering_kun_en_gang_pr_dokument_og_destination(db_session, kunde, besked):
    d = _dok(besked)
    dest = InboxDestination(client_id=kunde.id, kind="economic", address="scan@kunde.dk")
    db_session.add_all([d, dest])
    db_session.flush()
    noegle = leveringsnoegle(d.id, dest.id)
    assert noegle == leveringsnoegle(d.id, dest.id) and len(noegle) == 64      # deterministisk
    db_session.add(InboxDelivery(document_id=d.id, destination_id=dest.id, idempotency_key=noegle, status="pending"))
    db_session.flush()
    _afvises(db_session, InboxDelivery(document_id=d.id, destination_id=dest.id, idempotency_key=noegle,
                                       status="pending"))
    _afvises(db_session, InboxDelivery(document_id=d.id, destination_id=dest.id, idempotency_key="x" * 64,
                                       status="sent"))                                   # sendt uden tidspunkt


def test_adresser_gemmes_med_smaa_bogstaver(db_session, kunde):
    _afvises(db_session, InboxAddress(client_id=kunde.id, address="Kunde-1@Bilag.dk"))


def test_hjerteslag_tilstande(db_session, kunde):
    db_session.add(InboxHealth(client_id=kunde.id))
    db_session.flush()
    assert db_session.get(InboxHealth, kunde.id).alert_state == "ok"
    _afvises(db_session, InboxHealth(client_id=kunde.id + 10_000, alert_state="panik"))


# --- Opret-kommandoen -------------------------------------------------------------------


def test_opret_alias_og_destination_kan_koeres_igen(db_session, kunde):
    a, ny = opret.opret_alias(db_session, kunde, "test")
    assert ny and a.address == f"kunde-{kunde.id}@bilag.dinbogholder.dk"
    assert opret.opret_alias(db_session, kunde, "test") == (a, False)
    d, ny = opret.opret_destination(db_session, kunde, " Scan@Kunde.DK ", "economic", "test")
    assert ny and d.address == "scan@kunde.dk"
    assert opret.opret_destination(db_session, kunde, "scan@kunde.dk", "economic", "test")[1] is False
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.handling.in_(("bilag_alias_oprettet", "bilag_destination_oprettet")))) == 2


@pytest.mark.parametrize("adresse, besked", [
    ("ikke-en-mail", "gyldig"),
    ("kunde-9@bilag.dinbogholder.dk", "løkke"),
])
def test_opret_destination_afviser(db_session, kunde, adresse, besked):
    with pytest.raises(opret.OpretFejl, match=besked):
        opret.opret_destination(db_session, kunde, adresse, "email", "test")
