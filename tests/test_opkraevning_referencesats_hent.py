"""Referencesatsen hentes automatisk (simuleret Statistikbank – testene taler aldrig med internettet)."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.audit.models import AuditLog
from app.config import get_settings
from app.opkraevning.models import ReferenceRate
from app.opkraevning.nationalbanken import HentFejl, hent_sats, opdater_referencesats

INSTRUMENTER = [{"id": "ODKNAA", "text": "Diskontoen"}, {"id": "OIRNAA", "text": "Udlånsrente"},
                {"id": "OFONAA", "text": "Foliorente"}]


def _tider(*datoer):
    return [{"id": f"{d:%Y}M{d:%m}D{d:%d}", "text": f"{d}"} for d in datoer]


def _bank(instrumenter=INSTRUMENTER, tider=None, vaerdi="1.60", kald=None):
    tider = tider or _tider(date(2026, 6, 29), date(2026, 6, 30), date(2026, 7, 1))

    def svar(request: httpx.Request):
        if kald is not None:
            kald.append(request)
        if request.url.path.endswith("/tableinfo/DNRENTD"):
            return httpx.Response(200, json={"id": "DNRENTD", "variables": [
                {"id": "INSTRUMENT", "text": "instrument", "values": instrumenter},
                {"id": "LAND", "text": "land", "values": [{"id": "DK", "text": "Danmark"}]},
                {"id": "Tid", "text": "tid", "time": True, "values": tider}]})
        if request.url.path.endswith("/data"):
            krop = json.loads(request.content)
            tid = next(v["values"][0] for v in krop["variables"] if v["code"] == "Tid")
            return httpx.Response(200, text=f"INSTRUMENT;LAND;TID;INDHOLD\nUdlånsrente;Danmark;{tid};{vaerdi}\n")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(svar))


def test_satsen_hentes_og_gemmes_med_kilde(db_session):
    kald = []
    r = opdater_referencesats(db_session, date(2026, 7, 1), _bank(kald=kald))
    assert (r.valid_from, r.rate) == (date(2026, 7, 1), Decimal("1.60"))
    assert "INSTRUMENT=OIRNAA (Udlånsrente)" in r.source and "01.07.2026" in r.source
    krop = json.loads(kald[-1].content)
    assert {"code": "INSTRUMENT", "values": ["OIRNAA"]} in krop["variables"]
    assert {"code": "Tid", "values": ["2026M07D01"]} in krop["variables"]
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "referencesats_hentet")).one()
    assert log.detaljer["sats"] == "1.60"


def test_1_januar_er_helligdag_saa_bruges_seneste_dag_foer(db_session):
    bank = _bank(tider=_tider(date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2)))
    s = hent_sats(date(2026, 1, 1), bank)
    assert s.dato_i_kilden == date(2025, 12, 31)


def test_gemt_sats_overskrives_aldrig_og_kilden_kaldes_ikke(db_session):
    db_session.add(ReferenceRate(valid_from=date(2026, 7, 1), rate=Decimal("1.75"), source="hånd"))
    db_session.flush()
    kald = []
    assert opdater_referencesats(db_session, date(2026, 9, 3), _bank(kald=kald)).rate == Decimal("1.7500")
    assert kald == []


@pytest.mark.parametrize("bank, besked", [
    (lambda: _bank(instrumenter=INSTRUMENTER + [{"id": "X", "text": "Udlånsrente, dag-til-dag"}]), "entydigt"),
    (lambda: _bank(instrumenter=[{"id": "ODKNAA", "text": "Diskontoen"}, {"id": "OF", "text": "Foliorente"}]),
     "entydigt"),
    (lambda: _bank(tider=_tider(date(2026, 6, 1))), "ingen værdi"),           # 30 dage gammel
    (lambda: _bank(vaerdi=".."), "ikke med et tal"),
    (lambda: _bank(vaerdi="160"), "ser ikke rigtig ud"),
])
def test_gaetter_aldrig_og_gemmer_intet(db_session, bank, besked):
    with pytest.raises(HentFejl, match=besked):
        opdater_referencesats(db_session, date(2026, 7, 1), bank())
    assert db_session.scalar(select(func.count()).select_from(ReferenceRate)) == 0


def test_serien_kan_vaelges_i_indstillingerne(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "referencesats_valg", "INSTRUMENT=X")
    bank = _bank(instrumenter=INSTRUMENTER + [{"id": "X", "text": "Udlånsrente, dag-til-dag"}])
    assert "INSTRUMENT=X" in hent_sats(date(2026, 7, 1), bank).serie


def test_netvaerksfejl_giver_hentfejl_uden_detaljer():
    def svar(request):
        raise httpx.ConnectError("nede")
    with pytest.raises(HentFejl, match="ConnectError"):
        hent_sats(date(2026, 7, 1), httpx.Client(transport=httpx.MockTransport(svar)))


def test_job_planlaegges_en_gang_om_dagen_og_scheduler_har_tiden(db_session):
    from app.jobs.models import Job
    from app.natkoersel.scheduler import lav_scheduler
    from app.opkraevning.jobs import REFERENCESATS, planlaeg_referencesats

    nu = datetime(2026, 7, 1, 4, 10, tzinfo=timezone.utc)
    assert planlaeg_referencesats(db_session, nu) == 1
    assert planlaeg_referencesats(db_session, nu) == 0
    assert db_session.scalars(select(Job).where(Job.type == REFERENCESATS)).one().payload == {"dag": "2026-07-01"}
    tider = {j.id: str(j.trigger) for j in lav_scheduler().get_jobs()}
    assert "hour='6'" in tider["referencesats"] and "minute='10'" in tider["referencesats"]
