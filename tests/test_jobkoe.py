"""Beviser, at jobkøen er sikker.

1. To (eller flere) workers kørende samtidig tager aldrig samme job.
2. Et fejlende job prøves igen med stigende ventetid og ender som 'fejlet'.
3. Samme job kan ikke lægges i kø to gange med samme idempotensnøgle.

Testene her skal bruge rigtige, gemte transaktioner (workers ser kun hinandens
låse på tværs af forbindelser). De bruger kun jobtyper, der starter med
'test_', og rydder op efter sig – rigtige job i køen bliver ikke rørt.
"""

import random
import threading
import time
from collections import Counter

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import get_engine, ny_session
from app.jobs.koe import KoeFejl, genkoer, laeg_i_koe
from app.jobs.register import afregistrer, jobtype
from app.jobs.worker import HENT_NAESTE, MARKER_FAERDIG, Worker, ventetid

TEST_TYPER = ["test_taeller", "test_fejler", "test_ok", "test_ukendt", "test_venter"]


def _ryd_op():
    with get_engine().begin() as f:
        f.execute(text("DELETE FROM jobs WHERE type LIKE 'test\\_%'"))


@pytest.fixture
def ren_koe():
    try:
        _ryd_op()
    except OperationalError:
        pytest.skip("PostgreSQL kører ikke – start den med: docker compose up -d")
    yield
    _ryd_op()


@pytest.fixture
def udfoert():
    """Registrér test-jobtyper. Returnerer listen over job-id'er, der blev udført."""
    liste: list[int] = []
    laas = threading.Lock()

    @jobtype("test_taeller")
    def taeller(job):
        time.sleep(random.uniform(0, 0.003))  # gør kapløbet mellem workers mere realistisk
        with laas:
            liste.append(job.job_id)

    @jobtype("test_ok")
    def ok(job):
        with laas:
            liste.append(job.job_id)

    @jobtype("test_fejler")
    def fejler(job):
        # Skriver noget i databasen og fejler bagefter: skrivningen må ikke blive gemt.
        job.session.execute(
            text("UPDATE jobs SET payload = '{\"halvt_arbejde\": true}' WHERE id = :id"),
            {"id": job.job_id},
        )
        raise RuntimeError("bum")

    @jobtype("test_venter")
    def venter(job):
        from app.jobs.register import UdskydJob

        raise UdskydJob(30, "for mange kald")

    yield liste
    for navn in ("test_taeller", "test_ok", "test_fejler", "test_venter"):
        afregistrer(navn)


def _laeg(type_, antal=1, **kw):
    with ny_session() as s:
        ids = [laeg_i_koe(s, type_, **kw).job_id for _ in range(antal)]
        s.commit()
    return ids


def _job(job_id):
    with get_engine().connect() as f:
        return f.execute(
            text("SELECT *, planlagt_til - now() AS om FROM jobs WHERE id = :id"), {"id": job_id}
        ).one()


def _worker(**kw):
    return Worker(kun_typer=TEST_TYPER, pause=0.01, **kw)


# --- 1. To workers tager aldrig samme job ----------------------------------


def test_laast_job_springes_over_af_anden_worker(ren_koe, udfoert):
    foerste, anden = _laeg("test_ok", 2, prioritet=1)

    # Forbindelse A tager første job og holder låsen (transaktionen er ikke afsluttet).
    with get_engine().connect() as a:
        transaktion = a.begin()
        laast = a.execute(HENT_NAESTE, {"typer": TEST_TYPER}).scalar()
        assert laast == foerste

        # Worker B springer det låste job over og tager det næste …
        b = _worker()
        assert b.tag_naeste().id == anden
        # … og finder ikke flere, selv om det første stadig står som 'koe'.
        assert b.tag_naeste() is None
        transaktion.rollback()


def test_mange_workers_samtidig_tager_hvert_job_praecis_en_gang(ren_koe, udfoert):
    ids = _laeg("test_taeller", 300)
    workers = [_worker(navn=f"test-worker-{i}") for i in range(6)]
    traade = [threading.Thread(target=w.koer, kwargs={"stop_naar_tom": True}) for w in workers]
    for t in traade:
        t.start()
    for t in traade:
        t.join(timeout=60)

    taelling = Counter(udfoert)
    assert set(taelling) == set(ids)
    assert max(taelling.values()) == 1, "et job blev udført mere end én gang"
    with get_engine().connect() as f:
        statusser = f.execute(
            text("SELECT status, count(*) FROM jobs WHERE type = 'test_taeller' GROUP BY status")
        ).all()
    assert statusser == [("faerdig", 300)]


# --- 2. Fejlende job prøves igen og ender som fejlet ------------------------


def test_fejlende_job_proeves_igen_med_stigende_ventetid_og_ender_fejlet(ren_koe, udfoert):
    (job_id,) = _laeg("test_fejler", max_forsoeg=4)
    w = _worker(basis_ventetid=10, maks_ventetid=3600)
    ventetider = []

    for forsoeg in range(1, 5):
        assert w.koer_et_job(), f"forsøg {forsoeg} blev ikke taget"
        job = _job(job_id)
        assert job.forsoeg == forsoeg
        assert job.sidste_fejl == "RuntimeError: bum"
        assert job.payload == {}, "et fejlet jobs halve arbejde blev gemt"
        if forsoeg < 4:
            assert job.status == "koe"
            ventetider.append(job.om.total_seconds())
            # Spol frem, så næste forsøg kan tages med det samme.
            with get_engine().begin() as f:
                f.execute(text("UPDATE jobs SET planlagt_til = now() WHERE id = :id"), {"id": job_id})
        else:
            assert job.status == "fejlet"
            assert job.afsluttet is not None

    # Stigende ventetid: ca. 10 s, 20 s, 40 s.
    assert ventetider == sorted(ventetider)
    for faktisk, forventet in zip(ventetider, [10, 20, 40]):
        assert forventet - 2 <= faktisk <= forventet + 1
    # Efter max_forsoeg prøves det ikke igen.
    assert w.koer_et_job() is False
    assert _job(job_id).forsoeg == 4


def test_fejl_i_et_job_stopper_ikke_workeren_eller_andre_job(ren_koe, udfoert):
    (fejler,) = _laeg("test_fejler", prioritet=1)
    ok = _laeg("test_ok", 3, prioritet=2)

    _worker().koer(stop_naar_tom=True)

    assert sorted(udfoert) == sorted(ok)
    assert all(_job(i).status == "faerdig" for i in ok)
    assert _job(fejler).status == "koe"  # venter på næste forsøg


def test_ukendt_jobtype_fejler_med_det_samme(ren_koe):
    with get_engine().begin() as f:
        job_id = f.execute(text(
            "INSERT INTO jobs (type, idempotens_noegle) VALUES ('test_ukendt', 'test:ukendt') RETURNING id"
        )).scalar()
    assert _worker().koer_et_job()
    job = _job(job_id)
    assert (job.status, job.forsoeg) == ("fejlet", 1)
    assert "Ukendt jobtype" in job.sidste_fejl


@pytest.mark.parametrize("forsoeg, forventet", [(1, 30), (2, 60), (3, 120), (7, 1920), (8, 3600), (20, 3600)])
def test_ventetid_fordobles_og_har_et_loft(forsoeg, forventet):
    assert ventetid(forsoeg, basis=30, maks=3600) == forventet


# --- Hængende job frigives --------------------------------------------------


def test_haengende_job_frigives_og_gammel_worker_kan_ikke_afslutte_det(ren_koe, udfoert):
    (job_id,) = _laeg("test_ok")
    doed = _worker(navn="test-doed-worker")
    assert doed.tag_naeste().id == job_id
    with get_engine().begin() as f:  # lad som om, den har stået i gang i en time
        f.execute(text("UPDATE jobs SET laast_tidspunkt = now() - interval '1 hour' WHERE id = :id"),
                  {"id": job_id})

    assert _worker(haengende_efter=600).frigiv_haengende() == 1
    job = _job(job_id)
    assert (job.status, job.laast_af) == ("koe", None)
    assert "stod i gang for længe" in job.sidste_fejl

    # Den "døde" worker kan ikke længere markere jobbet som færdigt.
    with get_engine().begin() as f:
        assert f.execute(MARKER_FAERDIG, {"id": job_id, "worker": doed.navn}).rowcount == 0


def test_haengende_job_uden_flere_forsoeg_bliver_fejlet(ren_koe, udfoert):
    (job_id,) = _laeg("test_ok", max_forsoeg=1)
    _worker().tag_naeste()
    with get_engine().begin() as f:
        f.execute(text("UPDATE jobs SET laast_tidspunkt = now() - interval '1 hour' WHERE id = :id"),
                  {"id": job_id})
    _worker(haengende_efter=600).frigiv_haengende()
    assert _job(job_id).status == "fejlet"


def test_nyligt_startet_job_frigives_ikke(ren_koe, udfoert):
    _laeg("test_ok")
    _worker().tag_naeste()
    assert _worker(haengende_efter=600).frigiv_haengende() == 0


# --- 3. Idempotens ----------------------------------------------------------


def test_samme_idempotensnoegle_laegges_kun_i_koe_en_gang(db_session):
    foerste = laeg_i_koe(db_session, "log_klient", idempotens_noegle="kontoplan:42:2026-09-23")
    anden = laeg_i_koe(db_session, "log_klient", idempotens_noegle="kontoplan:42:2026-09-23")

    assert foerste.ny is True
    assert anden.ny is False
    assert anden.job_id == foerste.job_id
    antal = db_session.scalar(text(
        "SELECT count(*) FROM jobs WHERE idempotens_noegle = 'kontoplan:42:2026-09-23'"
    ))
    assert antal == 1


def test_databasen_afviser_dubleret_idempotensnoegle(db_session):
    sql = text("INSERT INTO jobs (type, idempotens_noegle) VALUES ('log_klient', 'test:dublet')")
    db_session.execute(sql)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(sql)


def test_uden_noegle_faar_hvert_job_sin_egen(db_session):
    a = laeg_i_koe(db_session, "log_klient")
    b = laeg_i_koe(db_session, "log_klient")
    assert a.ny and b.ny and a.job_id != b.job_id


# --- Øvrige regler ----------------------------------------------------------


@pytest.mark.parametrize("payload", [{"token": "x"}, {"api_key": "x"}, {"indre": {"Password": "x"}}])
def test_payload_maa_ikke_indeholde_hemmeligheder(db_session, payload):
    with pytest.raises(KoeFejl, match="må ikke indeholde"):
        laeg_i_koe(db_session, "log_klient", payload=payload)


def test_ukendt_type_kan_ikke_laegges_i_koe(db_session):
    from app.jobs.register import UkendtJobtype

    with pytest.raises(UkendtJobtype):
        laeg_i_koe(db_session, "findes_ikke")


def test_genkoer_fejlet_job(ren_koe, udfoert):
    (job_id,) = _laeg("test_fejler", max_forsoeg=1)
    _worker().koer_et_job()
    assert _job(job_id).status == "fejlet"

    with ny_session() as s:
        genkoer(s, job_id)
        s.commit()
    job = _job(job_id)
    assert (job.status, job.forsoeg) == ("koe", 0)

    with ny_session() as s, pytest.raises(KoeFejl, match="kun fejlede job"):
        genkoer(s, job_id)


def test_eksempeljobbet_logger_client_id(db_session, caplog):
    import logging

    from app.jobs.register import JobKontekst, hent_funktion

    caplog.set_level(logging.INFO)
    hent_funktion("log_klient")(JobKontekst(
        job_id=1, type="log_klient", client_id=40850635, payload={}, forsoeg=1,
        max_forsoeg=5, session=db_session,
    ))
    assert "client_id=40850635" in caplog.text


def test_udskudt_job_taeller_ikke_som_forsoeg_og_ender_aldrig_som_fejlet(ren_koe, udfoert):
    (job_id,) = _laeg("test_venter", max_forsoeg=2)
    w = _worker()
    for _ in range(5):  # flere gange end max_forsoeg
        assert w.koer_et_job()
        job = _job(job_id)
        assert (job.status, job.forsoeg) == ("koe", 0)
        assert 25 <= job.om.total_seconds() <= 31
        assert "for mange kald" in job.sidste_fejl
        with get_engine().begin() as f:
            f.execute(text("UPDATE jobs SET planlagt_til = now() WHERE id = :id"), {"id": job_id})
