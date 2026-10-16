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


def _falsk_model(monkeypatch, stykker, fejl=None, kontrol=None, system_ud=None):
    async def stream_svar(beskeder, svar, system=None):
        if kontrol:
            kontrol(beskeder)
        if system_ud is not None:
            system_ud.append(system)
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


# Trin 6: loggen – administratorer ser alt, medarbejdere kun deres eget
def _samtale_med(db_session, staff, spoergsmaal, svar, vurdering=None, kommentar=None):
    from app.assistent.models import AssistantFeedback

    s = AssistantConversation(staff_id=staff.id, title=spoergsmaal)
    db_session.add(s)
    db_session.flush()
    db_session.add(AssistantMessage(conversation_id=s.id, role="user", content=spoergsmaal))
    db_session.flush()
    a = AssistantMessage(conversation_id=s.id, role="assistant", content=svar)
    db_session.add(a)
    db_session.flush()
    if vurdering:
        db_session.add(AssistantFeedback(message_id=a.id, staff_id=staff.id, rating=vurdering, comment=kommentar))
        db_session.flush()
    return a


def test_medarbejder_ser_kun_egne_spoergsmaal_i_loggen(db_session, klient, medarbejder, anden):
    _samtale_med(db_session, medarbejder, "Mit spørgsmål om arbejdstøj", "Svar A")
    _samtale_med(db_session, anden, "Andens spørgsmål om skurvogn", "Svar B")
    log = klient.get("/api/v1/assistant/log").json()
    tekster = [r["spoergsmaal"] for r in log["spoergsmaal"]]
    assert log["alle"] is False
    assert "Mit spørgsmål om arbejdstøj" in tekster and "Andens spørgsmål om skurvogn" not in tekster
    assert log["spoergsmaal"][0]["svar"] == "Svar A"
    ord_ = [e["ord"] for e in klient.get("/api/v1/assistant/emner").json()]
    assert "arbejdstøj" in ord_ and "skurvogn" not in ord_


def test_admin_ser_alle_og_kan_filtrere_paa_tommel_ned(db_session, klient, medarbejder, anden):
    chef = Staff(navn="Chef", email="chef-ass@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(chef)
    db_session.flush()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: chef
    _samtale_med(db_session, medarbejder, "Spørgsmål om moms ved byggeri", "Svar A", "up")
    _samtale_med(db_session, anden, "Spørgsmål om gule plader", "Svar B", "down", "Forkert regel")
    log = klient.get("/api/v1/assistant/log").json()
    tekster = [r["spoergsmaal"] for r in log["spoergsmaal"]]
    assert log["alle"] is True
    assert {"Spørgsmål om moms ved byggeri", "Spørgsmål om gule plader"} <= set(tekster)
    ned = klient.get("/api/v1/assistant/log?kun_ned=true").json()["spoergsmaal"]
    assert [(r["spoergsmaal"], r["kommentar"], r["medarbejder"]) for r in ned] == [
        ("Spørgsmål om gule plader", "Forkert regel", "Anden")]
    ord_ = [e["ord"] for e in klient.get("/api/v1/assistant/emner").json()]
    assert {"spørgsmål", "moms", "byggeri", "plader"} <= set(ord_)


def test_egen_vurdering_vises_paa_svaret(db_session, klient, medarbejder):
    a = _samtale_med(db_session, medarbejder, "Hej", "Svar", "down")
    beskeder = klient.get(f"/api/v1/assistant/conversations/{a.conversation_id}/messages").json()
    assert [b["min_vurdering"] for b in beskeder] == [None, "down"]


def test_hyppigste_ord_viser_aldrig_tal_eller_smaaord(db_session, klient, medarbejder):
    _samtale_med(db_session, medarbejder, "Kunden har CPR 010190-1234 og konto 1234 5678901234 til moms", "Svar")
    ord_ = [e["ord"] for e in klient.get("/api/v1/assistant/emner").json()]
    assert {"kunden", "konto", "moms"} <= set(ord_)
    assert not any(any(t.isdigit() for t in o) for o in ord_)
    assert "har" not in ord_ and "til" not in ord_


# Fase 2, trin 8–9: opslag i videnbasen og kilder
def _dok(db_session, **felter):
    from app.assistent.models import KbDocument

    d = KbDocument(**{"category": "skat", "status": "active", **felter})
    db_session.add(d)
    db_session.flush()
    return d


def test_19_soegning_finder_boejede_ord(db_session):
    d = _dok(db_session, title="Arbejdstøj og sikkerhedsudstyr",
             body="Fradraget for arbejdstøjet afhænger af, om tøjet kan bruges privat.")
    assert d.id in [x.id for x in sm.find_materiale(db_session, "Kan en tømrer få fradrag for arbejdstøj?")]


def test_20_og_21_udloebne_og_ikke_aktive_kommer_ikke_med(db_session):
    from datetime import date

    udloebet = _dok(db_session, title="Skurvognsfradrag 2024", body="Skurvognsfradrag gammel regel",
                    valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31))
    kladde = _dok(db_session, title="Skurvognsfradrag kladde", body="Skurvognsfradrag ny regel", status="draft")
    afloest = _dok(db_session, title="Skurvognsfradrag afløst", body="Skurvognsfradrag", status="superseded")
    fremtid = _dok(db_session, title="Skurvognsfradrag 2030", body="Skurvognsfradrag", valid_from=date(2030, 1, 1))
    gyldig = _dok(db_session, title="Skurvognsfradrag", body="Skurvognsfradrag gældende regel",
                  valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))
    fundne = [d.id for d in sm.find_materiale(db_session, "skurvognsfradrag", dag=date(2026, 10, 16))]
    assert fundne == [gyldig.id]
    assert not {udloebet.id, kladde.id, afloest.id, fremtid.id} & set(fundne)


def test_15_sats_sendes_med_vaerdi_og_bekraeftelsesdato(db_session):
    from datetime import datetime, timezone
    from decimal import Decimal

    # Opdigtet tal – testen handler om, at tallet og datoen kommer med, ikke om den rigtige sats.
    d = _dok(db_session, title="Kilometersats", category="satser", body="Sats for kørsel i egen bil.",
             value=Decimal("1.2300"), unit="kr/km", last_verified_at=datetime(2026, 1, 12, tzinfo=timezone.utc))
    tekst = sm.systemtekst(sm.find_materiale(db_session, "Hvad er kilometersatsen?"))
    assert "Kilometersats" in tekst and "Værdi: 1,23 kr/km. Senest bekræftet: 12.01.2026." in tekst
    assert sm.INTET_MATERIALE not in tekst
    assert d.id in [k["kb_id"] for k in sm.kilder(sm.find_materiale(db_session, "kilometersats"))]


def test_14_uden_materiale_faar_modellen_besked(db_session):
    assert sm.INTET_MATERIALE in sm.systemtekst(sm.find_materiale(db_session, "Hvad er momssatsen xyzzy?"))


def test_22_kilder_vises_og_gemmes(db_session, klient, monkeypatch):
    d = _dok(db_session, title="Gulpladebiler og privat kørsel", body="Regler for gulpladebiler og dagsbeviser.")
    sid = _ny(klient)
    system = []
    _falsk_model(monkeypatch, ["Svar om gulpladebiler"], system_ud=system)
    h = _haendelser(klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H,
                                json={"tekst": "Må en tømrer køre privat i en gulpladebil?"}))
    assert h[0]["kilder"] == [{"kb_id": d.id, "title": "Gulpladebiler og privat kørsel"}]
    assert "Gulpladebiler og privat kørsel" in system[0]
    gemt = db_session.get(AssistantMessage, h[-1]["besked_id"])
    assert gemt.sources == [{"kb_id": d.id, "title": "Gulpladebiler og privat kørsel"}]
    assert klient.get(f"/api/v1/assistant/conversations/{sid}/messages").json()[-1]["kilder"] == gemt.sources


def test_27_kladde_naar_aldrig_modellen(db_session, klient, monkeypatch):
    from decimal import Decimal

    _dok(db_session, title="Kilometersats NY (ikke godkendt)", category="satser", body="Kilometersats",
         value=Decimal("9.9900"), unit="kr/km", status="draft")
    sid = _ny(klient)
    system = []
    _falsk_model(monkeypatch, ["x"], system_ud=system)
    h = _haendelser(klient.post(f"/api/v1/assistant/conversations/{sid}/messages", headers=H,
                                json={"tekst": "Hvad er kilometersatsen?"}))
    assert "9,99" not in system[0] and "ikke godkendt" not in system[0]
    assert h[0]["kilder"] == []


@pytest.mark.parametrize("spoergsmaal, titel", [
    ("Hvad er kilometersatsen?", "Kilometersats"),
    ("Hvilke satser gælder?", "Satser for kørsel og kost"),
    ("momssatsen ved byggeri", "Momssats ved byggeri for egen regning"),
    ("Hvordan bogføres acontofakturaer?", "Acontofakturering og momstidspunkt"),
])
def test_19_boejninger_finder_hinanden(db_session, spoergsmaal, titel):
    d = _dok(db_session, title=titel, body="Tekst.")
    assert d.id in [x.id for x in sm.find_materiale(db_session, spoergsmaal)]


def test_korte_ord_rammer_ikke_for_bredt(db_session):
    bilag = _dok(db_session, title="Bilag skal gemmes i fem år", body="Opbevaring af bilag.")
    assert bilag.id not in [x.id for x in sm.find_materiale(db_session, "bil")]


def test_spoergsmaal_kun_med_smaaord_giver_intet(db_session):
    _dok(db_session, title="Det er en test", body="og eller men")
    assert sm.find_materiale(db_session, "og det er") == []
