"""Posteringer (entries) fra e-conomic hele vejen ind i databasen.

Rigtig PostgreSQL, men e-conomic er SIMULERET (httpx.MockTransport) – der
sendes aldrig noget ud på nettet. Beviser:
- samme poster to gange giver ingen dubletter (upsert),
- bogmærket (cursor) gemmes og bruges næste gang,
- fejler hentningen midtvejs, beholdes det, der sikkert nåede at komme,
- 429 med Retry-After fører til nyt forsøg, ikke til fejl,
- beløb gemmes som NUMERIC (Decimal), aldrig som kommatal,
- der sendes KUN GET til e-conomic, og tokens havner aldrig i rå data, logs eller fejl.
"""

import ast
import logging
import secrets
import time
from contextlib import contextmanager
from decimal import Decimal
from functools import partial
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import Text, cast, func, select, text
from tenacity import wait_none

import app.cli
from app.adaptere.economic.adapter import lav_economic_adapter
from app.adaptere.economic.klient import EconomicFejl
from app.adaptere.regnskab import base
from app.adaptere.regnskab.base import ForMangeKald
from app.config import get_settings
from app.jobs.register import JobKontekst, UdskydJob, hent_funktion
from app.kunder.models import Client, Credential
from app.regnskab.models import EntryCache
from app.synk.ressourcer import synk_entries
from app.synk.tilstand import hent_cursor, hent_tilstand, nulstil_cursor

ECONOMIC_MAPPE = Path(__file__).resolve().parent.parent / "app" / "adaptere" / "economic"
BASE = "https://restapi.e-conomic.com"


def _entry(nr: int, aar: str = "2026", beloeb=100, **ekstra) -> dict:
    """Én postering, formet som det rigtige svar fra demo-aftalen."""
    return {
        "account": {"accountNumber": 1010, "self": f"{BASE}/accounts/1010"},
        "amount": beloeb,
        "amountInBaseCurrency": beloeb,
        "currency": "DKK",
        "date": f"{aar[:4]}-03-01",
        "entryNumber": nr,
        "text": f"Postering {nr}",
        "entryType": "financeVoucher",
        "voucherNumber": 1000 + nr,
        "self": f"{BASE}/entries/{nr}",
        **ekstra,
    }


class FalskEconomic:
    """Svarer som e-conomic: regnskabsår, sider, filter `entryNumber$gt:` og sort."""

    def __init__(self, poster: dict[str, list[dict]], side_stoerrelse: int = 1000):
        self.poster = poster  # regnskabsår -> poster
        self.side_stoerrelse = side_stoerrelse
        self.kald: list[httpx.Request] = []
        self.fejl_paa: dict[tuple[str, int], httpx.Response] = {}  # (år, side) -> svar
        self.foerste_svar: list[httpx.Response] = []  # sendes før alt andet (fx 429)
        self.ignorer_sort = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.kald.append(request)
        if self.foerste_svar:
            return self.foerste_svar.pop(0)
        sti = request.url.path
        if sti == "/accounting-years":
            aar = [{"year": a.replace("_6_", "/")} for a in self.poster]
            return httpx.Response(200, json={"collection": aar, "pagination": {"results": len(aar)}})
        aar = sti.split("/")[2]
        p = request.url.params
        raekker = list(self.poster[aar])
        if (f := p.get("filter")) and f.startswith("entryNumber$gt:"):
            raekker = [r for r in raekker if r["entryNumber"] > int(f.split(":")[1])]
        if p.get("sort") == "entryNumber" and not self.ignorer_sort:
            raekker.sort(key=lambda r: r["entryNumber"])
        side, stoerrelse = int(p.get("skipPages", 0)), min(int(p.get("pageSize", 20)), self.side_stoerrelse)
        if (aar, side) in self.fejl_paa:
            return self.fejl_paa[(aar, side)]
        start = side * stoerrelse
        naeste = None
        if start + stoerrelse < len(raekker):
            naeste = f"{BASE}{sti}?" + urlencode({**dict(p), "skipPages": side + 1, "pageSize": stoerrelse})
        return httpx.Response(200, json={
            "collection": raekker[start:start + stoerrelse],
            "pagination": {"results": len(raekker), "pageSize": stoerrelse, "skipPages": side,
                           **({"nextPage": naeste} if naeste else {})},
        })


@pytest.fixture
def app_secret(monkeypatch) -> str:
    vaerdi = "test-app-" + secrets.token_urlsafe(24)
    monkeypatch.setenv("ECONOMIC_APP_SECRET_TOKEN", vaerdi)
    monkeypatch.setenv("ECONOMIC_API_BASE_URL", BASE)
    get_settings.cache_clear()
    yield vaerdi
    get_settings.cache_clear()


@pytest.fixture
def kunde(db_session, token, app_secret):
    k = Client(navn="Posteringer ApS", kundenummer="EN-1", regnskabssystem="economic")
    db_session.add(k)
    db_session.flush()
    adgang = Credential(client_id=k.id, system="economic")
    adgang.saet_token(token)
    db_session.add(adgang)
    db_session.commit()
    return k


@pytest.fixture
def economic(monkeypatch):
    """Brug den RIGTIGE e-conomic-adapter, men mod den simulerede e-conomic."""
    def lav(poster, side_stoerrelse=1000, vent=wait_none()):
        falsk = FalskEconomic(poster, side_stoerrelse)
        monkeypatch.setitem(base._FABRIKKER, "economic", partial(
            lav_economic_adapter, transport=httpx.MockTransport(falsk), vent=vent))
        return falsk
    return lav


def _antal(session, client_id) -> int:
    return session.scalar(select(func.count()).select_from(EntryCache).where(EntryCache.client_id == client_id))


# --- Upsert og bogmærke -------------------------------------------------------


def test_upsert_er_idempotent(db_session, kunde, economic):
    economic({"2026": [_entry(1), _entry(2), _entry(3)]})
    foerste = synk_entries(db_session, kunde.id)

    nulstil_cursor(db_session, kunde.id, "entries")  # tving en fuld hentning af det samme igen
    anden = synk_entries(db_session, kunde.id)

    assert (foerste.antal, foerste.nye, foerste.opdaterede) == (3, 3, 0)
    assert (anden.antal, anden.nye, anden.opdaterede) == (3, 0, 3)
    assert _antal(db_session, kunde.id) == 3


def test_cursor_gemmes(db_session, kunde, economic):
    falsk = economic({"2025_6_2026": [_entry(4, "2025/2026")], "2026": [_entry(7), _entry(5)]})
    resultat = synk_entries(db_session, kunde.id)
    assert resultat.cursor == "7"
    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "7"

    falsk.poster["2026"].append(_entry(8))
    falsk.kald.clear()
    naeste = synk_entries(db_session, kunde.id)

    filtre = {r.url.params.get("filter") for r in falsk.kald if r.url.path.endswith("/entries")}
    assert filtre == {"entryNumber$gt:7"}
    assert (naeste.antal, naeste.nye, naeste.cursor) == (1, 1, "8")
    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "8"


# --- Fejl midtvejs ------------------------------------------------------------


def test_fejl_midtvejs_beholder_delvis_cursor(db_session, kunde, economic):
    """Side 1 af det sidste regnskabsår kommer; side 2 fejler (500).
    Side 1's poster gemmes, og bogmærket flyttes til den sidste post på side 1."""
    falsk = economic({"2025": [_entry(1, "2025"), _entry(2, "2025")],
                      "2026": [_entry(n) for n in range(3, 9)]}, side_stoerrelse=2)
    falsk.fejl_paa[("2026", 1)] = httpx.Response(500, json={"message": "fejl"})

    with pytest.raises(EconomicFejl, match="500"):
        synk_entries(db_session, kunde.id)

    gemt = db_session.scalars(select(EntryCache.bogfoert_id).where(EntryCache.client_id == kunde.id)
                              .order_by(EntryCache.bogfoert_id)).all()
    assert gemt == [1, 2, 3, 4]
    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "4"
    t = hent_tilstand(db_session, kunde.id, "entries")
    assert (t.status, t.antal_fejl_i_traek) == ("forsinket", 1)

    # Næste kørsel fortsætter fra 4 og får resten – uden dubletter.
    del falsk.fejl_paa[("2026", 1)]
    resultat = synk_entries(db_session, kunde.id)
    assert (resultat.nye, resultat.cursor) == (4, "8")
    assert _antal(db_session, kunde.id) == 8
    assert hent_tilstand(db_session, kunde.id, "entries").status == "ok"


def test_fejl_i_et_tidligere_aar_flytter_ikke_cursor(db_session, kunde, economic):
    """Fejler et ÆLDRE år, kan et senere år have lavere numre: bogmærket bliver stående."""
    falsk = economic({"2025": [_entry(n, "2025") for n in (1, 2, 9)], "2026": [_entry(3)]},
                     side_stoerrelse=2)
    falsk.fejl_paa[("2025", 1)] = httpx.Response(503, json={})

    with pytest.raises(EconomicFejl):
        synk_entries(db_session, kunde.id)

    assert _antal(db_session, kunde.id) == 2  # det hentede er gemt …
    assert hent_cursor(db_session, kunde.id, "entries").vaerdi is None  # … men bogmærket står stille


def test_usorteret_svar_flytter_ikke_cursor(db_session, kunde, economic):
    """Ignorerer e-conomic sorteringen, ved vi ikke, hvad der mangler: bogmærket står stille."""
    falsk = economic({"2026": [_entry(n) for n in (5, 3, 9, 4)]}, side_stoerrelse=2)
    falsk.ignorer_sort = True
    falsk.fejl_paa[("2026", 1)] = httpx.Response(500, json={})

    with pytest.raises(EconomicFejl):
        synk_entries(db_session, kunde.id)
    assert _antal(db_session, kunde.id) == 2
    assert hent_cursor(db_session, kunde.id, "entries").vaerdi is None


def test_rate_limit_midtvejs_gemmer_det_hentede_og_udskyder_jobbet(db_session, kunde, economic):
    falsk = economic({"2026": [_entry(n) for n in range(1, 5)]}, side_stoerrelse=2)
    falsk.fejl_paa[("2026", 1)] = httpx.Response(429, headers={"Retry-After": "600"}, json={})
    job = JobKontekst(job_id=1, type="synk_entries", client_id=kunde.id, payload={}, forsoeg=1,
                      max_forsoeg=5, session=db_session)

    with pytest.raises(UdskydJob):
        hent_funktion("synk_entries")(job)

    assert hent_cursor(db_session, kunde.id, "entries").vaerdi == "2"
    t = hent_tilstand(db_session, kunde.id, "entries")
    assert (t.status, t.antal_fejl_i_traek) == ("ok", 0)  # rate limit er ikke en fejl


# --- 429 og beløb ---------------------------------------------------------------


def test_429_bliver_til_retry(db_session, kunde, economic):
    # Rigtig ventetid (ingen wait_none): klienten skal vente det, e-conomic beder om.
    falsk = economic({"2026": [_entry(1)]}, vent=None)
    falsk.foerste_svar.append(httpx.Response(429, headers={"Retry-After": "1"}, json={}))

    start = time.monotonic()
    resultat = synk_entries(db_session, kunde.id)

    assert time.monotonic() - start >= 1
    assert resultat.nye == 1
    assert [r.url.path for r in falsk.kald[:2]] == ["/accounting-years", "/accounting-years"]
    assert hent_tilstand(db_session, kunde.id, "entries").antal_fejl_i_traek == 0


def test_vedvarende_429_giver_for_mange_kald(db_session, kunde, economic):
    falsk = economic({"2026": [_entry(1)]})
    falsk.foerste_svar.extend([httpx.Response(429, headers={"Retry-After": "1"}, json={})] * 5)
    with pytest.raises(ForMangeKald):
        synk_entries(db_session, kunde.id)
    assert len(falsk.kald) == 5  # højst 5 forsøg


def test_beloeb_er_numeric(db_session, kunde, economic):
    economic({"2026": [_entry(1, beloeb=1234.56), _entry(2, beloeb=0.1)]})
    synk_entries(db_session, kunde.id)

    beloeb = db_session.scalar(select(EntryCache.beloeb).where(
        EntryCache.client_id == kunde.id, EntryCache.bogfoert_id == 1))
    assert isinstance(beloeb, Decimal)
    assert beloeb == Decimal("1234.56")
    assert db_session.scalar(select(EntryCache.beloeb_dkk).where(
        EntryCache.client_id == kunde.id, EntryCache.bogfoert_id == 2)) == Decimal("0.10")
    typer = db_session.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'entries' AND column_name IN ('beloeb', 'beloeb_dkk')")).all()
    assert {t for _, t in typer} == {"numeric"}


# --- Kun GET – aldrig skrive til e-conomic --------------------------------------


SKRIVE_METODER = {"post", "put", "patch", "delete", "request", "stream", "send"}


def test_economic_modulet_indeholder_kun_get():
    """Fejler, hvis noget i e-conomic-mappen kan sende andet end GET."""
    brud = []
    for fil in ECONOMIC_MAPPE.rglob("*.py"):
        for node in ast.walk(ast.parse(fil.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr.lower() in SKRIVE_METODER):
                brud.append(f"{fil.name}:{node.lineno} .{node.func.attr}(")
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.strip() in {"POST", "PUT", "PATCH", "DELETE"}):
                brud.append(f"{fil.name}:{node.lineno} '{node.value}'")
    assert not brud, "Kun GET er tilladt mod e-conomic:\n" + "\n".join(brud)


def test_get_testen_fanger_et_skrivekald(tmp_path):
    """Sikrer, at testen ovenfor faktisk virker (den må ikke altid bestå)."""
    for kode in ('self._http.post("/x")', 'httpx.request("DELETE", url)', 'self._http.put(u)'):
        traeet = ast.parse(kode)
        fundet = [n for n in ast.walk(traeet)
                  if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr.lower() in SKRIVE_METODER)
                  or (isinstance(n, ast.Constant) and str(n.value).upper() == "DELETE")]
        assert fundet, kode


def test_en_hel_synkronisering_sender_kun_get(db_session, kunde, economic):
    falsk = economic({"2026": [_entry(n) for n in range(1, 6)]}, side_stoerrelse=2)
    synk_entries(db_session, kunde.id)
    assert {r.method for r in falsk.kald} == {"GET"}


# --- Tokens: aldrig i rå data, logs eller fejl ----------------------------------


def test_tokens_er_aldrig_i_raa_data_logs_eller_fejl(db_session, kunde, economic, token, app_secret,
                                                     caplog):
    caplog.set_level(logging.DEBUG)
    falsk = economic({"2026": [_entry(1), _entry(2)]}, side_stoerrelse=1)
    synk_entries(db_session, kunde.id)
    # Nøglerne blev sendt til (den simulerede) e-conomic …
    assert falsk.kald[0].headers["X-AgreementGrantToken"] == token
    # … men står ingen steder i det gemte.
    raa = db_session.scalars(select(cast(EntryCache.raa_data, Text))
                             .where(EntryCache.client_id == kunde.id)).all()
    assert raa and all("entryNumber" in r for r in raa)
    for hemmelig in (token, app_secret):
        assert not any(hemmelig in r for r in raa)

    # En fejl fra e-conomic (401) må heller ikke indeholde nøglerne.
    falsk.foerste_svar.append(httpx.Response(401, json={"message": "nej"}))
    nulstil_cursor(db_session, kunde.id, "entries")
    with pytest.raises(EconomicFejl) as fejl:
        synk_entries(db_session, kunde.id)
    besked = str(fejl.value) + (hent_tilstand(db_session, kunde.id, "entries").sidste_fejl_besked or "")
    for hemmelig in (token, app_secret):
        assert hemmelig not in besked
        assert hemmelig not in caplog.text


# --- Kommandoen python -m app.cli sync-entries ------------------------------------


def test_cli_sync_entries_viser_tal_og_cursor(db_session, kunde, economic, monkeypatch, capsys):
    economic({"2026": [_entry(1), _entry(2), _entry(3)]})

    @contextmanager
    def samme_session():
        yield db_session

    class TestWorker:
        """Kører jobbet i testens session (alt rulles tilbage bagefter)."""

        def koer_et_job(self, bestemt_id):
            raekke = db_session.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": bestemt_id}).one()
            hent_funktion(raekke.type)(JobKontekst(
                job_id=raekke.id, type=raekke.type, client_id=raekke.client_id, payload=raekke.payload,
                forsoeg=1, max_forsoeg=raekke.max_forsoeg, session=db_session))
            db_session.execute(text("UPDATE jobs SET status = 'faerdig', afsluttet = now() WHERE id = :id"),
                               {"id": bestemt_id})

    monkeypatch.setattr(app.cli, "ny_session", samme_session)
    assert app.cli.sync_entries(kunde.id, worker=TestWorker()) == 0
    ud = capsys.readouterr().out
    assert "Hentet:      3" in ud
    assert "Nye:         3" in ud
    assert "Opdaterede:  0" in ud
    assert "Ny cursor:   3" in ud


def test_cli_ukendt_kunde(db_session, monkeypatch, capsys):
    @contextmanager
    def samme_session():
        yield db_session

    monkeypatch.setattr(app.cli, "ny_session", samme_session)
    assert app.cli.sync_entries(-1) == 2
    assert "findes ikke" in capsys.readouterr().err
