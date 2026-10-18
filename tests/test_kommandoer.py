"""Kommandoerne skal virke, når de køres alene – uden testopsætningens hjælp.

Hver kommando startes i sin egen Python-proces (som når man kører den i
Terminal), og vi tjekker, at alle tabeller og deres henvisninger kan findes.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROD = Path(__file__).resolve().parent.parent

KOMMANDOER = [
    "app.kunder.gem_token",
    "app.adaptere.economic.kontoplan",
    "app.sikkerhed.ny_noegle",
    "app.planlaegning.natlig_kontoplan",
    "app.jobs.worker",
    "app.jobs.koe",
    "app.synk.kommando",
    "app.synk.koer",
    "app.synk.planlaegger",
    "app.synk.bekraeft",
    "app.synk.kontoplan",
    "app.afstemning.fejlkonto",
    "app.afstemning.regler",
    "app.cli",
    "app.natkoersel.scheduler",
    "app.natkoersel.status",
    "app.kontoudtog.importer",
    "app.kunder.adgange",
]


@pytest.mark.parametrize("modul", KOMMANDOER)
def test_kommando_kender_alle_tabeller(modul):
    kode = (
        f"import {modul}\n"
        "from app.db import Base\n"
        "import app.db\n"
        "app.db.ny_session  # samme vej som kommandoerne\n"
        "import app.models\n"
        "Base.metadata.sorted_tables  # fejler, hvis en henvisning peger på en ukendt tabel\n"
    )
    # Tjek FØR app.models indlæses udefra: modulet selv skal have indlæst tabellerne.
    foer = (
        f"import {modul}\n"
        "from app.db import Base\n"
        "if any(t.name == 'audit_log' for t in Base.metadata.sorted_tables):\n"
        "    assert 'staff' in Base.metadata.tables\n"
    )
    for script in (foer, kode):
        resultat = subprocess.run(
            [sys.executable, "-c", script], cwd=ROD, capture_output=True, text=True
        )
        assert resultat.returncode == 0, resultat.stderr[-800:]


@pytest.mark.parametrize("modul", [m for m in KOMMANDOER if m != "app.kunder.adgange"])
def test_kommando_viser_hjaelp(modul):
    resultat = subprocess.run(
        [sys.executable, "-m", modul, "--help"], cwd=ROD, capture_output=True, text=True
    )
    assert resultat.returncode == 0, resultat.stderr[-800:]


def test_opret_kunde(db_session, monkeypatch, capsys):
    from contextlib import contextmanager

    from sqlalchemy import select

    from app.kunder import opret
    from app.kunder.models import Client

    @contextmanager
    def samme():
        yield db_session

    monkeypatch.setattr(opret, "ny_session", samme)
    assert opret.main(["--navn", "Test El ApS", "--kundenummer", "T-1045", "--cvr", "1234 5678",
                       "--system", "economic"]) == 0
    k = db_session.scalars(select(Client).where(Client.kundenummer == "T-1045")).one()
    assert (k.cvr, k.status, k.regnskabssystem) == ("12345678", "aktiv", "economic")
    assert "gem_token --kundenummer T-1045" in capsys.readouterr().out
    assert opret.main(["--navn", "Igen", "--kundenummer", "T-1045", "--system", "economic"]) == 2

    assert opret.main(["--kundenummer", "T-1045", "--kontoudtog-fra", "2026-09-01"]) == 0
    assert str(k.kontoudtog_fra) == "2026-09-01"


def test_opret_kunde_med_ansvarlig(db_session, monkeypatch, capsys):
    from contextlib import contextmanager

    import pytest
    from sqlalchemy import select

    from app.audit.models import AuditLog
    from app.kunder import opret
    from app.kunder.models import Client
    from app.personale.models import Staff

    @contextmanager
    def samme():
        yield db_session

    monkeypatch.setattr(opret, "ny_session", samme)
    m = Staff(navn="Ansvarlig", email="ansvarlig-kmd@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    assert opret.main(["--navn", "Ansvar ApS", "--kundenummer", "T-2001", "--system", "economic",
                       "--ansvarlig", "Ansvarlig-KMD@dinbogholder.dk"]) == 0
    k = db_session.scalars(select(Client).where(Client.kundenummer == "T-2001")).one()
    assert k.ansvarlig_medarbejder_id == m.id
    with pytest.raises(SystemExit, match="ikke en aktiv medarbejder"):
        opret.main(["--kundenummer", "T-2001", "--ansvarlig", "findes-ikke@dinbogholder.dk"])
    m2 = Staff(navn="Ny ansvarlig", email="ny-kmd@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m2)
    db_session.flush()
    assert opret.main(["--kundenummer", "T-2001", "--ansvarlig", "ny-kmd@dinbogholder.dk"]) == 0
    assert k.ansvarlig_medarbejder_id == m2.id
    log = db_session.scalars(select(AuditLog).where(AuditLog.client_id == k.id, AuditLog.handling == "kunde_rettet")).one()
    assert log.detaljer["ansvarlig"]["til"] == m2.id
    assert opret.main(["--vis"]) == 0
    assert "ansvarlig ny-kmd@dinbogholder.dk" in capsys.readouterr().out
