"""Den natlige kørsel: alle kunder hentes, én fejl stopper ikke resten, og tidsplanen er én gang i døgnet."""

import plistlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from tenacity import wait_none

from functools import partial

from app.adaptere.economic.adapter import lav_economic_adapter
from app.adaptere.regnskab import base
from app.synk.kontoplan import hent_for_alle
from app.config import get_settings
from app.kunder.adgange import gem_token
from app.kunder.models import Client
from app.planlaegning.natlig_kontoplan import ETIKET, _tjek_tid, lav_plist, main
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
    session.commit()
    return k


def test_henter_alle_og_fortsaetter_efter_fejl(db_session, token, app_secret, caplog, monkeypatch):
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

    monkeypatch.setitem(base._FABRIKKER, "economic", partial(
        lav_economic_adapter, transport=httpx.MockTransport(falsk), vent=wait_none()))
    resultat = hent_for_alle(db_session)

    # Kun testkunderne vurderes – databasen kan indeholde rigtige kunder.
    test_nr = {"N-1", "N-2", "N-3"}
    assert [nr for nr in resultat["ok"] if nr in test_nr] == ["N-1"]
    assert [nr for nr in resultat["fejlede"] if nr in test_nr] == ["N-2"]
    assert db_session.scalars(select(Account.tenant_id).where(
        Account.tenant_id.in_(select(Client.id).where(Client.kundenummer.in_(test_nr)))
    )).all() == [god.id]
    assert "N-2" in caplog.text and "401" in caplog.text
    assert token not in caplog.text


def test_uden_app_noegle_fejler_kunden_men_koerslen_stopper_ikke(db_session, token, monkeypatch, caplog):
    _kunde(db_session, "N-1", token)
    monkeypatch.setenv("ECONOMIC_APP_SECRET_TOKEN", "")
    get_settings.cache_clear()
    try:
        resultat = hent_for_alle(db_session)
    finally:
        get_settings.cache_clear()
    assert "N-1" in resultat["fejlede"]
    assert "ECONOMIC_APP_SECRET_TOKEN" in caplog.text


def test_tidsplan_er_en_gang_i_doegnet():
    plist = lav_plist(2, 30, "/sti/.venv/bin/python", Path("/sti"), Path("/sti/logs/k.log"))
    assert plist["Label"] == ETIKET
    assert plist["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
    # Ingen gentagelse inden for døgnet og ingen kørsel ved hver opstart.
    assert "StartInterval" not in plist and plist["RunAtLoad"] is False
    assert plist["ProgramArguments"][1:] == ["-m", "app.synk.kontoplan", "--alle"]
    plistlib.dumps(plist)  # gyldig indstillingsfil


@pytest.mark.parametrize("tid, forventet", [("02:30", (2, 30)), ("4:05", (4, 5)), ("23:59", (23, 59))])
def test_gyldige_tidspunkter(tid, forventet):
    assert _tjek_tid(tid) == forventet


@pytest.mark.parametrize("tid", ["24:00", "2.30", "kl 2", "02:60", ""])
def test_ugyldige_tidspunkter(tid):
    with pytest.raises(SystemExit):
        _tjek_tid(tid)


def test_standardtidspunkt_er_12_30(monkeypatch):
    brugt = {}
    monkeypatch.setattr(
        "app.planlaegning.natlig_kontoplan.installer", lambda tid: brugt.setdefault("tid", tid)
    )
    main(["installer"])
    assert _tjek_tid(brugt["tid"]) == (12, 30)
