"""Test af hentning af kontoplan fra e-conomic – mod et falsk e-conomic (ingen netværk)."""

import io
import logging
import secrets

import httpx
import pytest
from sqlalchemy import select, text
from tenacity import wait_none

from app.adaptere.adgang import AdgangMangler
from app.adaptere.economic.klient import EconomicFejl
from app.adaptere.economic.kontoplan import KontoplanFejl, hent_og_gem_kontoplan
from app.audit.models import AuditLog
from app.config import get_settings
from app.kunder.models import Client, Credential
from app.regnskab.models import Account

BASE = "https://restapi.e-conomic.com"


def _konto(nr: int, navn: str, **ekstra) -> dict:
    return {
        "accountNumber": nr,
        "accountType": "profitAndLoss",
        "name": navn,
        "debitCredit": "credit",
        "balance": 1234.5,
        "draftBalance": 1300.0,
        "barred": False,
        "blockDirectEntries": False,
        "vatAccount": {"vatCode": "U25", "self": f"{BASE}/vat-accounts/U25"},
        "self": f"{BASE}/accounts/{nr}",
        **ekstra,
    }


@pytest.fixture
def app_secret(monkeypatch) -> str:
    vaerdi = "test-app-" + secrets.token_urlsafe(24)
    monkeypatch.setenv("ECONOMIC_APP_SECRET_TOKEN", vaerdi)
    get_settings.cache_clear()
    yield vaerdi
    get_settings.cache_clear()


@pytest.fixture
def kunde(db_session, token):
    k = Client(navn="Kontoplan ApS", kundenummer="KP-1", regnskabssystem="economic")
    adgang = Credential(system="economic")
    adgang.saet_token(token)
    k.credentials.append(adgang)
    db_session.add(k)
    db_session.commit()
    return k


class FalskEconomic:
    """Svarer som e-conomic: to sider, der kædes sammen med pagination.nextPage."""

    def __init__(self, sider: list[list[dict]], fejl_foerst: list[int] | None = None):
        self.sider = sider
        self.fejl_foerst = list(fejl_foerst or [])
        self.kald: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.kald.append(request)
        if self.fejl_foerst:
            return httpx.Response(self.fejl_foerst.pop(0), json={"message": "fejl"})
        side = int(request.url.params.get("skipPages", 0))
        antal = sum(len(s) for s in self.sider)
        pagination = {
            "maxPageSizeAllowed": 1000, "skipPages": side, "pageSize": 1000,
            "results": antal, "resultsWithoutFilter": antal,
        }
        if side + 1 < len(self.sider):
            pagination["nextPage"] = f"{BASE}/accounts?skipPages={side + 1}&pageSize=1000"
        return httpx.Response(200, json={"collection": self.sider[side], "pagination": pagination})


def _hent(db_session, kunde, falsk):
    return hent_og_gem_kontoplan(
        db_session, kunde.id, transport=httpx.MockTransport(falsk), vent=wait_none()
    )


def test_henter_alle_sider_og_gemmer_med_tenant_id(db_session, kunde, token, app_secret):
    falsk = FalskEconomic([[_konto(1010, "Salg"), _konto(1020, "Salg EU")], [_konto(5810, "Bank")]])

    resultat = _hent(db_session, kunde, falsk)

    assert resultat == {"antal_konti": 3, "fjernet": 0}
    konti = db_session.scalars(select(Account).where(Account.tenant_id == kunde.id)).all()
    assert sorted(k.kontonummer for k in konti) == [1010, 1020, 5810]
    bank = next(k for k in konti if k.kontonummer == 5810)
    assert (bank.navn, bank.momskode, bank.system, str(bank.saldo)) == ("Bank", "U25", "economic", "1234.50")

    # Rigtige headere og sidestørrelse på første kald, derefter nextPage.
    foerste = falsk.kald[0]
    assert foerste.url.path == "/accounts"
    assert foerste.url.params["pageSize"] == "1000"
    assert foerste.headers["X-AppSecretToken"] == app_secret
    assert foerste.headers["X-AgreementGrantToken"] == token
    assert foerste.headers["Content-Type"] == "application/json"
    assert falsk.kald[1].url.params["skipPages"] == "1"
    assert db_session.scalars(select(AuditLog.handling)).all() == ["kontoplan_hentet"]


def test_ny_hentning_opdaterer_og_fjerner_forsvundne_konti(db_session, kunde, app_secret):
    _hent(db_session, kunde, FalskEconomic([[_konto(1010, "Salg"), _konto(1020, "Gammel")]]))
    resultat = _hent(db_session, kunde, FalskEconomic([[_konto(1010, "Salg af varer")]]))

    assert resultat == {"antal_konti": 1, "fjernet": 1}
    konti = db_session.scalars(select(Account).where(Account.tenant_id == kunde.id)).all()
    assert [(k.kontonummer, k.navn) for k in konti] == [(1010, "Salg af varer")]


def test_proever_igen_ved_429_og_serverfejl(db_session, kunde, app_secret):
    falsk = FalskEconomic([[_konto(1010, "Salg")]], fejl_foerst=[429, 503])
    assert _hent(db_session, kunde, falsk)["antal_konti"] == 1
    assert len(falsk.kald) == 3


def test_giver_op_efter_fem_forsoeg(db_session, kunde, app_secret):
    falsk = FalskEconomic([[_konto(1010, "Salg")]], fejl_foerst=[503] * 10)
    with pytest.raises(EconomicFejl, match="503"):
        _hent(db_session, kunde, falsk)
    assert len(falsk.kald) == 5


def test_proever_ikke_igen_ved_forkerte_noegler(db_session, kunde, token, app_secret):
    falsk = FalskEconomic([[_konto(1010, "Salg")]], fejl_foerst=[401])
    with pytest.raises(EconomicFejl, match="401") as fejl:
        _hent(db_session, kunde, falsk)
    assert len(falsk.kald) == 1
    assert token not in str(fejl.value) and app_secret not in str(fejl.value)


def test_sender_aldrig_noegler_til_anden_adresse(db_session, kunde, app_secret):
    def ondsindet(request):
        return httpx.Response(200, json={
            "collection": [_konto(1010, "Salg")],
            "pagination": {"results": 2, "nextPage": "https://evil.example/accounts?skipPages=1"},
        })

    kald = []
    with pytest.raises(EconomicFejl, match="uden for e-conomic"):
        hent_og_gem_kontoplan(
            db_session, kunde.id,
            transport=httpx.MockTransport(lambda r: kald.append(r) or ondsindet(r)),
            vent=wait_none(),
        )
    assert all(r.url.host == "restapi.e-conomic.com" for r in kald)


def test_tom_kontoplan_sletter_ikke_den_gemte(db_session, kunde, app_secret):
    _hent(db_session, kunde, FalskEconomic([[_konto(1010, "Salg")]]))
    with pytest.raises(KontoplanFejl, match="ingen konti"):
        _hent(db_session, kunde, FalskEconomic([[]]))
    assert db_session.scalar(text("SELECT count(*) FROM accounts")) == 1


def test_mangler_adgang(db_session, app_secret):
    uden = Client(navn="Uden adgang", kundenummer="KP-2", regnskabssystem="economic")
    db_session.add(uden)
    db_session.commit()
    with pytest.raises(AdgangMangler):
        _hent(db_session, uden, FalskEconomic([[]]))


def test_mangler_app_noegle(db_session, kunde, monkeypatch):
    monkeypatch.setenv("ECONOMIC_APP_SECRET_TOKEN", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(KontoplanFejl, match="ECONOMIC_APP_SECRET_TOKEN"):
            _hent(db_session, kunde, FalskEconomic([[]]))
    finally:
        get_settings.cache_clear()


def test_noegler_havner_ikke_i_logs(db_session, kunde, token, app_secret):
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    rod = logging.getLogger()
    gammelt_niveau = rod.level
    rod.addHandler(handler)
    rod.setLevel(logging.DEBUG)
    try:
        falsk = FalskEconomic([[_konto(1010, "Salg")]], fejl_foerst=[429])
        _hent(db_session, kunde, falsk)
    finally:
        rod.removeHandler(handler)
        rod.setLevel(gammelt_niveau)
    log = buffer.getvalue()
    assert "prøver igen" in log  # genforsøget blev logget …
    assert token not in log and app_secret not in log  # … men uden nøgler
