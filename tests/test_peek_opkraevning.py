"""Kigge-scriptet til opkrævning: kun GET, ingen tokens i output, persondata skjult."""

import importlib.util
from pathlib import Path

import httpx

from app.adaptere.economic.klient import EconomicKlient
from app.sikkerhed.hemmeligheder import HemmeligtToken

STI = Path(__file__).resolve().parent.parent / "scripts" / "peek_opkraevning.py"
spec = importlib.util.spec_from_file_location("peek_opkraevning", STI)
peek = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peek)

FAKTURA = {"bookedInvoiceNumber": 20001, "date": "2026-09-01", "dueDate": "2026-09-15", "currency": "DKK",
           "grossAmount": 1250.0, "remainder": 1250.0, "customer": {"customerNumber": 7},
           "recipient": {"name": "Hanne Hansen", "address": "Vejen 1"},
           "self": "https://restapi.e-conomic.com/invoices/booked/20001"}
DEBITOR = {"customerNumber": 7, "name": "Hanne Hansen", "email": "hanne@eksempel.dk",
           "corporateIdentificationNumber": "12345678", "customerGroup": {"customerGroupNumber": 1}}


def test_kun_get_ingen_token_og_persondata_skjult(capsys, monkeypatch):
    metoder = []

    def svar(request: httpx.Request) -> httpx.Response:
        metoder.append(request.method)
        sti = request.url.path
        data = {"/invoices": {"booked": "https://restapi.e-conomic.com/invoices/booked"},
                "/invoices/unpaid": {"collection": [FAKTURA], "pagination": {"results": 1}},
                "/invoices/booked/20001": FAKTURA, "/customers/7": DEBITOR,
                "/customer-groups/1": {"customerGroupNumber": 1, "name": "Erhverv"}}.get(sti, {"collection": []})
        return httpx.Response(200, json=data)

    token = "hemmelig-" + "x" * 30
    klient = EconomicKlient(HemmeligtToken(token), HemmeligtToken(token), transport=httpx.MockTransport(svar))
    monkeypatch.setattr(peek, "lav_klient", lambda args: klient)
    assert peek.main(["--demo"]) == 0
    ud = capsys.readouterr().out
    assert set(metoder) == {"GET"}
    assert token not in ud
    assert "Hanne Hansen" not in ud and "hanne@eksempel.dk" not in ud and "12345678" not in ud
    assert '"dueDate": "2026-09-15"' in ud and '"remainder": 1250.0' in ud and '"currency": "DKK"' in ud
    assert '"name": "«tekst, 12 tegn»"' in ud


def test_feltoversigt_viser_kun_navne():
    antal, felter = peek.feltoversigt([DEBITOR, {"customerNumber": 8, "email": "a@b.dk"}])
    assert antal == 2
    assert felter["customerNumber"] == 2 and felter["email"] == 2 and felter["name"] == 1
    assert felter["customerGroup.customerGroupNumber"] == 1
    assert "hanne@eksempel.dk" not in str(felter) and "Hanne" not in str(felter)


def test_gruppenavn_vises_men_debitornavn_skjules():
    ud = peek.anonymiser({"name": "Hanne", "customerGroup": {"customerGroupNumber": 1, "name": "Erhverv"},
                          "vatZone": {"name": "Domestic", "vatZoneNumber": 1}})
    assert ud["customerGroup"]["name"] == "Erhverv" and ud["vatZone"]["name"] == "Domestic"
    assert ud["name"] == "«tekst, 5 tegn»"
    gruppe = peek.anonymiser({"customerGroupNumber": 2, "name": "Privat", "customers": "https://x"})
    assert gruppe["name"] == "Privat"
