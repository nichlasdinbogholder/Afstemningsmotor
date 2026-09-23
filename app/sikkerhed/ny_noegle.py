"""Lav en ny hovednøgle til CREDENTIALS_KEY.

    python -m app.sikkerhed.ny_noegle          # vis en ny nøgle på skærmen
    python -m app.sikkerhed.ny_noegle --gem    # skriv den direkte ind i .env

--gem nægter at overskrive en eksisterende nøgle, fordi alle gemte tokens så
ikke længere kan læses.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from app.sikkerhed.kryptering import generer_hovednoegle

_LINJE = re.compile(r"^CREDENTIALS_KEY=(.*)$", re.MULTILINE)


def gem_i_env(sti: Path, noegle: str) -> None:
    indhold = sti.read_text() if sti.exists() else ""
    fundet = _LINJE.search(indhold)
    if fundet and fundet.group(1).strip():
        raise SystemExit(
            f"{sti} har allerede en CREDENTIALS_KEY. Den overskrives ikke, fordi de "
            "gemte tokens så ikke kan læses længere. Fjern den selv, hvis du er sikker."
        )
    if fundet:
        indhold = _LINJE.sub(lambda _: f"CREDENTIALS_KEY={noegle}", indhold, count=1)
    else:
        if indhold and not indhold.endswith("\n"):
            indhold += "\n"
        indhold += f"CREDENTIALS_KEY={noegle}\n"
    sti.write_text(indhold)
    os.chmod(sti, 0o600)  # kun ejeren må læse filen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lav en ny hovednøgle til CREDENTIALS_KEY.")
    parser.add_argument("--gem", action="store_true", help="skriv nøglen ind i .env")
    parser.add_argument("--env-fil", default=".env", type=Path, help="sti til .env (standard: .env)")
    args = parser.parse_args(argv)

    noegle = generer_hovednoegle()
    if args.gem:
        gem_i_env(args.env_fil, noegle)
        print(f"Ny hovednøgle gemt i {args.env_fil}.")
        print("Tag en sikkerhedskopi af den (fx i en adgangskodeholder).")
    else:
        print(noegle)
        print(
            "\nIndsæt linjen CREDENTIALS_KEY=<nøglen> i .env, og gem en kopi et "
            "sikkert sted.\nDel den aldrig i chat, mail eller git.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
