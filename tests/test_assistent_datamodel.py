"""Fagassistenten, trin 1: tabellerne og databasens regler (faste værdier, satser, feedback, dansk søgning)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.assistent.models import (
    AssistantConversation,
    AssistantFeedback,
    AssistantMessage,
    KbDocument,
    KbRateCheck,
)
from app.kunder.models import Client
from app.personale.models import Staff


@pytest.fixture
def medarbejder(db_session):
    m = Staff(navn="Elev", email="elev@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def besked(db_session, medarbejder):
    samtale = AssistantConversation(staff_id=medarbejder.id, title="Arbejdstøj")
    db_session.add(samtale)
    db_session.flush()
    b = AssistantMessage(conversation_id=samtale.id, role="assistant", content="Svar")
    db_session.add(b)
    db_session.flush()
    return b


def _fejler(db_session, objekt):
    db_session.add(objekt)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.flush()


def test_samtale_kan_have_en_kunde_eller_vaere_generel(db_session, medarbejder):
    kunde = Client(navn="Tømrer ApS", kundenummer="ASS-1", regnskabssystem="economic", status="aktiv")
    db_session.add(kunde)
    db_session.flush()
    generel = AssistantConversation(staff_id=medarbejder.id)
    med_kunde = AssistantConversation(staff_id=medarbejder.id, client_id=kunde.id)
    db_session.add_all([generel, med_kunde])
    db_session.flush()
    assert generel.client_id is None and med_kunde.client_id == kunde.id


def test_besked_status_er_som_standard_complete(db_session, besked):
    db_session.refresh(besked)
    assert besked.status == "complete"


def test_ugyldig_rolle_afvises(db_session, besked):
    _fejler(db_session, AssistantMessage(conversation_id=besked.conversation_id, role="system", content="x"))


def test_ugyldig_status_afvises(db_session, besked):
    _fejler(db_session, AssistantMessage(conversation_id=besked.conversation_id, role="user", content="x",
                                         status="halv"))


def test_feedback_kun_en_gang_pr_medarbejder_pr_svar(db_session, besked, medarbejder):
    db_session.add(AssistantFeedback(message_id=besked.id, staff_id=medarbejder.id, rating="up"))
    db_session.flush()
    _fejler(db_session, AssistantFeedback(message_id=besked.id, staff_id=medarbejder.id, rating="down"))


def test_ugyldig_vurdering_afvises(db_session, besked, medarbejder):
    _fejler(db_session, AssistantFeedback(message_id=besked.id, staff_id=medarbejder.id, rating="meh"))


def test_kategori_er_laast(db_session):
    _fejler(db_session, KbDocument(title="x", category="jura", body="x"))


def test_sats_uden_vaerdi_og_enhed_afvises(db_session):
    _fejler(db_session, KbDocument(title="Kilometersats", category="satser", body="Sats for kørsel"))


def test_sats_med_vaerdi_og_enhed_er_aktiv_som_standard(db_session):
    # Opdigtet tal – testen handler om reglen, ikke om den rigtige sats.
    ok = KbDocument(title="Kilometersats", category="satser", body="Sats for kørsel",
                    value=Decimal("1.0000"), unit="kr/km")
    db_session.add(ok)
    db_session.flush()
    db_session.refresh(ok)
    assert ok.status == "active"


def test_gyldig_periode_skal_vende_rigtigt(db_session):
    _fejler(db_session, KbDocument(title="x", category="moms", body="x",
                                   valid_from=date(2026, 2, 1), valid_to=date(2026, 1, 1)))


def test_satskontrol_udfald_er_laast(db_session):
    dok = KbDocument(title="Sats", category="satser", body="x", value=Decimal("1"), unit="kr")
    db_session.add(dok)
    db_session.flush()
    _fejler(db_session, KbRateCheck(kb_id=dok.id, outcome="maaske"))


def test_soegning_finder_boejede_danske_ord(db_session):
    dok = KbDocument(title="Arbejdstøj og sikkerhedsudstyr",
                     body="Fradrag for arbejdstøjet afhænger af, om tøjet er egnet til privat brug.",
                     category="skat")
    db_session.add(dok)
    db_session.flush()
    fundet = db_session.scalars(
        select(KbDocument.id).where(KbDocument.search.op("@@")(text("plainto_tsquery('danish', 'fradragene')")))
    ).all()
    assert dok.id in fundet
