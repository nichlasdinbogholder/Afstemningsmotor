"""Bekræftelses-kommandoen skal sige BEKRÆFTET, når alt virker – og IKKE BEKRÆFTET ellers."""

from decimal import Decimal

from app.adaptere.regnskab.base import AdapterFejl, Kunde, Leverandoer
from app.synk.bekraeft import bekraeft
from tests.test_synk_regnskab import SimuleretAdapter, _aaben, _kunde, _post


def _fabrik(adapter):
    return lambda session, client_id: adapter


def _fuld_adapter():
    return SimuleretAdapter(
        kunder=[Kunde(1, "Kunde Et", None, 1, False, Decimal("10"))],
        leverandoerer=[Leverandoer(9, "Lev Ni", None, None, None)],
        poster=[_post(1), _post(2)],
        aabne=[_aaben(2, partnummer=1)],
    )


def test_alt_virker_giver_bekraeftet(db_session, capsys):
    _kunde(db_session, "B-1")
    assert bekraeft(db_session, "B-1", _fabrik(_fuld_adapter())) is True
    ud = capsys.readouterr().out
    assert "BEKRÆFTET – synkroniseringen virker." in ud
    assert "✘" not in ud


def test_fejl_fra_systemet_giver_ikke_bekraeftet(db_session, capsys):
    _kunde(db_session, "B-2")
    adapter = _fuld_adapter()
    adapter.fejl["hent_customers"] = AdapterFejl("e-conomic svarede 401 – tjek nøglerne")
    assert bekraeft(db_session, "B-2", _fabrik(adapter)) is False
    ud = capsys.readouterr().out
    assert "✘ customers: hentet" in ud and "401" in ud
    assert "IKKE BEKRÆFTET" in ud


def test_kunde_paa_pause_giver_ikke_bekraeftet(db_session, capsys):
    _kunde(db_session, "B-3", status="pause")
    assert bekraeft(db_session, "B-3", _fabrik(_fuld_adapter())) is False
    assert "✘ Kunden har status 'aktiv'" in capsys.readouterr().out


def test_kunde_uden_adgang_giver_ikke_bekraeftet(db_session, capsys):
    _kunde(db_session, "B-4", med_adgang=False)
    assert bekraeft(db_session, "B-4", _fabrik(_fuld_adapter())) is False
    assert "✘ Kunden har en aktiv adgang" in capsys.readouterr().out


def test_afvigelse_mellem_system_og_database_opdages(db_session, capsys):
    _kunde(db_session, "B-5")
    adapter = _fuld_adapter()
    oprindelig = adapter.hent_open_entries
    kald = {"n": 0}

    def skiftende():  # systemet svarer noget andet anden gang end det, der blev gemt
        kald["n"] += 1
        return oprindelig() if kald["n"] == 1 else [_aaben(2, rest="1.00")]

    adapter.hent_open_entries = skiftende
    assert bekraeft(db_session, "B-5", _fabrik(adapter)) is False
    assert "✘ Åbne poster og restbeløb er præcis de samme" in capsys.readouterr().out
