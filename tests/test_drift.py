"""Drift: /health, Sentry uden hemmeligheder, backup og en afprøvet gendannelse,
og at serveropsætningen (Docker Compose + Caddy) ikke åbner mere end nødvendigt."""

import json
import os
import shutil
import secrets
import subprocess
from pathlib import Path

import pytest
import sentry_sdk
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

import app.api.main as api
from app.config import get_settings
from app.fejlrapport import init_fejlrapport, rens_haendelse, sentry_indstillinger
from app.sikkerhed.hemmeligheder import registrer_hemmelighed
from app.sikkerhed.kryptering import kan_dekrypteres, krypter_token

ROD = Path(__file__).resolve().parent.parent


# --- /health ----------------------------------------------------------------------


def test_health_svarer_ok_med_databaseversion(db_session):
    head = ScriptDirectory.from_config(Config(str(ROD / "alembic.ini"))).get_current_head()
    svar = TestClient(api.app).get("/health")
    assert svar.status_code == 200
    assert svar.json() == {"status": "ok", "database": "ok", "databaseversion": head}


def test_health_uden_database_giver_503_uden_detaljer(monkeypatch):
    class DoedEngine:
        def connect(self):
            raise RuntimeError("forbindelse til db:5432 afvist, adgangskode=hemmelig")

    monkeypatch.setattr(api, "get_engine", lambda: DoedEngine())
    svar = TestClient(api.app).get("/health")
    assert svar.status_code == 503
    assert svar.json() == {"status": "fejl", "database": "svarer ikke"}
    assert "hemmelig" not in svar.text and "5432" not in svar.text


# --- Sentry -------------------------------------------------------------------------


def test_sentry_er_slaaet_fra_uden_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    get_settings.cache_clear()
    try:
        assert init_fejlrapport("test") is False
    finally:
        get_settings.cache_clear()


def test_sentry_sender_aldrig_lokale_variabler_eller_personoplysninger():
    s = sentry_indstillinger("worker")
    assert s["include_local_variables"] is False
    assert s["send_default_pii"] is False
    assert s["max_request_body_size"] == "never"
    assert s["before_send"] is rens_haendelse and s["before_breadcrumb"] is rens_haendelse


def test_rens_haendelse_maskerer_tokens_overalt(token):
    registrer_hemmelighed(token)
    haendelse = {
        "message": f"kald fejlede med {token}",
        "exception": {"values": [{"value": f"401 for {token}", "stacktrace": {"frames": [
            {"vars": {"t": token}}]}}]},
        "breadcrumbs": {"values": [{"message": f"GET ?x={token}"}]},
        "extra": {"liste": [token, (token,)]},
    }
    renset = rens_haendelse(haendelse)
    assert token not in json.dumps(renset, default=str)
    assert "****" in renset["message"]


def test_en_rigtig_sentry_rapport_indeholder_hverken_token_eller_lokale_variabler(token):
    """Hele vejen gennem sentry_sdk: fang det, der ville blive sendt, og søg efter tokenet."""
    sendt: list[str] = []

    class FangTransport(sentry_sdk.transport.Transport):
        def capture_envelope(self, envelope):
            sendt.append(envelope.serialize().decode("utf-8", errors="replace"))

    registrer_hemmelighed(token)
    indstillinger = sentry_indstillinger("test")
    indstillinger.update(dsn="https://offentlig@example.invalid/1", transport=FangTransport)
    sentry_sdk.init(**indstillinger)
    try:
        def kald_e_conomic():
            agreement_grant_token = token  # noqa: F841 – lokal variabel, der IKKE må sendes
            raise RuntimeError(f"e-conomic afviste nøglen {token}")

        try:
            kald_e_conomic()
        except RuntimeError as fejl:
            sentry_sdk.capture_exception(fejl)
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init(dsn=None)

    assert sendt, "Sentry skulle have lavet en rapport"
    rapport = "\n".join(sendt)
    assert "e-conomic afviste n" in rapport  # (ø skrives som \\u00f8 i rapporten)
    assert token not in rapport
    # Lokale variabler (og dermed deres værdier) kommer ikke med i stakken. Kodelinjen selv
    # (fx variabelnavnet) må gerne – det er kildekode, ikke data.
    assert '"vars"' not in rapport


# --- Tokens efter gendannelse --------------------------------------------------------


def test_kan_dekrypteres_svarer_kun_ja_eller_nej(token):
    krypteret = krypter_token(token)
    assert kan_dekrypteres(krypteret) is True
    assert kan_dekrypteres("ødelagt") is False
    anden_noegle = Fernet(Fernet.generate_key()).encrypt(b"x").decode()
    assert kan_dekrypteres(anden_noegle) is False


# --- Backup og gendannelse (rigtig runde mod den lokale database) ---------------------


def _lokal_pg_miljoe(tmp_path: Path) -> dict:
    url = make_url(get_settings().database_url.get_secret_value())
    envfil = tmp_path / "env"
    envfil.write_text(
        f"POSTGRES_USER={url.username}\nPOSTGRES_DB={url.database}\n"
        f"DATABASE_URL={url.render_as_string(hide_password=False)}\n"
    )
    noegle = tmp_path / "noegle"
    noegle.write_text(secrets.token_urlsafe(48))
    miljoe = dict(os.environ)
    if (ROD / ".env").exists():
        # Testene kører med en midlertidig hovednøgle; tokens i den lokale database er
        # krypteret med den rigtige fra .env – brug den til gendannelsens token-kontrol.
        miljoe.pop("CREDENTIALS_KEY", None)
    return {
        **miljoe,
        "LOKAL_PG": "1", "ENV_FIL": str(envfil), "BACKUP_MAPPE": str(tmp_path / "backups"),
        "BACKUP_NOEGLEFIL": str(noegle), "GENDAN_DB": f"gendan_test_{secrets.token_hex(4)}",
        "PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432),
        "PGPASSWORD": url.password or "",
    }


@pytest.fixture
def lokal_pg(tmp_path, db_session):
    if not all(shutil.which(v) for v in ("pg_dump", "pg_restore", "psql", "createdb", "gpg")):
        pytest.skip("PostgreSQL-værktøjer eller gpg mangler")
    if not db_session.scalar(text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")):
        pytest.skip("Databasebrugeren må ikke oprette databaser (kræves af gendannelsestesten)")
    return _lokal_pg_miljoe(tmp_path)


def _koer(script, miljoe, *args):
    return subprocess.run([str(ROD / "scripts" / script), *args], env=miljoe, cwd=ROD,
                          capture_output=True, text=True, timeout=300)


def test_backup_og_afproevet_gendannelse(lokal_pg):
    backup = _koer("backup.sh", lokal_pg)
    assert backup.returncode == 0, backup.stderr
    filer = list(Path(lokal_pg["BACKUP_MAPPE"]).glob("afstemning-*.dump.gpg"))
    assert len(filer) == 1
    # Krypteret: ingen tabelnavne eller data kan læses direkte i filen.
    indhold = filer[0].read_bytes()
    assert b"credentials" not in indhold and b"CREATE TABLE" not in indhold
    assert oct(filer[0].stat().st_mode & 0o777) == "0o600"

    gendan = _koer("gendan_test.sh", lokal_pg)
    assert gendan.returncode == 0, gendan.stdout + gendan.stderr
    assert "GENDANNELSE OK" in gendan.stdout
    assert "tokens kan læses med CREDENTIALS_KEY" in gendan.stdout
    log = (Path(lokal_pg["BACKUP_MAPPE"]) / "gendannelser.log").read_text()
    assert "GENDANNELSE OK" in log


def test_gendannelse_med_forkert_noegle_fejler(lokal_pg, tmp_path):
    assert _koer("backup.sh", lokal_pg).returncode == 0
    forkert = tmp_path / "forkert"
    forkert.write_text(secrets.token_urlsafe(48))
    gendan = _koer("gendan_test.sh", {**lokal_pg, "BACKUP_NOEGLEFIL": str(forkert)})
    assert gendan.returncode == 1
    assert "GENDANNELSE FEJLET" in gendan.stdout


def test_gendannelse_med_forkert_credentials_key_fejler(lokal_pg):
    assert _koer("backup.sh", lokal_pg).returncode == 0
    gendan = _koer("gendan_test.sh", {**lokal_pg, "CREDENTIALS_KEY": Fernet.generate_key().decode()})
    if "0 af 0 tokens" in gendan.stdout:
        pytest.skip("Ingen tokens i den lokale database at kontrollere")
    assert gendan.returncode == 1 and "GENDANNELSE FEJLET" in gendan.stdout


def test_backup_uden_noeglefil_stopper_med_forklaring(lokal_pg):
    resultat = _koer("backup.sh", {**lokal_pg, "BACKUP_NOEGLEFIL": "/findes/ikke"})
    assert resultat.returncode == 2 and "backup-nøglen" in resultat.stderr


# --- Serveropsætningen ---------------------------------------------------------------


def test_databasen_har_ingen_aaben_port_paa_serveren():
    compose = yaml.safe_load((ROD / "docker-compose.prod.yml").read_text())
    tjenester = compose["services"]
    assert {"db", "migrate", "api", "worker", "scheduler", "caddy"} <= set(tjenester)
    aabne = {navn for navn, t in tjenester.items() if t.get("ports")}
    assert aabne == {"caddy"}, "Kun Caddy må have åbne porte (80/443)"
    # Databasen og Caddy får kun de værdier, de skal bruge – ikke hele .env med tokens.
    assert "env_file" not in tjenester["db"] and "env_file" not in tjenester["caddy"]


def test_caddy_beskytter_alt_undtagen_health():
    caddy = (ROD / "deploy" / "Caddyfile").read_text()
    assert "handle /health" in caddy
    assert "basic_auth" in caddy
    # basic_auth ligger i den generelle handle-blok, ikke i /health-blokken.
    health_blok = caddy.split("handle /health", 1)[1].split("}", 1)[0]
    assert "basic_auth" not in health_blok


def test_docker_image_faar_aldrig_env_filen():
    ignorer = (ROD / ".dockerignore").read_text().splitlines()
    assert ".env" in ignorer and ".env.*" in ignorer


def test_scheduler_planlaegger_og_koerer_ingen_job(monkeypatch):
    from app.jobs.worker import Worker

    w = Worker(navn="test-scheduler", planlaeg=True)
    kald = []

    def planlaeg():
        kald.append("planlagt")
        w._stop.set()  # stop efter første runde

    monkeypatch.setattr(w, "_koer_planlaegger", planlaeg)
    monkeypatch.setattr(w, "koer_et_job", lambda *a, **k: pytest.fail("scheduler må ikke køre job"))
    w.koer_scheduler(interval=0.01)
    assert kald == ["planlagt"]


# --- Sentry: følsomme felter, miljø, webdel og jobs -----------------------------------


def test_foelsomme_felter_fjernes_uanset_hvor_de_ligger():
    haendelse = {
        "request": {"headers": {"Authorization": "Bearer abc123", "X-AgreementGrantToken": "kunde-tok",
                                "X-AppSecretToken": "app-hemmelig", "Accept": "application/json"},
                    "cookies": {"session": "s"}},
        "extra": {"niveau1": {"niveau2": [{"api_key": "k1", "password": "p1", "ok": "synlig"}]}},
        "breadcrumbs": {"values": [{"data": {"client_secret": "cs", "url": "https://x"}}]},
        "contexts": {"headers_som_par": [["authorization", "Basic xyz"], ["user-agent", "httpx"]]},
    }
    renset = rens_haendelse(haendelse)
    tekst = json.dumps(renset)
    for hemmelighed in ("abc123", "kunde-tok", "app-hemmelig", "k1", "p1", "cs", "xyz", '"s"'):
        assert hemmelighed not in tekst, hemmelighed
    assert renset["request"]["headers"]["Accept"] == "application/json"
    assert renset["extra"]["niveau1"]["niveau2"][0]["ok"] == "synlig"
    assert renset["contexts"]["headers_som_par"][1] == ["user-agent", "httpx"]
    assert renset["request"]["headers"]["Authorization"] == "[fjernet]"


@pytest.mark.parametrize("app_env, forventet", [
    ("production", "production"), ("PROD", "production"),
    ("development", "development"), ("", "development"), ("test", "development"),
])
def test_environment_er_production_eller_development(monkeypatch, app_env, forventet):
    monkeypatch.setenv("APP_ENV", app_env)
    get_settings.cache_clear()
    try:
        assert sentry_indstillinger("api")["environment"] == forventet
    finally:
        get_settings.cache_clear()


def test_api_bruger_fastapi_integrationen_og_worker_goer_ikke():
    navne = {type(i).__name__ for i in sentry_indstillinger("api")["integrations"]}
    assert {"FastApiIntegration", "StarletteIntegration"} <= navne
    assert sentry_indstillinger("worker")["integrations"] == []


@pytest.fixture
def fang_sentry():
    """Slå Sentry til med en transport, der fanger rapporterne i stedet for at sende dem."""
    sendt: list[dict] = []

    class FangTransport(sentry_sdk.transport.Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    sendt.append(json.loads(item.payload.get_bytes()))

    def start(komponent):
        indstillinger = sentry_indstillinger(komponent)
        indstillinger.update(dsn="https://offentlig@example.invalid/1", transport=FangTransport)
        sentry_sdk.init(**indstillinger)
        return sendt

    yield start
    sentry_sdk.flush()
    sentry_sdk.get_client().close()
    sentry_sdk.init(dsn=None)


def test_debug_boom_lander_i_sentry_uden_authorization_header(fang_sentry):
    sendt = fang_sentry("api")
    klient = TestClient(api.app, raise_server_exceptions=False)
    svar = klient.get("/debug/boom", headers={"Authorization": "Basic hemmelig-base64"})
    sentry_sdk.flush()
    assert svar.status_code == 500
    assert len(sendt) == 1
    haendelse = sendt[0]
    assert haendelse["exception"]["values"][-1]["value"] == "Test af Sentry: /debug/boom"
    assert "hemmelig-base64" not in json.dumps(haendelse)


def test_fejl_i_et_job_lander_i_sentry_med_job_tags(fang_sentry, db_session, token):
    from app.jobs.koe import laeg_i_koe
    from app.jobs.register import afregistrer, jobtype
    from app.jobs.worker import Worker
    from app.db import ny_session

    @jobtype("test_sentry_boom")
    def boom(job):
        raise RuntimeError(f"jobbet fejlede – kundens nøgle {token} må ikke med")

    registrer_hemmelighed(token)
    with ny_session() as s:
        client_id = s.scalar(text("SELECT min(id) FROM clients"))
        job_id = laeg_i_koe(s, "test_sentry_boom", client_id=client_id).job_id
        s.commit()
    sendt = fang_sentry("worker")
    try:
        Worker(kun_typer=["test_sentry_boom"]).koer_et_job(bestemt_id=job_id)
        sentry_sdk.flush()
    finally:
        afregistrer("test_sentry_boom")
        with ny_session() as s:
            s.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
            s.commit()

    assert len(sendt) == 1, [h.get("message") or h.get("exception") for h in sendt]
    tags = sendt[0]["tags"]
    assert tags["job_id"] == str(job_id)
    assert tags["job_type"] == "test_sentry_boom"
    assert tags["client_id"] == (str(client_id) if client_id is not None else "ingen")
    assert token not in json.dumps(sendt[0])


def test_udskudt_job_sendes_ikke_til_sentry(fang_sentry):
    from app.jobs.koe import laeg_i_koe
    from app.jobs.register import UdskydJob, afregistrer, jobtype
    from app.jobs.worker import Worker
    from app.db import ny_session

    @jobtype("test_sentry_venter")
    def venter(job):
        raise UdskydJob(30, "for mange kald")

    with ny_session() as s:
        job_id = laeg_i_koe(s, "test_sentry_venter").job_id
        s.commit()
    sendt = fang_sentry("worker")
    try:
        Worker(kun_typer=["test_sentry_venter"]).koer_et_job(bestemt_id=job_id)
        sentry_sdk.flush()
    finally:
        afregistrer("test_sentry_venter")
        with ny_session() as s:
            s.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
            s.commit()
    assert sendt == []  # rate limit er ikke en fejl
