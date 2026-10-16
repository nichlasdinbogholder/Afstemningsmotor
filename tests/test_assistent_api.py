"""Fagassistenten, trin 3–4: endpoints, adgang, streaming, gemning og grundinstruksen.

Modellen kaldes ALDRIG i testene – `stream_svar` erstattes af en falsk, der svarer med faste tekststykker.
"""

import json
import logging
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import assistent as api
from app.api import login
from app.api.main import app
from app.assistent import samtale as sm
from app.assistent.models import AssistantConversation, AssistantMessage
from app.personale.models import Staff

H = {"X-Afstemning": "1"}
FALSK_NOEGLE = "sk-ant-api03-FALSK-TESTNOEGLE-0000000000000000"


@pytest.fixture
def medarbejder(db_session):
    m = Staff(navn="Elev", email="elev-ass@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def anden(db_session):
    m = Staff(navn="Anden", email="anden-ass@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def klient(db_session, medarbejder, monkeypatch):
    @contextmanager
    def samme_session():
        yield db_session

    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: medarbejder
    monkeypatch.setattr(api, "ny_session", samme_session)
    monkeypatch.setattr(sm, "slaaet_til", lambda: True)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _falsk_model(monkeypatch, stykker, fejl=None, kontrol=None):
    async def stream_svar(beskeder, svar):
        if kontrol:
            kontrol(beskeder)
        for s in stykker:
            svar.tekst += s
            yield s
        if fejl:
            raise fejl
        svar.model, svar.tokens_ind, svar.tokens_ud, svar.millisekunder = "testmodel", 10, 5, 42

    monkeypatch.setattr(sm, "stream_svar", stream_svar)


def _haendelser(svar) -> list[dict]:
    return [json.loads(linje[6:]) for linje in svar.text.split("\n\n") if linje.startswith("data: ")]


def _ny(klient) -> int:
    return klient.post("/api/v1/assistant/conversations", headers=H).json()["id"]


# 6. Uden login: 401 overalt
def test_uden_login_401(db_session):
    app.dependency_overrides[login.db] = lambda: db_session
    try:
        k = TestClient(app)
        assert k.get("/api/v1/assistant/conversations").status_code == 401
        assert k.post("/api/v1/assistant/conversations", headers=H).status_code == 401
        assert k.get("/api/v1/assistant/conversations/1/messages").status_code == 401
        assert k.post("/api/v1/assistant/conversations/1/messages", headers=H, json={"tekst": "x"}).status_code == 401
        assert k.post("/api/v1/assistant/messages/1/feedback", headers=H, json={"vurdering": "up"}).status_code == 401
        assert k.get("/api/v1/assistant/status").status_code == 401
    finally:
        app.dependency_overrides.clear()


# 7. Bruger A kan ikke se eller bruge bruger B's samtale – heller ikke med kendt id
def test_andres_samtale_findes_ikke(db_session, klient, anden, monkeypatch):
    fremmed = AssistantConversation(staff_id=anden.id, title="Hemmeligt")
    db_session.add(fremmed)
    db_session.flush()
    svar = AssistantMessage(conversation_id=fremmed.id, role="assistant", content="Svar til B")
    db_session.add(svar)
    db_session.flush()
    _falsk_model(monkeypatch, ["x"])
    assert klient.get(f"/api/v1/assistant/conversations/{fremmed.id}/messages").status_code == 404
    assert klient.post(f"/api/v1/assistant/conversations/{fremmed.id}/messages", headers=H,
                       json={"tekst": "Hej"}).status_code == 404
    assert klient.post(f"/api/v1/assistant/messages/{svar.id}/feedback", headers=H,
                       json={"vurdering": "up"}).status_code == 404
    assert fremmed.id not in [s["id"] for s in klient.get("/api/v1/assistant/conversations").json()]


def test_aendringer_kraever_header(klient):
    assert klient.post("/api/v1/assistant/conversations").status_code == 403


def test_slaaet_fra_uden_noegle(klient, monkeypatch):
    sid = _ny(klient)
    monkeypatch.setattr(sm, "slaaet_til", lambda: False)
    assert klient.get("/api/v1/assistant/status").json() == {"slaaet_til": False}
    assert klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H,
                       json={"tekst": "Hej"}).status_code == 503


def test_kunde_kommer_i_fase_4(klient):
    assert klient.post("/api/v1/assistant/conversations", headers=H, json={"kunde_id": 1}).status_code == 422


# 9 + 10. Spørgsmålet gemmes før kaldet; svaret streames og gemmes komplet
def test_spoergsmaal_gemmes_foer_kaldet_og_svar_gemmes_komplet(db_session, klient, monkeypatch):
    sid = _ny(klient)
    set_i_databasen = []

    def kontrol(beskeder):
        raekker = db_session.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == sid)).all()
        set_i_databasen.append([(r.role, r.content) for r in raekker])
        assert beskeder[-1] == {"role": "user", "content": "Kan en tømrer trække arbejdstøj fra?"}

    _falsk_model(monkeypatch, ["Arbejdstøj ", "kan trækkes fra, ", "hvis …"], kontrol=kontrol)
    svar = klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H,
                       json={"tekst": "  Kan en tømrer trække arbejdstøj fra?  "})
    assert svar.status_code == 200 and svar.headers["content-type"].startswith("text/event-stream")
    assert set_i_databasen == [[("user", "Kan en tømrer trække arbejdstøj fra?")]]
    h = _haendelser(svar)
    assert [x["type"] for x in h] == ["start", "tekst", "tekst", "tekst", "slut"]
    assert "".join(x["tekst"] for x in h if x["type"] == "tekst") == "Arbejdstøj kan trækkes fra, hvis …"
    gemt = db_session.get(AssistantMessage, h[-1]["besked_id"])
    assert (gemt.role, gemt.status, gemt.content, gemt.model, gemt.tokens_in, gemt.latency_ms) == (
        "assistant", "complete", "Arbejdstøj kan trækkes fra, hvis …", "testmodel", 10, 42)
    beskeder = klient.get(f"/api/v1/assistant/conversations/{sid}/messages").json()
    assert [b["rolle"] for b in beskeder] == ["user", "assistant"]
    assert klient.get("/api/v1/assistant/conversations").json()[0]["titel"].startswith("Kan en tømrer")


# 11. Afbrudt stream gemmer det modtagne med markering – ikke en tom række
def test_afbrudt_svar_gemmes_med_markering(db_session, klient, monkeypatch):
    sid = _ny(klient)
    _falsk_model(monkeypatch, ["Første del af svaret"], fejl=RuntimeError("forbindelsen røg"))
    h = _haendelser(klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H, json={"tekst": "Hej"}))
    assert [x["type"] for x in h] == ["start", "tekst", "slut", "fejl"]
    gemt = db_session.get(AssistantMessage, h[2]["besked_id"])
    assert (gemt.status, gemt.content) == ("interrupted", "Første del af svaret")


def test_fejl_foer_tekst_giver_ingen_tom_raekke(db_session, klient, monkeypatch):
    sid = _ny(klient)
    _falsk_model(monkeypatch, [], fejl=RuntimeError("ingen forbindelse"))
    klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H, json={"tekst": "Hej"})
    roller = db_session.scalars(select(AssistantMessage.role).where(AssistantMessage.conversation_id == sid)).all()
    assert roller == ["user"]


# 12 + 8. API-fejl giver en læsbar dansk besked – og nøglen står hverken i svaret eller i loggen
def test_fejl_er_laesbar_og_viser_aldrig_noeglen(klient, monkeypatch, caplog):
    sid = _ny(klient)
    _falsk_model(monkeypatch, [], fejl=RuntimeError(f"401 ugyldig nøgle {FALSK_NOEGLE}"))
    with caplog.at_level(logging.DEBUG):
        svar = klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H, json={"tekst": "Hej"})
    fejl = [x for x in _haendelser(svar) if x["type"] == "fejl"]
    assert fejl and fejl[0]["besked"] == api.FEJL_TEKST
    assert FALSK_NOEGLE not in svar.text
    assert FALSK_NOEGLE not in caplog.text


def test_noeglen_sendes_aldrig_til_browseren(klient):
    assert "anthropic" not in klient.get("/api/v1/assistant/status").text.lower()


# 13. Feedback én gang pr. bruger pr. svar
def test_feedback_kun_en_gang(db_session, klient, monkeypatch):
    sid = _ny(klient)
    _falsk_model(monkeypatch, ["Svar"])
    h = _haendelser(klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H, json={"tekst": "Hej"}))
    bid = h[-1]["besked_id"]
    assert klient.post(f"/api/v1/assistant/messages/{bid}/feedback", headers=H,
                       json={"vurdering": "down", "kommentar": "For kort"}).status_code == 200
    assert klient.post(f"/api/v1/assistant/messages/{bid}/feedback", headers=H,
                       json={"vurdering": "up"}).status_code == 409
    assert klient.post(f"/api/v1/assistant/messages/{bid}/feedback", headers=H,
                       json={"vurdering": "maaske"}).status_code == 422


def test_historik_starter_med_spoergsmaal_og_springer_fejlede_over(db_session, medarbejder):
    samtale = AssistantConversation(staff_id=medarbejder.id)
    db_session.add(samtale)
    db_session.flush()
    for rolle, tekst, status in [("assistant", "løst svar", "complete"), ("user", "Spørgsmål 1", "complete"),
                                 ("assistant", "fejlede", "failed"), ("assistant", "Svar 1", "complete"),
                                 ("user", "Spørgsmål 2", "complete")]:
        db_session.add(AssistantMessage(conversation_id=samtale.id, role=rolle, content=tekst, status=status))
        db_session.flush()
    assert sm.historik(db_session, samtale.id) == [
        {"role": "user", "content": "Spørgsmål 1"}, {"role": "assistant", "content": "Svar 1"},
        {"role": "user", "content": "Spørgsmål 2"}]


# Trin 4: grundinstruksen ligger i en fil og siger det vigtigste
def test_grundinstruksen():
    tekst = sm.systemtekst()
    assert "Du oplyser ALDRIG en sats, en beløbsgrænse eller en frist fra hukommelsen." in tekst
    assert "Du afgør aldrig en konkret kundesag." in tekst
    assert "Hvert svar slutter med, hvem der giver det endelige svar" in tekst
    # Indtil videnbasen findes, får modellen altid besked om, at der intet materiale er.
    assert sm.INTET_MATERIALE in tekst
