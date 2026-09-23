"""Beviser de tre krav til token-lageret.

1. Det, der står i databasens tokenkolonne, kan ikke læses uden hovednøglen.
2. Dekrypteringen giver præcis det oprindelige token tilbage.
3. Print eller logning af credential-objektet (og token-objektet) afslører ikke tokenet.
"""

import base64
import io
import logging
import traceback

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text

from app.adaptere.adgang import hent_adgang, hent_token
from app.audit.models import AuditLog
from app.kunder.adgange import UgyldigAdgang, gem_token
from app.kunder.gem_token import main as gem_token_main
from app.kunder.models import Client, Credential


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Token ApS", kundenummer="TOK-1", regnskabssystem="dinero")
    db_session.add(k)
    db_session.flush()
    return k


def _raa_kolonne(session, credential_id: int) -> str:
    """Præcis det, der står i databasen – læst uden om programmets modeller."""
    return session.execute(
        text("SELECT token_krypteret FROM credentials WHERE id = :id"), {"id": credential_id}
    ).scalar_one()


# --- 1. Kan ikke læses uden nøglen ------------------------------------------


def test_tokenkolonnen_kan_ikke_laeses_uden_noeglen(db_session, kunde, token):
    credential = gem_token(db_session, kunde.id, "dinero", token)
    raa = _raa_kolonne(db_session, credential.id)

    # Tokenet står hverken direkte eller i simpel omkodning i databasen.
    assert token not in raa
    assert token.encode() not in base64.urlsafe_b64decode(raa)
    # Tilfældige andre nøgler kan ikke låse det op.
    for _ in range(10):
        with pytest.raises(InvalidToken):
            Fernet(Fernet.generate_key()).decrypt(raa.encode())


def test_samme_token_giver_forskellig_tekst_i_databasen(db_session, kunde, token):
    to = Client(navn="Token 2 ApS", kundenummer="TOK-2", regnskabssystem="dinero")
    db_session.add(to)
    db_session.flush()
    a = gem_token(db_session, kunde.id, "dinero", token)
    b = gem_token(db_session, to.id, "dinero", token)
    # Man kan altså ikke se på databasen, at to kunder har samme token.
    assert _raa_kolonne(db_session, a.id) != _raa_kolonne(db_session, b.id)


# --- 2. Dekryptering giver præcis det oprindelige token ----------------------


@pytest.mark.parametrize("variant", ["almindelig", "specialtegn", "langt"])
def test_dekryptering_giver_praecis_det_oprindelige(db_session, kunde, token, variant):
    original = {
        "almindelig": token,
        "specialtegn": token + "-æøå/+=!\"'§",
        "langt": token * 20,
    }[variant]
    gem_token(db_session, kunde.id, "dinero", original)
    db_session.expunge_all()  # tving frisk læsning fra databasen

    assert hent_token(db_session, kunde.id, "dinero").klartekst() == original


def test_nyt_token_erstatter_det_gamle(db_session, kunde, token):
    gem_token(db_session, kunde.id, "dinero", token, organisation_id="111")
    gem_token(db_session, kunde.id, "dinero", token + "-nyt")
    db_session.expunge_all()

    adgang = hent_adgang(db_session, kunde.id, "dinero")
    assert adgang.token.klartekst() == token + "-nyt"
    assert adgang.organisation_id == "111"  # beholdes, når den ikke angives
    adgange = db_session.scalars(select(Credential).where(Credential.client_id == kunde.id)).all()
    assert len(adgange) == 1
    handlinger = db_session.scalars(
        select(AuditLog.handling).where(AuditLog.client_id == kunde.id).order_by(AuditLog.id)
    ).all()
    assert handlinger == ["token_gemt", "token_udskiftet"]


def test_audit_log_indeholder_ikke_tokenet(db_session, kunde, token):
    gem_token(db_session, kunde.id, "dinero", token)
    alt = db_session.execute(text("SELECT detaljer::text FROM audit_log")).scalars().all()
    assert all(token not in (d or "") for d in alt)


@pytest.mark.parametrize("forkert", ["", "   ", " med-mellemrum ", None])
def test_ugyldige_tokens_afvises_uden_at_blive_vist(db_session, kunde, forkert):
    with pytest.raises(UgyldigAdgang) as fejl:
        gem_token(db_session, kunde.id, "dinero", forkert)
    if forkert and forkert.strip():
        assert forkert.strip() not in str(fejl.value)


def test_ukendt_system_og_kunde_afvises(db_session, kunde, token):
    with pytest.raises(UgyldigAdgang, match="Ukendt system"):
        gem_token(db_session, kunde.id, "billy", token)
    with pytest.raises(UgyldigAdgang, match="findes ikke"):
        gem_token(db_session, 999_999, "dinero", token)


# --- 3. Print og logning afslører ikke tokenet ------------------------------


def test_print_af_objekterne_afsloerer_ikke_tokenet(db_session, kunde, token, capsys):
    credential = gem_token(db_session, kunde.id, "dinero", token)
    adgang = hent_adgang(db_session, kunde.id, "dinero")
    hemmeligt = hent_token(db_session, kunde.id, "dinero")

    for objekt in (credential, adgang, hemmeligt):
        print(objekt)
        print(repr(objekt))
        print(f"{objekt} {objekt!r} {objekt!s}")
        print([objekt], {"x": objekt})
    udskrift = capsys.readouterr().out

    assert token not in udskrift
    assert "****" in udskrift


def test_logning_af_objekterne_afsloerer_ikke_tokenet(db_session, kunde, token):
    credential = gem_token(db_session, kunde.id, "dinero", token)
    adgang = hent_adgang(db_session, kunde.id, "dinero")

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    logger = logging.getLogger("test.token_lager")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("credential: %s", credential)
        logger.info("credential: %r", credential)
        logger.info("adgang: %s / token: %s", adgang, adgang.token)
        logger.info(f"f-streng: {credential} {adgang} {adgang.token}")
        logger.info({"credential": credential, "adgang": adgang})
    finally:
        logger.removeHandler(handler)

    assert token not in buffer.getvalue()


def test_exception_og_fejlrapport_afsloerer_ikke_tokenet(db_session, kunde, token):
    gem_token(db_session, kunde.id, "dinero", token)
    adgang = hent_adgang(db_session, kunde.id, "dinero")
    try:
        raise RuntimeError("kald til Dinero fejlede", adgang, adgang.token)
    except RuntimeError as fejl:
        besked = str(fejl) + repr(fejl)
        rapport = "".join(traceback.format_exception(fejl))
    assert token not in besked
    assert token not in rapport


# --- Kommandoen til at lægge et token ind -----------------------------------


def test_kommando_gemmer_token_fra_skjult_input(db_session, kunde, token, monkeypatch, capsys):
    from app.kunder import gem_token as modul

    monkeypatch.setattr(modul, "ny_session", lambda: _GenbrugSession(db_session))
    monkeypatch.setattr(modul.getpass, "getpass", lambda _prompt: token)

    kode = gem_token_main(["--kundenummer", "TOK-1", "--system", "dinero", "--organisation-id", "42"])

    ud = capsys.readouterr()
    assert kode == 0
    assert token not in ud.out + ud.err
    assert hent_token(db_session, kunde.id, "dinero").klartekst() == token


def test_kommando_tager_ikke_token_paa_kommandolinjen(capsys):
    with pytest.raises(SystemExit):
        gem_token_main(["--kundenummer", "TOK-1", "--system", "dinero", "--token", "hemmeligt"])


class _GenbrugSession:
    """Lader kommandoen bruge testens session (som rulles tilbage bagefter)."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *_):
        return False
