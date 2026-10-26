"""Datafordeleren (bygherren 26.10.2026): prøvekommandoen, der viser CVR-tjenestens felter. Nøglen må aldrig
stå i en fejlbesked – Datafordeleren vil have den i adressen."""

import httpx
import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.kunder import datafordeler

NOEGLE = "df-test-noegle-7c1d"


@pytest.fixture
def slaaet_til(monkeypatch):
    monkeypatch.setattr(get_settings(), "datafordeler_api_key", SecretStr(NOEGLE))


def _transport(status=200, svar=None, kald=None):
    def h(request: httpx.Request) -> httpx.Response:
        if kald is not None:
            kald.append(request)
        return httpx.Response(status, json=svar or {})
    return httpx.MockTransport(h)


SKEMA = {"data": {"__schema": {
    "queryType": {"fields": [{"name": "CVR_Virksomhed"}, {"name": "CVR_Navn"}, {"name": "CVR_Person"}]},
    "types": [
        {"name": "CVR_Virksomhed", "kind": "OBJECT", "fields": [
            {"name": "CVRNummer", "type": {"name": None, "kind": "NON_NULL", "ofType": {"name": "Long", "kind": "SCALAR"}}},
            {"name": "status", "type": {"name": "String", "kind": "SCALAR", "ofType": None}}]},
        {"name": "CVR_Person", "kind": "OBJECT", "fields": [
            {"name": "navn", "type": {"name": "String", "kind": "SCALAR", "ofType": None}}]},
        {"name": "__Type", "kind": "OBJECT", "fields": [{"name": "x", "type": {"name": "String", "kind": "SCALAR"}}]},
    ]}}}


def test_slaaet_fra_uden_noegle(monkeypatch):
    monkeypatch.setattr(get_settings(), "datafordeler_api_key", None)
    with pytest.raises(datafordeler.DatafordelerFejl, match="ikke slået til"):
        datafordeler.forespoerg("{x}")


def test_skemaet_vises_kun_for_relevante_typer(slaaet_til):
    kald = []
    tekst = datafordeler.skema_tekst(datafordeler.forespoerg(datafordeler.SKEMA_FORESPOERGSEL,
                                                             transport=_transport(svar=SKEMA, kald=kald)))
    assert "CVR_Virksomhed\n  CVRNummer: Long!\n  status: String" in tekst
    assert "CVR_Person" not in tekst.split("\n", 1)[1] and "__Type" not in tekst
    assert kald[0].method == "POST" and kald[0].url.params["apiKey"] == NOEGLE


@pytest.mark.parametrize("status,tekst", [(401, "afviste API-nøglen"), (500, "svarede med fejl")])
def test_fejl_viser_aldrig_noeglen(slaaet_til, status, tekst):
    with pytest.raises(datafordeler.DatafordelerFejl, match=tekst) as fejl:
        datafordeler.forespoerg("{x}", transport=_transport(status=status))
    assert NOEGLE not in str(fejl.value)


def test_graphql_fejl_vises(slaaet_til):
    with pytest.raises(datafordeler.DatafordelerFejl, match="Ukendt felt"):
        datafordeler.forespoerg("{x}", transport=_transport(svar={"errors": [{"message": "Ukendt felt x"}]}))


def test_fejl_viser_datafordelerens_forklaring_men_ikke_noeglen(slaaet_til):
    svar = {"errors": [{"message": f"Introspection er slået fra ({NOEGLE})"}]}
    with pytest.raises(datafordeler.DatafordelerFejl, match=r"status 400\): Introspection er slået fra") as fejl:
        datafordeler.forespoerg("{x}", transport=_transport(status=400, svar=svar))
    assert NOEGLE not in str(fejl.value)


SDL = """schema { query: Query }
type Query {
  CVR_Virksomhed(where: X): CVR_VirksomhedConnection
}
type CVR_Navn {
  vaerdi: String
  CVREnhedsId: Long
}
type CVR_Person {
  navn: String
}
"""


def test_skemaet_hentes_fra_schema_adressen(slaaet_til):
    kald = []

    def h(request):
        kald.append(request)
        return httpx.Response(200, text=SDL)
    udsnit = datafordeler.skema_udsnit(datafordeler.hent_skema(transport=httpx.MockTransport(h)))
    assert kald[0].method == "GET" and kald[0].url.path == "/CVR/v2/schema"
    assert "type CVR_Navn {\n  vaerdi: String" in udsnit and "CVR_Person" not in udsnit
    assert "type Query" in udsnit
