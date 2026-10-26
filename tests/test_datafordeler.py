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


def _cvr_transport(virksomheder, navne, former, kald=None):
    def h(request):
        import json as _json
        q = _json.loads(request.content)
        if kald is not None:
            kald.append(q)
        if "CVR_Virksomhed(" in q["query"]:
            return httpx.Response(200, json={"data": {"CVR_Virksomhed": {"nodes": virksomheder}}})
        if "CVR_Navn(" in q["query"]:
            return httpx.Response(200, json={"data": {"CVR_Navn": {"nodes": navne}}})
        return httpx.Response(200, json={"data": {"CVR_Virksomhedsform": {"nodes": former}}})
    return httpx.MockTransport(h)


NU = {"virkningTil": None, "registreringTil": None}
GAMMEL = {"virkningTil": "2020-01-01", "registreringTil": None}


def test_slaa_op_bruger_den_gaeldende_vaerdi(slaaet_til):
    kald = []
    t = _cvr_transport([{"id": "E1", "CVRNummer": 12345678, "status": "NORMAL", "virksomhedStartdato": "2015-03-01",
                         "virksomhedOphoersdato": None, **NU}],
                       [{"vaerdi": "Gammelt Navn ApS", **GAMMEL}, {"vaerdi": "Murer Hansen ApS", **NU}],
                       [{"vaerdi": "80", "vaerdiTekst": "Anpartsselskab", **NU}], kald)
    d = datafordeler.slaa_op("12345678", transport=t)
    assert (d["navn"], d["virksomhedsform"], d["status"]) == ("Murer Hansen ApS", "Anpartsselskab", "NORMAL")
    assert kald[0]["variables"] == {"cvr": 12345678} and kald[1]["variables"] == {"id": "E1"}


def test_slaa_op_gaetter_aldrig(slaaet_til):
    with pytest.raises(datafordeler.DatafordelerFejl, match="findes ikke"):
        datafordeler.slaa_op("12345678", transport=_cvr_transport([], [], []))
    to = [{"id": "E1", "CVRNummer": 12345678, **NU}]
    with pytest.raises(datafordeler.DatafordelerFejl, match="2 gældende værdier for navnet"):
        datafordeler.slaa_op("12345678", transport=_cvr_transport(to, [{"vaerdi": "A", **NU}, {"vaerdi": "B", **NU}], []))
    with pytest.raises(datafordeler.DatafordelerFejl, match="anden virksomhed"):
        datafordeler.slaa_op("12345678", transport=_cvr_transport([{"id": "E1", "CVRNummer": 87654321, **NU}], [], []))
    with pytest.raises(datafordeler.DatafordelerFejl, match="8 cifre"):
        datafordeler.slaa_op("1234")
