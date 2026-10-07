"""Login med Microsoft: kun aktive medarbejdere i jeres egen Microsoft 365, to roller."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.audit.models import AuditLog
from app.personale import bruger
from app.personale.models import Staff

TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def indstillinger(monkeypatch):
    s = SimpleNamespace(ms_tenant_id=TENANT, public_url="http://testserver")
    monkeypatch.setattr(login, "get_settings", lambda: s)
    monkeypatch.setattr(login, "login_slaaet_til", lambda: True)
    return s


@pytest.fixture
def medarbejdere(db_session):
    a = Staff(navn="Anne Admin", email="anne@dinbogholder.dk", rolle="admin", aktiv=True)
    b = Staff(navn="Bo Bruger", email="bo@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    c = Staff(navn="Carl Stoppet", email="carl@dinbogholder.dk", rolle="medarbejder", aktiv=False)
    db_session.add_all([a, b, c])
    db_session.flush()
    return {"admin": a, "bruger": b, "stoppet": c}


def _claims(email, oid="oid-1", tid=TENANT):
    return {"tid": tid, "oid": oid, "preferred_username": email}


@pytest.fixture
def klient(db_session, indstillinger, monkeypatch):
    app.dependency_overrides[login.db] = lambda: db_session
    claims = {}

    class FalskMicrosoft:
        async def authorize_access_token(self, request):
            return {"userinfo": dict(claims)}

    monkeypatch.setattr(login, "_microsoft", lambda: FalskMicrosoft())
    k = TestClient(app)
    k.log_ind = lambda c: (claims.clear(), claims.update(c), k.get("/auth/callback", follow_redirects=False))[2]
    yield k
    app.dependency_overrides.clear()


# --- Hvem må komme ind ------------------------------------------------------------------


def test_aktiv_medarbejder_kommer_ind_og_microsoft_id_bindes(db_session, indstillinger, medarbejdere):
    m = login.find_medarbejder(db_session, _claims("Bo@DinBogholder.dk"))
    assert m.id == medarbejdere["bruger"].id
    assert m.microsoft_oid == "oid-1" and m.sidst_logget_ind is not None


@pytest.mark.parametrize("claims, aarsag", [
    (_claims("bo@dinbogholder.dk", tid="anden-tenant"), "Microsoft 365"),
    (_claims("ukendt@dinbogholder.dk"), "ikke oprettet"),
    (_claims("carl@dinbogholder.dk"), "ikke oprettet"),  # deaktiveret
    ({"tid": TENANT, "oid": "x"}, "ingen e-mail"),
])
def test_afvises(db_session, indstillinger, medarbejdere, claims, aarsag):
    with pytest.raises(login.LoginAfvist, match=aarsag):
        login.find_medarbejder(db_session, claims)


def test_genbrugt_email_med_anden_microsoft_konto_afvises(db_session, indstillinger, medarbejdere):
    login.find_medarbejder(db_session, _claims("bo@dinbogholder.dk", oid="oid-1"))
    with pytest.raises(login.LoginAfvist, match="anden Microsoft-konto"):
        login.find_medarbejder(db_session, _claims("bo@dinbogholder.dk", oid="oid-2"))


# --- Hele forløbet gennem webdelen -------------------------------------------------------


def test_uden_login_er_der_ingen_adgang(klient):
    assert klient.get("/mig").status_code == 401
    assert klient.get("/admin/medarbejdere").status_code == 401
    assert "Log ind med Microsoft" in klient.get("/").text
    assert klient.get("/health").status_code in (200, 503)  # /health kræver aldrig login


def test_medarbejder_logger_ind_men_er_ikke_administrator(klient, db_session, medarbejdere):
    svar = klient.log_ind(_claims("bo@dinbogholder.dk"))
    assert svar.status_code == 303 and svar.headers["location"] == "/"
    assert klient.get("/mig").json() == {"navn": "Bo Bruger", "email": "bo@dinbogholder.dk",
                                         "rolle": "medarbejder"}
    assert "Logget ind som Bo Bruger (medarbejder)" in klient.get("/").text
    assert klient.get("/admin/medarbejdere").status_code == 403
    assert db_session.scalars(select(AuditLog).where(AuditLog.handling == "login",
                                                     AuditLog.staff_id == medarbejdere["bruger"].id)).one()


def test_administrator_ser_medarbejderne(klient, medarbejdere):
    klient.log_ind(_claims("anne@dinbogholder.dk"))
    navne = [m["navn"] for m in klient.get("/admin/medarbejdere").json()]
    assert {"Anne Admin", "Bo Bruger", "Carl Stoppet"} <= set(navne)


def test_ukendt_person_afvises_og_logges(klient, db_session, medarbejdere):
    svar = klient.log_ind(_claims("fremmed@dinbogholder.dk"))
    assert svar.status_code == 403
    assert klient.get("/mig").status_code == 401
    afvist = db_session.scalars(select(AuditLog).where(AuditLog.handling == "login_afvist")).all()
    assert any("fremmed@dinbogholder.dk" in a.detaljer["aarsag"] for a in afvist)


def test_deaktiveret_medarbejder_mister_adgangen_med_det_samme(klient, db_session, medarbejdere):
    klient.log_ind(_claims("bo@dinbogholder.dk"))
    assert klient.get("/mig").status_code == 200
    medarbejdere["bruger"].aktiv = False
    db_session.flush()
    assert klient.get("/mig").status_code == 401


def test_log_ud(klient, medarbejdere):
    klient.log_ind(_claims("bo@dinbogholder.dk"))
    klient.get("/logout", follow_redirects=False)
    assert klient.get("/mig").status_code == 401


def test_login_uden_opsaetning_giver_besked(db_session, monkeypatch):
    monkeypatch.setattr(login, "login_slaaet_til", lambda: False)
    svar = TestClient(app).get("/login", follow_redirects=False)
    assert svar.status_code == 503 and "ikke sat op" in svar.json()["detail"]


def test_login_cookien_indeholder_ingen_oplysninger_fra_microsoft(klient, medarbejdere):
    klient.log_ind(_claims("bo@dinbogholder.dk", oid="hemmeligt-oid-123"))
    cookie = klient.cookies.get("afstemning_login") or ""
    import base64

    indhold = base64.b64decode(cookie.split(".")[0] + "==").decode(errors="ignore")
    assert "hemmeligt-oid" not in indhold and "bo@" not in indhold


# --- Kommandoen til medarbejdere -----------------------------------------------------------


def test_kommando_opret_rolle_og_deaktiver(db_session, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def samme_session():
        yield db_session

    monkeypatch.setattr(bruger, "ny_session", samme_session)
    assert bruger.main(["opret", "--email", "Ny@DinBogholder.dk", "--navn", "Ny Person"]) == 0
    m = db_session.scalars(select(Staff).where(Staff.email == "ny@dinbogholder.dk")).one()
    assert (m.rolle, m.aktiv) == ("medarbejder", True)
    assert bruger.main(["rolle", "--email", "ny@dinbogholder.dk", "--rolle", "admin"]) == 0
    assert bruger.main(["deaktiver", "--email", "ny@dinbogholder.dk"]) == 0
    assert (m.rolle, m.aktiv) == ("admin", False)
    assert bruger.main(["opret", "--email", "ny@dinbogholder.dk", "--navn", "Igen"]) == 2
    handlinger = db_session.scalars(select(AuditLog.handling).where(
        AuditLog.handling.like("medarbejder_%"))).all()
    assert {"medarbejder_opret", "medarbejder_rolle", "medarbejder_deaktiver"} <= set(handlinger)
