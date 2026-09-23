"""Daglig hentning af kontoplaner – én gang i døgnet – via macOS' indbyggede planlægger (launchd).

    python -m app.planlaegning.natlig_kontoplan installer            # hver dag kl. 12:30
    python -m app.planlaegning.natlig_kontoplan installer --tid 04:00
    python -m app.planlaegning.natlig_kontoplan status
    python -m app.planlaegning.natlig_kontoplan koer-nu              # prøv den med det samme
    python -m app.planlaegning.natlig_kontoplan afinstaller

Kørslen skriver til logs/kontoplan.log i projektmappen. Er Mac'en i dvale
på tidspunktet, kører den, når Mac'en vågner. Er den helt slukket, springes
den nat over. Docker (databasen) skal køre.
Tokens og nøgler skrives aldrig i loggen.
"""

import argparse
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ETIKET = "dk.dinbogholder.afstemningsmotor.kontoplan"
PROJEKT = Path(__file__).resolve().parents[2]
PLIST_STI = Path.home() / "Library" / "LaunchAgents" / f"{ETIKET}.plist"
LOG_STI = PROJEKT / "logs" / "kontoplan.log"


def _tjek_tid(tid: str) -> tuple[int, int]:
    fundet = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", tid)
    if not fundet:
        raise SystemExit(f"Ugyldigt tidspunkt '{tid}' – skriv fx 12:30")
    return int(fundet.group(1)), int(fundet.group(2))


def lav_plist(time: int, minut: int, python: str, projekt: Path, log: Path) -> dict:
    """Indstillingerne for jobbet: kør én gang i døgnet på det angivne klokkeslæt."""
    return {
        "Label": ETIKET,
        "ProgramArguments": [python, "-m", "app.adaptere.economic.kontoplan", "--alle"],
        "WorkingDirectory": str(projekt),
        # Én gang i døgnet. Var Mac'en i dvale, kører den ved opvågning (slukket: springes over).
        "StartCalendarInterval": {"Hour": time, "Minute": minut},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
        # Kør med lav prioritet, så Mac'en ikke bliver langsom.
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def _launchctl(*args: str, tjek: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=tjek)


def _domaene() -> str:
    return f"gui/{os.getuid()}"


def _kraev_mac() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Tidsplanen kan kun installeres på en Mac.")


def installer(tid: str) -> None:
    _kraev_mac()
    time, minut = _tjek_tid(tid)
    python = str(PROJEKT / ".venv" / "bin" / "python")
    if not Path(python).exists():
        raise SystemExit("Fandt ikke .venv/bin/python – kør først: python3 -m venv .venv")
    LOG_STI.parent.mkdir(exist_ok=True)
    PLIST_STI.parent.mkdir(parents=True, exist_ok=True)

    # Fjern en tidligere udgave, så der aldrig kører to.
    _launchctl("bootout", f"{_domaene()}/{ETIKET}", tjek=False)
    with PLIST_STI.open("wb") as f:
        plistlib.dump(lav_plist(time, minut, python, PROJEKT, LOG_STI), f)
    _launchctl("bootstrap", _domaene(), str(PLIST_STI))
    print(f"Installeret: kontoplaner hentes hver dag kl. {time:02d}:{minut:02d}.")
    print(f"Log: {LOG_STI}")


def afinstaller() -> None:
    _kraev_mac()
    _launchctl("bootout", f"{_domaene()}/{ETIKET}", tjek=False)
    if PLIST_STI.exists():
        PLIST_STI.unlink()
    print("Tidsplanen er fjernet. Kontoplaner hentes ikke længere automatisk.")


def status() -> None:
    _kraev_mac()
    if not PLIST_STI.exists():
        print("Ikke installeret.")
        return
    with PLIST_STI.open("rb") as f:
        interval = plistlib.load(f)["StartCalendarInterval"]
    aktiv = _launchctl("print", f"{_domaene()}/{ETIKET}", tjek=False).returncode == 0
    print(
        f"Installeret: hver dag kl. {interval['Hour']:02d}:{interval['Minute']:02d} "
        f"({'aktiv' if aktiv else 'IKKE indlæst – kør installer igen'})."
    )
    if LOG_STI.exists():
        linjer = LOG_STI.read_text(errors="replace").splitlines()
        seneste = [l for l in linjer if "Færdig:" in l or "stoppet" in l]
        print("Seneste kørsel:", seneste[-1] if seneste else "(ingen endnu)")


def koer_nu() -> None:
    _kraev_mac()
    _launchctl("kickstart", f"{_domaene()}/{ETIKET}")
    print(f"Startet. Følg med i loggen: tail -f {LOG_STI}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daglig hentning af kontoplaner på din Mac.")
    under = parser.add_subparsers(dest="handling", required=True)
    inst = under.add_parser("installer", help="kør hver dag (én gang i døgnet)")
    inst.add_argument("--tid", default="12:30", help="klokkeslæt, fx 12:30 (standard)")
    under.add_parser("afinstaller", help="stop den natlige kørsel")
    under.add_parser("status", help="vis om den er installeret og seneste kørsel")
    under.add_parser("koer-nu", help="kør den med det samme (til test)")
    args = parser.parse_args(argv)

    {
        "installer": lambda: installer(args.tid),
        "afinstaller": afinstaller,
        "status": status,
        "koer-nu": koer_nu,
    }[args.handling]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
