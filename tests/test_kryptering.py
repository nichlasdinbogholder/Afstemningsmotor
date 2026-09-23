"""Beviser, at tokens krypteres i databasen og kun kan læses med hovednøglen."""

import base64
import io
import logging
import pickle
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.adaptere.adgang import AdgangMangler, hent_adgang
from app.kunder.models import Client, Credential
from app.sikkerhed.hemmeligheder import HemmeligtToken, maskér
from app.sikkerhed.kryptering import (
    FERNET_PREFIX,
    KrypteringsFejl,
    dekrypter_token_til_adapter,
    krypter_token,
)
from app.sikkerhed.ny_noegle import gem_i_env, main as ny_noegle_main

APP = Path(__file__).resolve().parent.parent / "app"


def _opret_kunde_med_token(session, token: str) -> Credential:
    kunde = Client(navn="Testkunde ApS", kundenummer="TEST-1", system="dinero")
    adgang = Credential(systemnavn="dinero", organisation_id="12345")
    adgang.saet_token(token)
    kunde.credentials.append(adgang)
    session.add(kunde)
    session.flush()
    return adgang


# --- Databasen: det gemte kan ikke læses uden nøglen ------------------------


def test_databasen_indeholder_ikke_tokenet_i_klartekst(db_session, token):
    adgang = _opret_kunde_med_token(db_session, token)

    # Læs præcis det, der ligger i databasen – uden om programmets modeller.
    rå = db_session.execute(
        text("SELECT token_krypteret FROM credentials WHERE id = :id"), {"id": adgang.id}
    ).scalar_one()

    assert rå.startswith(FERNET_PREFIX)
    assert token not in rå
    # Heller ikke skjult i en simpel omkodning (base64).
    assert token.encode() not in base64.urlsafe_b64decode(rå)


def test_det_gemte_kan_ikke_dekrypteres_uden_den_rigtige_noegle(db_session, token):
    adgang = _opret_kunde_med_token(db_session, token)
    rå = db_session.execute(
        text("SELECT token_krypteret FROM credentials WHERE id = :id"), {"id": adgang.id}
    ).scalar_one()

    for _ in range(5):
        forkert_noegle = Fernet(Fernet.generate_key())
        with pytest.raises(InvalidToken):
            forkert_noegle.decrypt(rå.encode())


def test_dekryptering_giver_det_oprindelige_token_tilbage(db_session, token):
    adgang = _opret_kunde_med_token(db_session, token)
    db_session.expunge_all()  # tving en frisk læsning fra databasen

    hentet = hent_adgang(db_session, adgang.client_id, "dinero")

    assert hentet.token.klartekst() == token
    assert hentet.organisation_id == "12345"


def test_samme_token_krypteres_forskelligt_hver_gang(token):
    assert krypter_token(token) != krypter_token(token)


def test_databasen_afviser_ukrypterede_tokens_uden_at_vise_dem(db_session, token):
    kunde = Client(navn="Testkunde ApS", kundenummer="TEST-2", system="dinero")
    db_session.add(kunde)
    db_session.flush()

    with pytest.raises(Exception) as fejl:
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO credentials (client_id, systemnavn, token_krypteret) "
                    "VALUES (:c, 'dinero', :t)"
                ),
                {"c": kunde.id, "t": token},
            )
    assert "skal være krypteret" in str(fejl.value)
    assert token not in str(fejl.value)


def test_manglende_adgang_giver_tydelig_fejl(db_session):
    with pytest.raises(AdgangMangler):
        hent_adgang(db_session, 999_999, "economic")


# --- Tokens må aldrig blive vist -------------------------------------------


def test_token_vises_aldrig_ved_print_repr_eller_fstreng(token, capsys):
    hemmeligt = HemmeligtToken(token)

    print(hemmeligt, repr(hemmeligt), f"{hemmeligt}", str(hemmeligt), [hemmeligt])
    udskrift = capsys.readouterr().out

    assert token not in udskrift
    assert maskér(token) in udskrift


def test_adgang_og_credential_viser_ikke_token(db_session, token):
    adgang = _opret_kunde_med_token(db_session, token)
    hentet = hent_adgang(db_session, adgang.client_id, "dinero")

    assert token not in repr(hentet)
    assert token not in repr(adgang)
    assert adgang.token_krypteret not in repr(adgang)


def test_token_kan_ikke_gemmes_til_fil_med_pickle(token):
    with pytest.raises(TypeError):
        pickle.dumps(HemmeligtToken(token))


def test_logbeskeder_skjuler_tokens_selv_ved_fejlagtig_brug(token):
    hemmeligt = HemmeligtToken(token)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.hemmeligheder")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("objekt: %s", hemmeligt)
        logger.warning("rå værdi ved en fejl: %s", hemmeligt.klartekst())
        logger.error(f"f-streng med rå værdi: {hemmeligt.klartekst()}")
        try:
            raise RuntimeError(f"kald fejlede med token {hemmeligt.klartekst()}")
        except RuntimeError:
            logger.exception("fejl i adapter")
    finally:
        logger.removeHandler(handler)

    log = buffer.getvalue()
    assert token not in log
    assert log.count(maskér(token)) >= 4
    assert "Traceback" in log


def test_ukontrollerede_fejl_skjuler_tokens(token, capsys):
    import sys

    hemmeligt = HemmeligtToken(token)
    try:
        raise ValueError(f"uventet fejl: {hemmeligt.klartekst()}")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    fejltekst = capsys.readouterr().err
    assert token not in fejltekst
    assert maskér(token) in fejltekst


def test_dekrypteringsfejl_viser_hverken_data_eller_noegle():
    fremmed = Fernet(Fernet.generate_key()).encrypt(b"andet-token").decode()
    with pytest.raises(KrypteringsFejl) as fejl:
        dekrypter_token_til_adapter(fremmed)
    besked = str(fejl.value)
    from app.config import get_settings

    assert fremmed not in besked
    assert get_settings().credentials_key.get_secret_value() not in besked
    assert fejl.value.__cause__ is None and fejl.value.__suppress_context__


def test_indstillingerne_viser_ikke_hovednoeglen():
    from app.config import get_settings

    indstillinger = get_settings()
    noegle = indstillinger.credentials_key.get_secret_value()
    assert noegle not in repr(indstillinger)
    assert noegle not in str(indstillinger)


# --- Kun ét sted i koden må dekryptere -------------------------------------


def _python_filer():
    return [p for p in APP.rglob("*.py")]


def test_kun_et_sted_i_koden_dekrypterer():
    steder = [
        (p.relative_to(APP.parent), linje)
        for p in _python_filer()
        for linje in p.read_text().splitlines()
        if re.search(r"\.decrypt\(", linje)
    ]
    assert len(steder) == 1, steder
    assert str(steder[0][0]) == "app/sikkerhed/kryptering.py"


def test_kun_adapter_laget_kalder_dekrypteringen():
    brugere = {
        str(p.relative_to(APP.parent))
        for p in _python_filer()
        if "dekrypter_token_til_adapter" in p.read_text()
    }
    tilladte = {"app/sikkerhed/kryptering.py"}
    uden_for = {b for b in brugere - tilladte if not b.startswith("app/adaptere/")}
    assert not uden_for, f"Dekryptering kaldt uden for adapter-laget: {uden_for}"


# --- Kommandoen til ny hovednøgle ------------------------------------------


def test_ny_noegle_er_gyldig_og_unik(capsys):
    ny_noegle_main([])
    noegle1 = capsys.readouterr().out.strip()
    ny_noegle_main([])
    noegle2 = capsys.readouterr().out.strip()
    Fernet(noegle1.encode())  # fejler, hvis nøglen er ugyldig
    assert noegle1 != noegle2


def test_ny_noegle_gemmes_i_tom_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=noget\nCREDENTIALS_KEY=\n")
    ny_noegle_main(["--gem", "--env-fil", str(env)])
    indhold = env.read_text()
    noegle = re.search(r"^CREDENTIALS_KEY=(.+)$", indhold, re.MULTILINE).group(1)
    Fernet(noegle.encode())
    assert "DATABASE_URL=noget" in indhold
    assert env.stat().st_mode & 0o777 == 0o600


def test_ny_noegle_overskriver_aldrig_en_eksisterende(tmp_path):
    env = tmp_path / ".env"
    gammel = Fernet.generate_key().decode()
    env.write_text(f"CREDENTIALS_KEY={gammel}\n")
    with pytest.raises(SystemExit):
        gem_i_env(env, Fernet.generate_key().decode())
    assert env.read_text() == f"CREDENTIALS_KEY={gammel}\n"
