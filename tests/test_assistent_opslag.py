"""Fagassistenten, fase 4 trin 13: Svends LÆSENDE opslag i kundens regnskab (spec-test 31, 33–38).

e-conomic er SIMULERET (httpx.MockTransport eller en falsk provider), og modellen er en falsk klient med faste
svar – der sendes aldrig noget ud på nettet.
"""

import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adaptere.economic.adapter import EconomicAdapter
from app.adaptere.economic.klient import EconomicKlient
from app.adaptere.regnskab.base import AabenPost, Konto, Kunde, Leverandoer, Momskode, Postering, Regnskabsaar
from app.api import assistent as api
from app.api import login
from app.api.main import app
from app.assistent import opslag as op
from app.assistent import samtale as sm
from app.assistent.models import AssistantMessage
from app.kunder.models import Client
from app.personale.models import Staff
from app.sikkerhed.hemmeligheder import HemmeligtToken

H = {"X-Afstemning": "1"}


@pytest.fixture
def medarbejder(db_session):
    m = Staff(navn="Opslag", email="opslag-ass@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def kunde(db_session, medarbejder):
    k = Client(navn="Opslag Tømrer ApS", kundenummer="OPS-1", cvr="12345678", regnskabssystem="economic",
               status="aktiv", ansvarlig_medarbejder_id=medarbejder.id)
    db_session.add(k)
    db_session.flush()
    return k


@pytest.fixture
def samme_session(db_session, monkeypatch):
    @contextmanager
    def s():
        yield db_session

    monkeypatch.setattr(op, "ny_session", s)
    monkeypatch.setattr(api, "ny_session", s)
    return s


def _post(nr, konto, beloeb, dato="2026-08-10", tekst=None):
    return Postering(bogfoert_id=nr, bilagsnummer=100 + nr, dato=date.fromisoformat(dato), kontonummer=konto,
                     tekst=tekst or f"Bilag {nr}", beloeb=Decimal(beloeb), modpart=None, valuta="DKK",
                     entry_type="financeVoucher", beloeb_dkk=Decimal(beloeb))


def _konto(nr, navn, momskode=None):
    return Konto(kontonummer=nr, navn=navn, kontotype="profitAndLoss", debet_kredit="debit", momskode=momskode,
                 spaerret=False, direkte_posteringer_blokeret=False, saldo=Decimal("1000"), kladdesaldo=None,
                 raa_data={})


class FalskProvider:
    """Kundens regnskab – kun læsefunktioner, ligesom den rigtige provider."""

    def __init__(self, poster=None):
        self.poster = poster or []
        self.kald = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def fetch_accounts(self):
        self.kald.append("accounts")
        return [_konto(3217, "Arbejdstøj", "I25"), _konto(2210, "Lønninger"), _konto(14260, "Salgsmoms"),
                _konto(14240, "Købsmoms")]

    def fetch_entries_period(self, fra, til):
        self.kald.append(("periode", fra, til))
        return [p for p in self.poster if fra <= p.dato <= til]

    def fetch_vat_accounts(self):
        return [Momskode("U25", "Salgsmoms", Decimal(25), 14260, None),
                Momskode("I25", "Købsmoms", Decimal(25), 14240, None)]

    def fetch_open_entries(self):
        return [AabenPost(1, "debitor", 7, None, 55, "F-1", date(2026, 7, 1), date(2026, 7, 15), Decimal(500),
                          Decimal(500), "DKK"),
                AabenPost(2, "kreditor", 9, None, 56, "L-1", date(2026, 7, 2), date(2026, 8, 1), Decimal(-80),
                          Decimal(-80), "DKK")]

    def fetch_customers(self):
        return [Kunde(7, "Jens Hansen", None, None, False, None)]

    def fetch_suppliers(self):
        return [Leverandoer(9, "Grossisten A/S", "87654321", None, None)]

    def fetch_accounting_years(self):
        return [Regnskabsaar("2026", date(2026, 1, 1), date(2026, 12, 31), False)]


@pytest.fixture
def provider(monkeypatch):
    p = FalskProvider([_post(1, 3217, "400"), _post(2, 3217, "600"), _post(3, 3217, "50", dato="2026-10-02"),
                       _post(4, 2210, "30000", tekst="Løn Mette Jensen 010190-1234"),
                       _post(5, 14260, "-2500"), _post(6, 14240, "1000")])
    monkeypatch.setattr(op, "hent_adapter", lambda session, client_id: p)
    return p


def _opslag(medarbejder, kunde):
    return op.Opslag(staff_id=medarbejder.id, client_id=kunde.id)


Q3 = {"fra": "2026-07-01", "til": "2026-09-30"}


def test_de_syv_opslag_giver_tal_fra_kundens_regnskab(db_session, samme_session, provider, medarbejder, kunde):
    o = _opslag(medarbejder, kunde)
    k = {"client_id": kunde.id}
    assert [r["konto"] for r in o.udfoer("get_kontoplan", k)["raekker"]] == [3217, 2210, 14260, 14240]
    saldo = o.udfoer("get_saldo", {**k, "konto": 3217, **Q3})
    assert (saldo["navn"], saldo["bevaegelse_i_perioden"], saldo["antal_posteringer"]) == ("Arbejdstøj", "1000.00", 2)
    poster = o.udfoer("get_posteringer", {**k, "konto": 3217, **Q3, "limit": 50})
    assert [r["bilag"] for r in poster["raekker"]] == [101, 102] and poster["sum_alle"] == "1000.00"
    deb = o.udfoer("get_aabne_debitorer", k)
    assert [(r["nummer"], r["navn"], r["rest"]) for r in deb["raekker"]] == [(7, "Jens Hansen", "500.00")]
    kred = o.udfoer("get_aabne_kreditorer", k)
    assert [(r["navn"], r["rest"]) for r in kred["raekker"]] == [("Grossisten A/S", "-80.00")]
    moms = o.udfoer("get_moms", {**k, **Q3})
    assert [(r["konto"], r["momskoder"], r["bevaegelse_i_perioden"]) for r in moms["raekker"]] == [
        (14240, ["I25"], "1000.00"), (14260, ["U25"], "-2500.00")]
    assert "IKKE en momsangivelse" in moms["bemaerk"]
    info = o.udfoer("get_kundeinfo", k)
    assert (info["navn"], info["cvr"], info["regnskabssystem"]) == ("Opslag Tømrer ApS", "12345678", "e-conomic")
    # Samme periode hentes kun én gang pr. svar.
    assert provider.kald.count(("periode", date(2026, 7, 1), date(2026, 9, 30))) == 1
    assert [x["funktion"] for x in o.lookups] == list(op.NAVNE)


# 31. Et kunde-id fra modellen bruges aldrig ukontrolleret
def test_31_fremmed_client_id_fra_modellen_afvises(db_session, samme_session, provider, medarbejder, kunde):
    anden = Client(navn="Anden ApS", kundenummer="OPS-2", regnskabssystem="economic", status="aktiv",
                   ansvarlig_medarbejder_id=medarbejder.id)
    db_session.add(anden)
    db_session.flush()
    o = _opslag(medarbejder, kunde)
    svar = o.udfoer("get_kontoplan", {"client_id": anden.id})
    assert "Afvist" in svar["fejl"] and provider.kald == []
    assert o.lookups[0]["kunde_id"] == anden.id and "Afvist" in o.lookups[0]["fejl"]
    # Mister medarbejderen ansvaret undervejs, afvises næste opslag.
    kunde.ansvarlig_medarbejder_id = None
    db_session.flush()
    assert "Afvist" in o.udfoer("get_kontoplan", {"client_id": kunde.id})["fejl"]
    assert provider.kald == []


# 33. Alle opslag er læsende – der sendes kun GET til e-conomic
def test_33_alle_opslag_er_laesende(db_session, samme_session, medarbejder, kunde, token, monkeypatch):
    metoder = []

    def e_conomic(request):
        metoder.append(request.method)
        sti = request.url.path
        if sti == "/accounting-years":
            data = [{"year": "2026", "fromDate": "2026-01-01", "toDate": "2026-12-31", "closed": False}]
        elif sti == "/accounts":
            data = [{"accountNumber": 3217, "name": "Arbejdstøj"}, {"accountNumber": 14260, "name": "Salgsmoms"}]
        elif sti == "/vat-accounts":
            data = [{"vatCode": "U25", "name": "Salgsmoms", "ratePercentage": 25, "account": {"accountNumber": 14260}}]
        elif sti.endswith("/entries"):
            assert request.url.params.get("filter") in (None, "remainder$ne:0",
                                                        "date$gte:2026-07-01$and:date$lte:2026-09-30")
            data = [{"entryNumber": 1, "date": "2026-08-01", "account": {"accountNumber": 3217}, "amount": 10,
                     "amountInBaseCurrency": 10, "currency": "DKK", "remainder": 10,
                     "customer": {"customerNumber": 1}, "invoiceNumber": 4711}]  # tal – som i e-conomic
        else:
            data = []
        return httpx.Response(200, json={"collection": data, "pagination": {"results": len(data)}})

    def ny_adapter(session, client_id):
        return EconomicAdapter(EconomicKlient(HemmeligtToken("app-" + token), HemmeligtToken(token),
                                              transport=httpx.MockTransport(e_conomic)))

    monkeypatch.setattr(op, "hent_adapter", ny_adapter)
    o = _opslag(medarbejder, kunde)
    for navn in op.NAVNE:
        argumenter = {"client_id": kunde.id, "konto": 3217, **Q3}
        if navn in ("get_kontoplan", "get_aabne_debitorer", "get_aabne_kreditorer", "get_kundeinfo"):
            argumenter = {"client_id": kunde.id}
        elif navn == "get_moms":
            argumenter = {"client_id": kunde.id, **Q3}
        elif navn == "get_posteringer":
            argumenter["limit"] = 10
        assert "fejl" not in o.udfoer(navn, argumenter), navn
    assert metoder and set(metoder) == {"GET"}
    # Modellen får kun de syv faste, læsende opslag – intet, der kan skrive.
    assert set(op.NAVNE) == {"get_kontoplan", "get_saldo", "get_posteringer", "get_aabne_debitorer",
                             "get_aabne_kreditorer", "get_moms", "get_kundeinfo"}


# 34. Over ASSISTANT_MAX_ROWS -> afkortes, og det siges
def test_34_for_mange_raekker_afkortes(db_session, samme_session, medarbejder, kunde, monkeypatch):
    p = FalskProvider([_post(i, 3217, "1", dato="2026-08-01") for i in range(1, 251)])
    monkeypatch.setattr(op, "hent_adapter", lambda session, client_id: p)
    o = _opslag(medarbejder, kunde)
    svar = o.udfoer("get_posteringer", {"client_id": kunde.id, "konto": 3217, **Q3, "limit": 1000})
    assert len(svar["raekker"]) == 200 and svar["antal_i_alt"] == 250 and svar["afkortet"] is True
    assert "afkortet" in svar["besked"] and svar["sum_alle"] == "250.00"
    assert o.afkortet is True and o.lookups[0]["antal_raekker"] == 200


# 36. Personoplysninger filtreres fra, før data når modellen
def test_36_cpr_kontonumre_og_loentekster_skjules(db_session, samme_session, provider, medarbejder, kunde):
    o = _opslag(medarbejder, kunde)
    loen = o.udfoer("get_posteringer", {"client_id": kunde.id, "konto": 2210, **Q3, "limit": 10})
    assert loen["raekker"][0]["tekst"] == "[tekst skjult – lønkonto]" and loen["raekker"][0]["beloeb"] == "30000.00"
    assert "Mette" not in json.dumps(loen) and "010190" not in json.dumps(loen)
    assert op.skjul_tekst("Udlæg 010190-1234 til konto 1234 5678901234") == \
        "Udlæg [CPR skjult] til konto [kontonr. skjult]"
    assert op.skjul_tekst("IBAN DK50 0040 0440 1162 43") == "IBAN [kontonr. skjult]"
    assert op.skjul_tekst("Faktura 4711 på 1.250,00 kr.") == "Faktura 4711 på 1.250,00 kr."
    assert op.skjul_tekst(4711) == "4711"


# --- Værktøjsløkken med en falsk model --------------------------------------------


def _blok(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda **_: dict(kw)
    return b


def _tekst(t):
    return _blok(type="text", text=t)


def _kald(nr, navn, **input_):
    return _blok(type="tool_use", id=f"kald{nr}", name=navn, input=input_)


class FalskModel:
    """Svarer med de faste svar i rækkefølge. `kald` gemmer, hvad modellen fik hvert gang."""

    def __init__(self, svar):
        self.svar = list(svar)
        self.kald = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **kw):
        self.kald.append({**kw, "messages": list(kw["messages"])})
        if self.svar:
            indhold, stop = self.svar.pop(0)
        else:
            indhold, stop = [_kald(99, "get_kontoplan", client_id=0)], "tool_use"
        model = self

        class Stroem:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            @property
            def text_stream(self):
                async def gen():
                    for b in indhold:
                        if b.type == "text":
                            yield b.text
                return gen()

            async def get_final_message(self):
                return SimpleNamespace(model="testmodel", content=indhold, stop_reason=stop,
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=5))

        model.sidste = Stroem()
        return model.sidste


def _koer(model, opslag, monkeypatch, beskeder=None):
    import asyncio

    monkeypatch.setattr(sm, "_klient", lambda: model)
    svar = sm.Svar()

    async def alt():
        return [s async for s in sm.stream_svar(beskeder or [{"role": "user", "content": "Hej"}], svar, "sys",
                                                 opslag=opslag)]

    return svar, "".join(asyncio.run(alt()))


# 35. Over ASSISTANT_MAX_TOOL_CALLS -> stopper og siger det
def test_35_loft_for_antal_opslag(db_session, samme_session, provider, medarbejder, kunde, monkeypatch):
    model = FalskModel([([_kald(i, "get_kontoplan", client_id=kunde.id)], "tool_use") for i in range(5)]
                       + [([_kald(5, "get_kontoplan", client_id=kunde.id)], "tool_use"),
                          ([_tekst("Her er svaret.")], "end_turn")])
    o = _opslag(medarbejder, kunde)
    svar, tekst = _koer(model, o, monkeypatch)
    assert len(o.lookups) == 5  # det sjette blev ikke udført
    assert model.kald[5]["tool_choice"] == {"type": "none"}
    assert "Her er svaret." in tekst and "højeste antal opslag i kundens regnskab (5)" in tekst
    sjette = model.kald[6]["messages"][-1]["content"][0]
    assert sjette["is_error"] is True and "Loftet på 5 opslag" in sjette["content"]
    assert svar.lookups is o.lookups and svar.tokens_ind == 70


def test_34_afkortning_siges_i_svaret(db_session, samme_session, medarbejder, kunde, monkeypatch):
    p = FalskProvider([_post(i, 3217, "1", dato="2026-08-01") for i in range(1, 251)])
    monkeypatch.setattr(op, "hent_adapter", lambda session, client_id: p)
    model = FalskModel([([_kald(1, "get_posteringer", client_id=kunde.id, konto=3217, limit=500, **Q3)],
                         "tool_use"), ([_tekst("Der er mange posteringer.")], "end_turn")])
    svar, tekst = _koer(model, _opslag(medarbejder, kunde), monkeypatch)
    assert "listen er afkortet" in tekst and "(200)" in tekst
    resultat = json.loads(model.kald[1]["messages"][-1]["content"][0]["content"])
    assert resultat["afkortet"] is True and len(resultat["raekker"]) == 200


# 38. Samtale uden kunde -> ingen opslagsfunktioner
def test_38_uden_kunde_ingen_opslag(monkeypatch):
    model = FalskModel([([_tekst("Generelt svar.")], "end_turn")])
    svar, tekst = _koer(model, None, monkeypatch)
    assert tekst == "Generelt svar." and "tools" not in model.kald[0] and svar.lookups == []


# 37. Hvert opslag logges i lookups – hele vejen gennem API'et
def test_37_opslag_gemmes_paa_svaret(db_session, samme_session, provider, medarbejder, kunde, monkeypatch):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: medarbejder
    monkeypatch.setattr(sm, "slaaet_til", lambda: True)
    try:
        k = TestClient(app)
        s = k.post("/api/v1/assistant/conversations", headers=H, json={"kunde_id": kunde.id}).json()
        model = FalskModel([([_tekst("Jeg slår op."), _kald(1, "get_saldo", client_id=kunde.id, konto=3217, **Q3)],
                             "tool_use"),
                            ([_tekst("Konto 3217 (Arbejdstøj): 1.000,00 kr. i 3. kvartal.")], "end_turn")])
        monkeypatch.setattr(sm, "_klient", lambda: model)
        r = k.post(f"/api/v1/assistant/conversations/{s['id']}/messages", headers=H, json={"tekst": "Arbejdstøj?"})
        assert r.status_code == 200
        assert [v["name"] for v in model.kald[0]["tools"]] == list(op.NAVNE)
        besked = db_session.scalars(select(AssistantMessage).where(
            AssistantMessage.conversation_id == s["id"], AssistantMessage.role == "assistant")).one()
        assert [(x["funktion"], x["kunde_id"], x["argumenter"]["konto"], x["antal_raekker"]) for x in besked.lookups] \
            == [("get_saldo", kunde.id, 3217, 1)]
        assert "1.000,00 kr." in besked.content and besked.status == "complete"
        # Trin 14: det, Svend slog op, vises under svaret – både lige efter svaret og ved genindlæsning.
        slut = [json.loads(x[6:]) for x in r.text.split("\n\n") if x.startswith("data: ")][-1]
        forventet = ["Saldo · konto 3217 · 01.07.2026–30.09.2026 · 1 række"]
        assert slut["type"] == "slut" and slut["opslag"] == forventet
        beskeder = k.get(f"/api/v1/assistant/conversations/{s['id']}/messages").json()
        assert beskeder[-1]["opslag"] == forventet and beskeder[0]["opslag"] == []
    finally:
        app.dependency_overrides.clear()


def test_fejl_i_opslag_giver_kort_dansk_besked_uden_detaljer(db_session, samme_session, medarbejder, kunde,
                                                            token, monkeypatch):
    def fejler(session, client_id):
        raise RuntimeError(f"teknisk fejl med {token}")

    monkeypatch.setattr(op, "hent_adapter", fejler)
    o = _opslag(medarbejder, kunde)
    svar = o.udfoer("get_kontoplan", {"client_id": kunde.id})
    assert svar == {"fejl": "Opslaget i kundens regnskabssystem mislykkedes. Sig det til medarbejderen."}
    assert token not in json.dumps(o.lookups)
    # Uden token i hub'en (rigtig adapter, ingen adgang gemt) siges det ligeud.
    from app.adaptere.regnskab import base

    monkeypatch.setattr(op, "hent_adapter", base.hent_adapter)
    assert "ingen aktiv adgang" in o.udfoer("get_kontoplan", {"client_id": kunde.id})["fejl"]


def test_proevekommandoen(db_session, samme_session, provider, medarbejder, kunde, monkeypatch, capsys):
    from app.assistent import opslag_proeve

    monkeypatch.setattr(opslag_proeve, "ny_session", samme_session)
    assert opslag_proeve.sidste_kvartal(date(2026, 10, 18)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert opslag_proeve.main(["--kundenummer", "OPS-1", "--ansvarlig", medarbejder.email]) == 0
    ud = capsys.readouterr().out
    assert "7 af 7 opslag lykkedes" in ud and "14260 Salgsmoms (U25): -2500.00 kr." in ud
    assert opslag_proeve.main(["--kundenummer", "FINDES-IKKE", "--ansvarlig", medarbejder.email]) == 2


# Trin 14: svar med kundetal
def test_14_beskrivelse_af_opslag():
    assert sm.beskriv_opslag({"funktion": "get_moms", "argumenter": {"fra": "2026-07-01", "til": "2026-09-30"},
                              "antal_raekker": 4}) == "Momskonti · 01.07.2026–30.09.2026 · 4 rækker"
    assert sm.beskriv_opslag({"funktion": "get_kontoplan", "argumenter": {}, "antal_raekker": 200,
                              "afkortet": True}) == "Kontoplan · 200 rækker (afkortet)"
    assert sm.beskriv_opslag({"funktion": "get_kontoplan", "argumenter": {}, "antal_raekker": 0,
                              "fejl": "Afvist: du kan kun slå op hos samtalens kunde"}) == \
        "Kontoplan · ikke udført: Afvist: du kan kun slå op hos samtalens kunde"


def test_14_svarets_opbygning_i_instruksen(kunde):
    tekst = sm.kundetekst(kunde)
    foerst, regel, vurdering = (tekst.index("Først hvad du slog op"), tekst.index("Så reglen"),
                                tekst.index("hvem der skal vurdere"))
    assert foerst < regel < vurdering
    assert "Jeg har set på konto 3217 (Arbejdstøj)" in tekst and "konkluderer stadig ikke" in tekst


def test_14_proeven_kontrollerer_svarets_opbygning(db_session, samme_session, provider, medarbejder, kunde,
                                                   monkeypatch, capsys):
    import asyncio

    from app.assistent import proeve

    monkeypatch.setattr(proeve, "ny_session", samme_session)
    monkeypatch.setattr(proeve, "_materiale", lambda tekst: [])
    godt = ("Jeg har set på konto 14260 (Salgsmoms) for perioden: der er bogført -2.500,00 kr.\n\n"
            "Reglen er, at salgsmomsen skal stemme med omsætningen.\n\n"
            "Om tallene kan indberettes, skal den kundeansvarlige vurdere.")
    model = FalskModel([([_kald(1, "get_moms", client_id=kunde.id, **Q3)], "tool_use"), ([_tekst(godt)], "end_turn")])
    monkeypatch.setattr(sm, "_klient", lambda: model)
    assert asyncio.run(proeve._kundeproeve("OPS-1", medarbejder.email)) == [True, True, True, True]
    assert "Slået op: Momskonti · 01.07.2026–30.09.2026 · 2 rækker" in capsys.readouterr().out
    daarligt = "Ja, du skal indberette -2.500 kr. i salgsmoms."
    model = FalskModel([([_tekst(daarligt)], "end_turn")])
    monkeypatch.setattr(sm, "_klient", lambda: model)
    assert asyncio.run(proeve._kundeproeve("OPS-1", medarbejder.email)) == [False, False, False, False]
