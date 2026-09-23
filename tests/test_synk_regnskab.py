"""Synkronisering fra regnskabssystem til cache-tabeller – testet med en SIMULERET adapter.

Beviser især:
1. Samme post hentet to gange giver kun én række.
2. Bogmærket (cursor) rykkes ikke ved fejl.
3. Et rate limit-svar fører til nyt forsøg – ikke til fejl.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from tenacity import wait_none

from app.adaptere.economic.adapter import (
    EconomicAdapter,
    kod_id,
    oversaet_aaben_post,
    oversaet_kunde,
    oversaet_leverandoer,
    oversaet_postering,
)
from app.adaptere.economic.klient import EconomicFejl, EconomicKlient
from app.adaptere.regnskab import base
from app.adaptere.regnskab.base import (
    AabenPost,
    AdapterFejl,
    ForMangeKald,
    Kunde,
    Leverandoer,
    Postering,
    PosteringsSvar,
)
from app.jobs.register import JobKontekst, UdskydJob, hent_funktion
from app.kunder.models import Client, Credential
from app.regnskab.models import CustomerCache, EntryCache, OpenEntryCache, SupplierCache
from app.sikkerhed.hemmeligheder import HemmeligtToken
from app.synk.planlaegger import planlaeg_dag
from app.synk.ressourcer import synk_customers, synk_entries, synk_open_entries, synk_suppliers
from app.synk.tilstand import KundeIkkeAktiv, hent_cursor, hent_tilstand

APP = Path(__file__).resolve().parent.parent / "app"


# --- Simuleret adapter ------------------------------------------------------


def _post(nr, tekst="Salg", beloeb="100.00", modpart=None):
    return Postering(nr, 1000 + nr, date(2026, 9, 1), 1010, tekst, Decimal(beloeb), modpart, "DKK",
                     "financeVoucher")


def _aaben(nr, partnummer=1, rest="50.00", type_="debitor"):
    return AabenPost(nr, type_, partnummer, None, 2000 + nr, f"F{nr}", date(2026, 9, 1),
                     date(2026, 9, 15), Decimal("100.00"), Decimal(rest), "DKK")


class SimuleretAdapter:
    """Opfører sig som en rigtig adapter, men uden netværk."""

    system = "economic"

    def __init__(self, kunder=(), leverandoerer=(), poster=(), aabne=(), fejl=None,
                 ignorer_cursor=False):
        self.kunder, self.leverandoerer = list(kunder), list(leverandoerer)
        self.poster, self.aabne = list(poster), list(aabne)
        self.fejl = fejl or {}
        self.ignorer_cursor = ignorer_cursor
        self.kaldt_med_cursor = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def _maaske_fejl(self, metode):
        if metode in self.fejl:
            raise self.fejl[metode]

    def hent_customers(self):
        self._maaske_fejl("hent_customers")
        return self.kunder

    def hent_suppliers(self):
        self._maaske_fejl("hent_suppliers")
        return self.leverandoerer

    def hent_entries(self, efter):
        self._maaske_fejl("hent_entries")
        self.kaldt_med_cursor.append(efter)
        nye = [p for p in self.poster
               if self.ignorer_cursor or efter is None or p.bogfoert_id > int(efter)]
        hoejeste = max((p.bogfoert_id for p in nye), default=None)
        ny = str(hoejeste) if hoejeste is not None else efter
        return PosteringsSvar(nye, ny, "id" if ny else None)

    def hent_open_entries(self):
        self._maaske_fejl("hent_open_entries")
        return self.aabne


def _kunde(session, nr, status="aktiv", med_adgang=True, token="t"):
    k = Client(navn=f"Regnskab {nr}", kundenummer=nr, regnskabssystem="economic", status=status)
    session.add(k)
    session.flush()
    if med_adgang:
        c = Credential(client_id=k.id, system="economic")
        c.saet_token(token + nr)
        session.add(c)
    session.commit()
    return k


def _antal(session, model, client_id):
    return session.scalar(select(func.count()).select_from(model).where(model.client_id == client_id))


# --- 1. Samme post to gange giver én række ----------------------------------


def test_samme_post_hentet_to_gange_giver_en_raekke(db_session):
    k = _kunde(db_session, "R-1")
    adapter = SimuleretAdapter(poster=[_post(1), _post(2)], ignorer_cursor=True)
    synk_entries(db_session, k.id, adapter)
    adapter.poster = [_post(1, tekst="Rettet tekst"), _post(2)]
    synk_entries(db_session, k.id, adapter)

    assert _antal(db_session, EntryCache, k.id) == 2
    assert db_session.scalar(select(EntryCache.tekst).where(
        EntryCache.client_id == k.id, EntryCache.bogfoert_id == 1)) == "Rettet tekst"


def test_samme_post_to_gange_i_samme_svar_giver_en_raekke(db_session):
    k = _kunde(db_session, "R-2")
    synk_entries(db_session, k.id, SimuleretAdapter(poster=[_post(1), _post(1, tekst="nyest")]))
    assert _antal(db_session, EntryCache, k.id) == 1


@pytest.mark.parametrize("synk, model, adapter", [
    (synk_customers, CustomerCache,
     lambda: SimuleretAdapter(kunder=[Kunde(1, "Kunde A", "12345678", 1, False, Decimal("10"))])),
    (synk_suppliers, SupplierCache,
     lambda: SimuleretAdapter(leverandoerer=[Leverandoer(7, "Lev B", None, 2, None)])),
    (synk_open_entries, OpenEntryCache, lambda: SimuleretAdapter(aabne=[_aaben(5)])),
])
def test_ingen_dubletter_i_nogen_cache_tabel(db_session, synk, model, adapter):
    k = _kunde(db_session, f"D-{model.__tablename__}")
    synk(db_session, k.id, adapter())
    synk(db_session, k.id, adapter())
    assert _antal(db_session, model, k.id) == 1


def test_databasen_afviser_dublet_direkte(db_session):
    k = _kunde(db_session, "R-3")
    sql = text("INSERT INTO entries (client_id, bogfoert_id) VALUES (:k, 42)")
    db_session.execute(sql, {"k": k.id})
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(sql, {"k": k.id})


# --- 2. Cursor rykkes ikke ved fejl -----------------------------------------


def test_entries_hentes_inkrementelt_fra_cursor(db_session):
    k = _kunde(db_session, "I-1")
    adapter = SimuleretAdapter(poster=[_post(1), _post(2)])
    synk_entries(db_session, k.id, adapter)
    adapter.poster.append(_post(3))
    resultat = synk_entries(db_session, k.id, adapter)

    assert adapter.kaldt_med_cursor == [None, "2"]
    assert resultat.antal == 1
    assert hent_cursor(db_session, k.id, "entries").vaerdi == "3"


def test_cursor_rykkes_ikke_hvis_adapteren_fejler(db_session):
    k = _kunde(db_session, "C-1")
    adapter = SimuleretAdapter(poster=[_post(1)])
    synk_entries(db_session, k.id, adapter)
    adapter.poster.append(_post(2))
    adapter.fejl["hent_entries"] = AdapterFejl("e-conomic svarede 500")

    with pytest.raises(AdapterFejl):
        synk_entries(db_session, k.id, adapter)

    assert hent_cursor(db_session, k.id, "entries").vaerdi == "1"
    t = hent_tilstand(db_session, k.id, "entries")
    assert (t.status, t.antal_fejl_i_traek) == ("forsinket", 1)


def test_cursor_rykkes_ikke_hvis_skrivningen_fejler(db_session):
    k = _kunde(db_session, "C-2")
    synk_entries(db_session, k.id, SimuleretAdapter(poster=[_post(1)]))
    ugyldig = Postering(2, None, None, None, None, None, None, None, "ukendt_type")
    adapter = SimuleretAdapter(poster=[_post(1), _post(3), ugyldig])

    with pytest.raises(IntegrityError):
        synk_entries(db_session, k.id, adapter)

    assert hent_cursor(db_session, k.id, "entries").vaerdi == "1"
    assert _antal(db_session, EntryCache, k.id) == 1  # post 3 blev heller ikke gemt


# --- 3. Rate limit fører til nyt forsøg, ikke fejl --------------------------


def _job(session, client_id, type_):
    return JobKontekst(job_id=1, type=type_, client_id=client_id, payload={}, forsoeg=1,
                       max_forsoeg=5, session=session)


def test_rate_limit_udskyder_jobbet_og_taeller_ikke_som_fejl(db_session, monkeypatch):
    k = _kunde(db_session, "L-1")
    adapter = SimuleretAdapter(fejl={"hent_entries": ForMangeKald("for mange kald", 42)})
    monkeypatch.setitem(base._FABRIKKER, "economic", lambda s, c: adapter)

    with pytest.raises(UdskydJob) as udskyd:
        hent_funktion("synk_entries")(_job(db_session, k.id, "synk_entries"))

    assert udskyd.value.sekunder == 42
    t = hent_tilstand(db_session, k.id, "entries")
    assert (t.status, t.antal_fejl_i_traek) == ("ok", 0)

    # Næste forsøg lykkes.
    del adapter.fejl["hent_entries"]
    adapter.poster = [_post(1)]
    hent_funktion("synk_entries")(_job(db_session, k.id, "synk_entries"))
    assert hent_cursor(db_session, k.id, "entries").vaerdi == "1"


def test_klienten_proever_igen_efter_429_med_retry_after(token):
    kald = []

    def e_conomic(request):
        kald.append(request)
        if len(kald) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "for mange"})
        return httpx.Response(200, json={"collection": [{"customerNumber": 1, "name": "A"}],
                                         "pagination": {"results": 1}})

    klient = EconomicKlient(HemmeligtToken("app-" + token), HemmeligtToken(token),
                            transport=httpx.MockTransport(e_conomic))
    assert [k.kundenummer for k in EconomicAdapter(klient).hent_customers()] == [1]
    assert len(kald) == 2


def test_vedvarende_429_bliver_til_for_mange_kald_ikke_fejl(token):
    svar = httpx.Response(429, headers={"Retry-After": "30"}, json={})
    klient = EconomicKlient(HemmeligtToken("app-" + token), HemmeligtToken(token),
                            transport=httpx.MockTransport(lambda r: svar), vent=wait_none())
    with pytest.raises(ForMangeKald) as fejl:
        EconomicAdapter(klient).hent_customers()
    assert fejl.value.vent_sekunder == 30


# --- Åbne poster: altid fuldt -----------------------------------------------


def test_aabne_poster_hentes_fuldt_betalte_fjernes_og_partnavn_udfyldes(db_session):
    k = _kunde(db_session, "A-1")
    synk_customers(db_session, k.id, SimuleretAdapter(
        kunder=[Kunde(1, "Kunde Et", None, None, None, None)]))
    synk_suppliers(db_session, k.id, SimuleretAdapter(
        leverandoerer=[Leverandoer(9, "Leverandør Ni", None, None, None)]))

    synk_open_entries(db_session, k.id, SimuleretAdapter(
        aabne=[_aaben(1), _aaben(2, rest="100.00"), _aaben(3, partnummer=9, type_="kreditor")]))
    # Post 1 er betalt, post 2 er delvist betalt siden sidst.
    r = synk_open_entries(db_session, k.id, SimuleretAdapter(
        aabne=[_aaben(2, rest="25.00"), _aaben(3, partnummer=9, type_="kreditor")]))

    assert (r.antal, r.fjernet) == (2, 1)
    raekker = {p.bogfoert_id: p for p in db_session.scalars(
        select(OpenEntryCache).where(OpenEntryCache.client_id == k.id))}
    assert set(raekker) == {2, 3}
    assert raekker[2].restbeloeb == Decimal("25.00")
    assert (raekker[2].partnavn, raekker[3].partnavn) == ("Kunde Et", "Leverandør Ni")
    assert hent_cursor(db_session, k.id, "open_entries").vaerdi is None


# --- Kunder på pause/opsagt og fejl hos én kunde ----------------------------


@pytest.mark.parametrize("status", ["pause", "opsagt"])
def test_pause_og_opsagt_kommer_aldrig_med(db_session, status):
    k = _kunde(db_session, f"S-{status}", status=status)
    with pytest.raises(KundeIkkeAktiv):
        synk_customers(db_session, k.id, SimuleretAdapter())
    planlaeg_dag(db_session)
    assert db_session.scalar(select(func.count()).select_from(text("jobs")).where(
        text("client_id = :k")).params(k=k.id)) == 0


def test_job_for_kunde_sat_paa_pause_springes_over(db_session, monkeypatch):
    k = _kunde(db_session, "S-2")
    k.status = "pause"
    db_session.commit()
    monkeypatch.setitem(base._FABRIKKER, "economic", lambda s, c: pytest.fail("må ikke kaldes"))
    hent_funktion("synk_customers")(_job(db_session, k.id, "synk_customers"))  # ingen fejl


def test_fejl_hos_en_kunde_paavirker_ikke_andre(db_session):
    a, b = _kunde(db_session, "F-A"), _kunde(db_session, "F-B")
    for k in (a, b):
        synk_entries(db_session, k.id, SimuleretAdapter(poster=[_post(1)]))

    with pytest.raises(AdapterFejl):
        synk_entries(db_session, a.id, SimuleretAdapter(fejl={"hent_entries": AdapterFejl("nede")}))
    synk_entries(db_session, b.id, SimuleretAdapter(poster=[_post(1), _post(2)]))

    ta, tb = hent_tilstand(db_session, a.id, "entries"), hent_tilstand(db_session, b.id, "entries")
    assert (ta.status, ta.cursor) == ("forsinket", "1")
    assert (tb.status, tb.cursor, tb.antal_fejl_i_traek) == ("ok", "2", 0)
    assert (_antal(db_session, EntryCache, a.id), _antal(db_session, EntryCache, b.id)) == (1, 2)


# --- Planlæggeren -----------------------------------------------------------


def _planlagte(session, kunder):
    return session.execute(text(
        "SELECT client_id, type, planlagt_til, idempotens_noegle FROM jobs "
        "WHERE client_id = ANY(:k) ORDER BY planlagt_til"), {"k": [k.id for k in kunder]}).all()


def test_planlaegger_fordeler_jaevnt_og_er_idempotent(db_session):
    kunder = [_kunde(db_session, f"P-{i}") for i in range(3)]
    _kunde(db_session, "P-uden-adgang", med_adgang=False)

    foerste = planlaeg_dag(db_session)
    job = _planlagte(db_session, kunder)
    assert len(job) == 3 * 4
    afstande = {(b.planlagt_til - a.planlagt_til) for a, b in zip(job, job[1:])}
    assert len(afstande) == 1 and min(afstande) >= timedelta(seconds=10)  # helt jævnt
    assert job[0].type == "synk_customers" and job[3].type == "synk_open_entries"
    assert re.fullmatch(rf"customers:{kunder[0].id}:\d{{4}}-\d{{2}}-\d{{2}}", job[0].idempotens_noegle)

    anden = planlaeg_dag(db_session)
    assert anden.nye_job == 0 and anden.fandtes == foerste.nye_job + foerste.fandtes
    assert len(_planlagte(db_session, kunder)) == 12


def test_planlaegger_springer_slaaet_fra_ressourcer_over(db_session):
    from app.synk.tilstand import saet_deaktiveret

    k = _kunde(db_session, "P-X")
    saet_deaktiveret(db_session, k.id, "entries", True)
    planlaeg_dag(db_session)
    assert {j.type for j in _planlagte(db_session, [k])} == {
        "synk_customers", "synk_suppliers", "synk_open_entries"}


def test_planlaegger_for_i_morgen_bruger_hele_doegnet(db_session):
    k = _kunde(db_session, "P-M")
    i_morgen = db_session.scalar(select(func.current_date())) + timedelta(days=1)
    planlaeg_dag(db_session, i_morgen)
    job = _planlagte(db_session, [k])
    assert len(job) == 4


# --- e-conomic-oversættelsen (gætter aldrig) --------------------------------


def test_kunde_uden_valgfrie_felter_giver_none():
    k = oversaet_kunde({"customerNumber": 5, "name": "Kun navn"})
    assert (k.cvr, k.betalingsbetingelse, k.spaerret, k.saldo) == (None, None, None, None)


def test_kunde_med_alle_felter():
    k = oversaet_kunde({"customerNumber": 5, "name": "A", "corporateIdentificationNumber": "12345678",
                        "paymentTerms": {"paymentTermsNumber": 3}, "barred": True, "balance": 99.5})
    assert (k.cvr, k.betalingsbetingelse, k.spaerret, k.saldo) == ("12345678", 3, True, Decimal("99.5"))


def test_leverandoer_saldo_er_altid_tom():
    assert oversaet_leverandoer({"supplierNumber": 1, "name": "L", "balance": 1}).saldo is None


def test_manglende_id_afvises_i_stedet_for_at_gaette():
    with pytest.raises(EconomicFejl, match="entryNumber"):
        oversaet_postering({"amount": 10})
    with pytest.raises(EconomicFejl, match="customerNumber"):
        oversaet_kunde({"name": "Uden nummer"})


def test_postering_og_aaben_post_oversaettes():
    d = {"entryNumber": 77, "voucherNumber": 12, "date": "2026-09-01", "dueDate": "2026-09-15",
         "account": {"accountNumber": 5820}, "text": "Faktura 5", "amount": 1250.0, "currency": "DKK",
         "entryType": "customerInvoice", "customer": {"customerNumber": 3}, "invoiceNumber": "5",
         "remainder": 250.0}
    p = oversaet_postering(d)
    assert (p.bogfoert_id, p.kontonummer, p.modpart, p.beloeb) == (77, 5820, "debitor:3", Decimal("1250.0"))
    a = oversaet_aaben_post(d)
    assert (a.type, a.partnummer, a.fakturanummer, a.restbeloeb, a.partnavn) == (
        "debitor", 3, "5", Decimal("250.0"), None)


def test_regnskabsaar_kodes_som_e_conomic_forventer():
    assert kod_id("2025/2026") == "2025_6_2026"
    assert kod_id("2026") == "2026"


def test_e_conomic_adapter_bruger_filter_og_alle_regnskabsaar(token):
    kald = []

    def e_conomic(request):
        kald.append((request.url.path, request.url.params.get("filter")))
        if request.url.path == "/accounting-years":
            return httpx.Response(200, json={"collection": [{"year": "2025/2026"}, {"year": "2027"}],
                                             "pagination": {"results": 2}})
        nr = 11 if "2025" in request.url.path else 12
        post = {"entryNumber": nr, "amount": 1, "customer": {"customerNumber": 1}, "remainder": 1}
        return httpx.Response(200, json={"collection": [post], "pagination": {"results": 1}})

    klient = EconomicKlient(HemmeligtToken("app-" + token), HemmeligtToken(token),
                            transport=httpx.MockTransport(e_conomic))
    svar = EconomicAdapter(klient).hent_entries("10")
    assert (svar.ny_cursor, svar.cursor_type, len(svar.poster)) == ("12", "id", 2)
    assert ("/accounting-years/2025_6_2026/entries", "entryNumber$gt:10") in kald
    assert ("/accounting-years/2027/entries", "entryNumber$gt:10") in kald

    kald.clear()
    assert len(EconomicAdapter(klient).hent_open_entries()) == 2
    assert all(f == "remainder$ne:0" for sti, f in kald if sti.endswith("/entries"))


# --- Arkitektur: kun adapter-laget taler med e-conomic ----------------------


def test_kun_adapter_laget_bruger_e_conomic_direkte():
    brud = []
    for fil in APP.rglob("*.py"):
        rel = str(fil.relative_to(APP.parent))
        if rel.startswith("app/adaptere/") or rel == "app/config.py":
            continue
        indhold = fil.read_text()
        if re.search(r"(from|import)\s+app\.adaptere\.economic", indhold) or "e-conomic.com" in indhold:
            brud.append(rel)
    assert not brud, f"Kalder e-conomic uden om adapter-laget: {brud}"
