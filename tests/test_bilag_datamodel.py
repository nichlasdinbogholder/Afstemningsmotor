"""Bilagsindbakken, trin 1 (v3): tabellerne og databasens regler – især godkendelsesgaten – og opret-kommandoen."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.audit.models import AuditLog
from app.bilag import opret
from app.bilag.models import (
    InboxAddress,
    InboxApproval,
    InboxDelivery,
    InboxDestination,
    InboxDocument,
    InboxHealth,
    InboxMessage,
    InboxPosting,
    SupplierAccountMap,
    leveringsnoegle,
)
from app.kunder.models import Client

NU = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)


def _hash(tegn: str) -> str:
    return tegn * 64


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


@pytest.fixture
def corpay(db_session, kunde):
    d = InboxDestination(client_id=kunde.id, kind="corpay", address="faktura@corpay.example")
    db_session.add(d)
    db_session.flush()
    return d


def _afvises(db_session, obj, fejl=IntegrityError, tekst=None):
    with pytest.raises(fejl, match=tekst):
        with db_session.begin_nested():
            db_session.add(obj)
            db_session.flush()


def _dok(besked, **felter):
    std = dict(message_id=besked.id, client_id=besked.client_id, source="email", filename="faktura.pdf",
               content_type="application/pdf", size_bytes=1000, sha256=_hash("a"), object_key="bilag/1.pdf",
               doc_type="faktura", status="pending_approval")
    return InboxDocument(**{**std, **felter})


def _gem(db_session, *objekter):
    db_session.add_all(objekter)
    db_session.flush()
    return objekter[0] if len(objekter) == 1 else objekter


def _godkend(db_session, d, beslutning="approved", sha=None, **felter):
    return _gem(db_session, InboxApproval(document_id=d.id, client_id=d.client_id, decision=beslutning,
                                          origin="app", decided_by="kunde@kunde.dk",
                                          document_sha256=sha or d.sha256, **felter))


def _levering(d, dest):
    return InboxDelivery(document_id=d.id, destination_id=dest.id, idempotency_key=leveringsnoegle(d.id, dest.id),
                         status="pending")


# --- Modtagelse og upload: én tabel -------------------------------------------------------


def test_ukendt_alias_kan_gemmes_uden_kunde(db_session):
    _gem(db_session, InboxMessage(client_id=None, graph_message_id="AAMk-ukendt", received_at=NU,
                                  raw_object_key="raa/2.eml", status="review", ignore_reason="ukendt_modtager"))


def test_samme_graph_besked_kun_en_gang(db_session, besked):
    _afvises(db_session, InboxMessage(client_id=besked.client_id, graph_message_id="AAMk-1", received_at=NU,
                                      raw_object_key="raa/1b.eml", status="received"))


def test_upload_uden_besked_men_med_bruger(db_session, kunde, besked):
    upload = _dok(besked, message_id=None, source="upload", uploaded_by="kunde@kunde.dk", sha256=_hash("b"),
                  status="extracting")
    _gem(db_session, upload)
    _afvises(db_session, _dok(besked, message_id=None, source="upload", sha256=_hash("c")))   # ingen bruger
    _afvises(db_session, _dok(besked, message_id=None, source="email", sha256=_hash("d")))    # mail uden besked


def test_status_og_typer_er_faste_vaerdier(db_session, kunde, besked):
    _afvises(db_session, InboxMessage(client_id=kunde.id, graph_message_id="x1", received_at=NU,
                                      raw_object_key="r", status="smidt_ud"))
    _afvises(db_session, _dok(besked, doc_type="regning"))
    _afvises(db_session, _dok(besked, category="tivoli"))
    _afvises(db_session, _dok(besked, status="sendt"))
    _afvises(db_session, InboxDestination(client_id=kunde.id, kind="ftp", address="a@b.dk"))


# --- Noten (restaurationsbilag) -----------------------------------------------------------


@pytest.mark.parametrize("kraevet, deltagere, anledning, komplet", [
    (True, None, None, False),
    (True, "Anne, Bo", "   ", False),
    (True, "Anne, Bo (Kunde A/S)", "Kundemøde om tilbud", True),
    (False, None, None, True),
])
def test_note_complete_beregnes_af_databasen(db_session, besked, kraevet, deltagere, anledning, komplet):
    d = _gem(db_session, _dok(besked, note_required=kraevet, participants=deltagere, occasion=anledning))
    db_session.refresh(d)
    assert d.note_complete is komplet


def test_note_complete_opdateres_naar_noten_tilfoejes(db_session, besked):
    d = _gem(db_session, _dok(besked, note_required=True))
    d.participants, d.occasion = "Anne", "Julefrokost"
    db_session.flush()
    db_session.refresh(d)
    assert d.note_complete is True


# --- Godkendelsesgaten (håndhæves af databasen) --------------------------------------------


@pytest.mark.parametrize("art", ["faktura", "kreditnota", "kontoudtog"])
def test_uden_godkendelse_ingen_levering_til_corpay(db_session, besked, corpay, art):
    d = _gem(db_session, _dok(besked, doc_type=art))
    _afvises(db_session, _levering(d, corpay), DBAPIError, "uden kundens godkendelse")


def test_afvist_faktura_kan_ikke_leveres(db_session, besked, corpay):
    d = _gem(db_session, _dok(besked))
    _godkend(db_session, d, "rejected", reason_code="ikke_vores")
    _afvises(db_session, _levering(d, corpay), DBAPIError, "uden kundens godkendelse")


def test_godkendt_faktura_kan_leveres_en_gang(db_session, besked, corpay):
    d = _gem(db_session, _dok(besked, status="approved"))
    _godkend(db_session, d)
    _gem(db_session, _levering(d, corpay))
    _afvises(db_session, _levering(d, corpay))                                     # samme nøgle → aldrig to gange


def test_rykker_leveres_uden_godkendelse(db_session, besked, corpay):
    d = _gem(db_session, _dok(besked, doc_type="rykker", status="auto_route"))
    _gem(db_session, _levering(d, corpay))


def test_godkendelse_af_en_anden_fil_taeller_ikke(db_session, besked, corpay):
    """Godkendelsen gælder præcis den fil, kunden så (document_sha256)."""
    d = _gem(db_session, _dok(besked))
    _godkend(db_session, d, sha=_hash("f"))
    _afvises(db_session, _levering(d, corpay), DBAPIError, "uden kundens godkendelse")


@pytest.mark.parametrize("art", ["kvittering", "andet"])
def test_kvittering_og_andet_sendes_aldrig_til_corpay(db_session, besked, corpay, art):
    d = _gem(db_session, _dok(besked, doc_type=art, status="auto_route"))
    _afvises(db_session, _levering(d, corpay), DBAPIError, "sendes ikke til Corpay")


def test_kassekladden_er_ingen_leveringsdestination(db_session, kunde, besked):
    kladde = _gem(db_session, InboxDestination(client_id=kunde.id, kind="economic_journal", journal_id="3"))
    d = _gem(db_session, _dok(besked, doc_type="rykker", status="auto_route"))
    _afvises(db_session, _levering(d, kladde), DBAPIError, "kun Corpay")


def test_godkendelser_er_uforanderlige_og_en_pr_dokument(db_session, besked):
    d = _gem(db_session, _dok(besked))
    g = _godkend(db_session, d)
    for sql in ("UPDATE inbox_approvals SET decision = 'rejected' WHERE id = :id",
                "DELETE FROM inbox_approvals WHERE id = :id"):
        with pytest.raises(DBAPIError, match="uforanderlig"):
            with db_session.begin_nested():
                db_session.execute(text(sql), {"id": g.id})
    _afvises(db_session, InboxApproval(document_id=d.id, client_id=d.client_id, decision="approved",
                                       origin="app", decided_by="anden@kunde.dk", document_sha256=d.sha256))


def test_afvisning_kraever_grund(db_session, besked):
    d = _gem(db_session, _dok(besked))
    _afvises(db_session, InboxApproval(document_id=d.id, client_id=d.client_id, decision="rejected",
                                       origin="app", decided_by="kunde@kunde.dk", document_sha256=d.sha256))


# --- Dubletter, kontering, kontoregler, hjerteslag ----------------------------------------


def test_dublettjek_samme_fil_samme_kunde(db_session, besked):
    original = _gem(db_session, _dok(besked))
    _afvises(db_session, _dok(besked, object_key="bilag/2.pdf"))
    _gem(db_session, _dok(besked, object_key="bilag/2.pdf", status="duplicate", duplicate_of=original.id))
    _afvises(db_session, _dok(besked, object_key="bilag/3.pdf", status="duplicate"))   # dublet uden original


def test_samme_fil_til_to_kunder_er_ikke_dublet(db_session, besked):
    anden = _gem(db_session, Client(navn="Anden ApS", kundenummer="BIL-2", regnskabssystem="economic",
                                    status="aktiv"))
    m2 = _gem(db_session, InboxMessage(client_id=anden.id, graph_message_id="AAMk-2", received_at=NU,
                                       raw_object_key="r2", status="received"))
    _gem(db_session, _dok(besked), _dok(m2, object_key="bilag/x.pdf"))


def test_beloeb_er_numeric(db_session, besked):
    d = _gem(db_session, _dok(besked, amount=Decimal("1234.56"), vat_amount=Decimal("246.91"),
                              confidence=Decimal("0.925"), currency="DKK"))
    db_session.refresh(d)
    assert (d.amount, d.vat_amount) == (Decimal("1234.56"), Decimal("246.91"))
    _afvises(db_session, _dok(besked, sha256=_hash("b"), confidence=Decimal("1.5")))
    _afvises(db_session, _dok(besked, sha256="ikke-en-hash"))
    _afvises(db_session, _dok(besked, sha256=_hash("c"), due_date=date(2026, 11, 1)))    # forfald uden kilde


def test_kontering_kun_en_gang_pr_dokument(db_session, kunde, besked):
    d = _gem(db_session, _dok(besked, doc_type="kvittering", status="auto_route"))
    k = dict(client_id=kunde.id, journal_id="3", voucher_text="Rema 1000 – kontorhold", amount=Decimal("89.95"),
             posting_date=date(2026, 10, 14), status="pending")
    _gem(db_session, InboxPosting(document_id=d.id, idempotency_key=leveringsnoegle(d.id, "kladde:3"), **k))
    _afvises(db_session, InboxPosting(document_id=d.id, idempotency_key="x" * 64, **k))
    d2 = _gem(db_session, _dok(besked, doc_type="kvittering", status="auto_route", sha256=_hash("e")))
    _afvises(db_session, InboxPosting(document_id=d2.id, idempotency_key="y" * 64, **{**k, "status": "created"}))


def test_kontoregel_en_pr_kunde_og_moenster(db_session, kunde):
    regel = dict(supplier_pattern="rema 1000", account="2800", vat_code="I25", source="manual")
    _gem(db_session, SupplierAccountMap(client_id=None, **regel), SupplierAccountMap(client_id=kunde.id, **regel))
    _afvises(db_session, SupplierAccountMap(client_id=None, **regel))           # fællesregel kun én gang
    _afvises(db_session, SupplierAccountMap(client_id=kunde.id, **regel))


def test_hoejst_en_aktiv_destination_af_hver_slags(db_session, kunde, corpay):
    _afvises(db_session, InboxDestination(client_id=kunde.id, kind="corpay", address="andet@corpay.example"))
    _afvises(db_session, InboxDestination(client_id=kunde.id, kind="corpay", address=None))
    _afvises(db_session, InboxDestination(client_id=kunde.id, kind="economic_journal", journal_id=None))
    _gem(db_session, InboxDestination(client_id=kunde.id, kind="corpay", address="gammel@corpay.example",
                                      active=False))


def test_hjerteslag_tilstande(db_session, kunde):
    _gem(db_session, InboxHealth(client_id=kunde.id))
    assert db_session.get(InboxHealth, kunde.id).alert_state == "ok"
    _afvises(db_session, InboxHealth(client_id=kunde.id + 10_000, alert_state="panik"))


def test_adresser_gemmes_med_smaa_bogstaver(db_session, kunde):
    _afvises(db_session, InboxAddress(client_id=kunde.id, address="Kunde-1@Bilag.dk"))


# --- Opret-kommandoen ---------------------------------------------------------------------


def test_opret_alias_corpay_og_kassekladde_kan_koeres_igen(db_session, kunde):
    a, ny = opret.opret_alias(db_session, kunde, "test")
    assert ny and a.address == f"kunde-{kunde.id}@bilag.dinbogholder.dk"
    assert opret.opret_alias(db_session, kunde, "test") == (a, False)
    c, ny = opret.opret_corpay(db_session, kunde, " Faktura@Corpay.example ", "test")
    assert ny and c.address == "faktura@corpay.example"
    assert opret.opret_corpay(db_session, kunde, "faktura@corpay.example", "test")[1] is False
    k, ny = opret.opret_kassekladde(db_session, kunde, "3", "test")
    assert ny and k.journal_id == "3"
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.handling.in_(("bilag_alias_oprettet", "bilag_destination_oprettet")))) == 3


@pytest.mark.parametrize("hvad, vaerdi, besked", [
    ("corpay", "ikke-en-mail", "gyldig"),
    ("corpay", "kunde-9@bilag.dinbogholder.dk", "løkke"),
    ("kassekladde", "Kladde 3", "tal"),
])
def test_opret_afviser(db_session, kunde, hvad, vaerdi, besked):
    funktion = opret.opret_corpay if hvad == "corpay" else opret.opret_kassekladde
    with pytest.raises(opret.OpretFejl, match=besked):
        funktion(db_session, kunde, vaerdi, "test")


def test_opret_skifter_ikke_stille_corpay_adresse(db_session, kunde):
    opret.opret_corpay(db_session, kunde, "a@corpay.example", "test")
    with pytest.raises(opret.OpretFejl, match="allerede"):
        opret.opret_corpay(db_session, kunde, "b@corpay.example", "test")
