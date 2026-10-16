"""Tjek af kassekladde for posteringer på fejlkonto 9900 – med simuleret adapter (intet netværk)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.adaptere.economic.adapter import EconomicAdapter
from app.adaptere.economic.klient import EconomicKlient
from app.adaptere.regnskab.base import Kassekladde, KladdePost
from app.afstemning import fejlkonto
from app.afstemning.fejlkonto import FejlkontoFejl, tjek_kassekladde
from app.sikkerhed.hemmeligheder import HemmeligtToken
from tests.test_synk_regnskab import _kunde


def _linje(kladde, nr, konto, modkonto, beloeb="100.00", tekst="Linje"):
    return KladdePost(kladde, nr, 10 + nr, date(2026, 10, 1), konto, modkonto, tekst,
                      Decimal(beloeb), "DKK", "financeVoucher")


class KladdeAdapter:
    def __init__(self, kladder, linjer):
        self.kladder, self.linjer = kladder, linjer
        self.hentede = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def fetch_journals(self):
        return self.kladder

    def fetch_journal_entries(self, nummer):
        self.hentede.append(nummer)
        return [p for p in self.linjer if p.kladde_nummer == nummer]


def _adapter():
    return KladdeAdapter(
        [Kassekladde(1, "Kassekladde"), Kassekladde(2, "Lønkladde")],
        [
            _linje(1, 1, 1010, 5820),                       # almindelig – ikke med
            _linje(1, 2, 9900, 5820, "250.00", "Ukendt"),   # 9900 som konto
            _linje(1, 3, 2750, 9900, "40.00", "Retur"),     # 9900 som modkonto
            _linje(2, 4, 9900, 5820, "999.00", "Løn"),      # i en anden kladde
        ],
    )


def test_finder_9900_som_konto_og_modkonto():
    r = tjek_kassekladde(_adapter(), 9900, kladde="Kassekladde")
    assert [p.linje_id for p in r.fund] == [2, 3]
    assert r.sum == Decimal("210.00")  # 250 på kontoen, 40 modsat via modkontoen


def test_ingen_fund_naar_kladden_er_ren():
    ren = KladdeAdapter([Kassekladde(1, "Kassekladde")], [_linje(1, 1, 1010, 5820)])
    assert tjek_kassekladde(ren, 9900).fund == []


def test_kladde_valgt_paa_nummer_navn_kundekartotek_eller_alle():
    a = _adapter()
    assert [p.linje_id for p in tjek_kassekladde(a, 9900, kladde="2").fund] == [4]
    assert [k.nummer for k in tjek_kassekladde(a, 9900, kladde=" kassekladde ").kladder] == [1]
    assert [k.nummer for k in tjek_kassekladde(a, 9900, standard_navn="Lønkladde").kladder] == [2]
    assert [k.nummer for k in tjek_kassekladde(a, 9900).kladder] == [1, 2]
    alle = tjek_kassekladde(a, 9900, standard_navn="Lønkladde", alle_kladder=True)
    assert [p.linje_id for p in alle.fund] == [2, 3, 4]


def test_ukendt_kladde_giver_tydelig_fejl():
    with pytest.raises(FejlkontoFejl, match="findes ikke.*Kassekladde.*Lønkladde"):
        tjek_kassekladde(_adapter(), 9900, kladde="Findes ikke")


def test_anden_fejlkonto_kan_vaelges():
    assert [p.linje_id for p in tjek_kassekladde(_adapter(), 2750, kladde="1").fund] == [3]


def test_e_conomic_kassekladde_oversaettes(token):
    def e_conomic(request):
        if request.url.path == "/journals":
            data = [{"journalNumber": 1, "name": "Kassekladde"}]
        else:
            assert request.url.path == "/journals/1/entries"
            data = [{"journalEntryNumber": 7, "voucher": {"voucherNumber": 33}, "date": "2026-10-01",
                     "account": {"accountNumber": 9900}, "contraAccount": {"accountNumber": 5820},
                     "text": "Ukendt indbetaling", "amount": 1250.5, "currency": {"code": "DKK"},
                     "entryType": "financeVoucher"},
                    {"journalEntryNumber": 8, "amount": 1}]  # kun det mest nødvendige
        return httpx.Response(200, json={"collection": data, "pagination": {"results": len(data)}})

    klient = EconomicKlient(HemmeligtToken("app-" + token), HemmeligtToken(token),
                            transport=httpx.MockTransport(e_conomic))
    r = tjek_kassekladde(EconomicAdapter(klient), 9900)
    assert len(r.fund) == 1
    p = r.fund[0]
    assert (p.linje_id, p.bilagsnummer, p.konto, p.modkonto, p.beloeb, p.valuta) == (
        7, 33, 9900, 5820, Decimal("1250.5"), "DKK")


class _GenbrugSession:
    def __init__(self, s):
        self._s = s

    def __enter__(self):
        return self._s

    def __exit__(self, *_):
        return False


def test_kommando_viser_fund_og_afslutningskode(db_session, monkeypatch, capsys):
    k = _kunde(db_session, "FK-1")
    k.kassekladde_navn = "Kassekladde"
    db_session.commit()
    monkeypatch.setattr(fejlkonto, "ny_session", lambda: _GenbrugSession(db_session))

    kode = fejlkonto.main(["--kundenummer", "FK-1"], adapter_fabrik=lambda s, c: _adapter())

    ud = capsys.readouterr().out
    assert kode == 1
    assert "Kassekladde(r) tjekket: 1 'Kassekladde'" in ud
    assert "✘ 2 postering(er) på konto 9900" in ud and "210,00" in ud
    assert "Ukendt" in ud and "Retur" in ud
    assert "999,00" not in ud  # linjen i lønkladden er ikke med


def test_kommando_ren_kladde_giver_kode_0(db_session, monkeypatch, capsys):
    _kunde(db_session, "FK-2")
    monkeypatch.setattr(fejlkonto, "ny_session", lambda: _GenbrugSession(db_session))
    ren = KladdeAdapter([Kassekladde(1, "Kassekladde")], [_linje(1, 1, 1010, 5820)])
    assert fejlkonto.main(["--kundenummer", "FK-2"], adapter_fabrik=lambda s, c: ren) == 0
    assert "✔ INGEN posteringer på konto 9900" in capsys.readouterr().out
