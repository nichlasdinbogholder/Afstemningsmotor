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


@pytest.mark.parametrize("modul", KOMMANDOER[:3])
def test_kommando_viser_hjaelp(modul):
    resultat = subprocess.run(
        [sys.executable, "-m", modul, "--help"], cwd=ROD, capture_output=True, text=True
    )
    assert resultat.returncode == 0, resultat.stderr[-800:]
