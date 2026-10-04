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
