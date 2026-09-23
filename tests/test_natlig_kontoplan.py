"""Den natlige kørsel: alle kunder hentes, én fejl stopper ikke resten, og tidsplanen er én gang i døgnet."""

import plistlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from tenacity import wait_none

from app.adaptere.economic.kontoplan import KontoplanFejl, hent_for_alle
from app.config import get_settings
from app.kunder.adgange import gem_token
from app.kunder.models import Client
from app.planlaegning.natlig_kontoplan import ETIKET, _tjek_tid, lav_plist
from app.regnskab.models import Account


@pytest.fixture
def app_secret(monkeypatch):
    monkeypatch.setenv("ECONOMIC_APP_SECRET_TOKEN", "test-app-hemmelighed-til-natlig-koersel")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _kunde(session, nr, token, status="aktiv"):
    k = Client(navn=f"Kunde {nr}", kundenummer=nr, regnskabssystem="economic", status=status)
    session.add(k)
    session.flush()
    gem_token(session, k.id, "economic", token + nr)
    return k


def test_henter_alle_og_fortsaetter_efter_fejl(db_session, token, app_secret, caplog):
    god = _kunde(db_session, "N-1", token)
    _kunde(db_session, "N-2", token)  # denne kundes nøgle afvises af e-conomic
    _kunde(db_session, "N-3", token, status="opsagt")  # springes over

    def falsk(request):
        if request.headers["X-AgreementGrantToken"] == token + "N-2":
            return httpx.Response(401, json={"message": "ugyldig"})
        return httpx.Response(200, json={
            "collection": [{"accountNumber": 1010, "name": "Salg", "accountType": "profitAndLoss"}],
            "pagination": {"results": 1},
        })

    resultat = hent_for_alle(db_session, transport=httpx.MockTransport(falsk), vent=wait_none())

    assert resultat == {"antal_kunder": 2, "ok": ["N-1"], "fejlede": ["N-2"]}
    assert db_session.scalar(select(func.count()).select_from(Account)) == 1
    assert db_session.scalar(select(Account.tenant_id)) == god.id
    assert "N-2" in caplog.text and "401" in caplog.text
    assert token not in caplog.text


def test_stopper_straks_uden_app_noegle(db_session, token, monkeypatch):
    _kunde(db_session, "N-1", token)
    monkeypatch.setenv("ECONOMIC_APP_SECRET_TOKEN", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(KontoplanFejl, match="ECONOMIC_APP_SECRET_TOKEN"):
            hent_for_alle(db_session, transport=httpx.MockTransport(lambda r: 1 / 0))
    finally:
        get_settings.cache_clear()


def test_tidsplan_er_en_gang_i_doegnet():
    plist = lav_plist(2, 30, "/sti/.venv/bin/python", Path("/sti"), Path("/sti/logs/k.log"))
    assert plist["Label"] == ETIKET
    assert plist["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
    # Ingen gentagelse inden for døgnet og ingen kørsel ved hver opstart.
    assert "StartInterval" not in plist and plist["RunAtLoad"] is False
    assert plist["ProgramArguments"][1:] == ["-m", "app.adaptere.economic.kontoplan", "--alle"]
    plistlib.dumps(plist)  # gyldig indstillingsfil


@pytest.mark.parametrize("tid, forventet", [("02:30", (2, 30)), ("4:05", (4, 5)), ("23:59", (23, 59))])
def test_gyldige_tidspunkter(tid, forventet):
    assert _tjek_tid(tid) == forventet


@pytest.mark.parametrize("tid", ["24:00", "2.30", "kl 2", "02:60", ""])
def test_ugyldige_tidspunkter(tid):
    with pytest.raises(SystemExit):
        _tjek_tid(tid)
