"""Kan alle gemte tokens læses med CREDENTIALS_KEY?

    python -m app.sikkerhed.tjek_tokens

Bruges efter en gendannelse fra backup: en database, hvis tokens ikke kan læses,
er ikke en brugbar backup (fx fordi hovednøglen ikke passer). Viser kun antal –
aldrig selve tokens. Kode 0 = alle kan læses, 1 = ikke alle, 2 = fejl.
"""

import sys

from sqlalchemy import text

from app.db import get_engine
from app.sikkerhed.kryptering import KrypteringsFejl, kan_dekrypteres


def main() -> int:
    try:
        with get_engine().connect() as forbindelse:
            krypterede = forbindelse.execute(text("SELECT token_krypteret FROM credentials")).scalars().all()
        ok = sum(kan_dekrypteres(t) for t in krypterede)
    except KrypteringsFejl as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2
    print(f"{ok} af {len(krypterede)} tokens kan læses med CREDENTIALS_KEY")
    return 0 if ok == len(krypterede) else 1


if __name__ == "__main__":
    sys.exit(main())
