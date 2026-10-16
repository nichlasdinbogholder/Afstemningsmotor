"""FLYTTET: kontoplan-hentningen ligger nu i app/synk/kontoplan.py og virker for alle systemer.

Modulet her er kun bevaret, så en allerede installeret daglig kørsel, der kalder
`python -m app.adaptere.economic.kontoplan --alle`, stadig virker. Brug fremover:

    python -m app.synk.kontoplan --kundenummer <nr>   |   --alle
"""

from app.synk.kontoplan import (  # noqa: F401
    FORVENTEDE_FEJL,
    KontoplanFejl,
    gem_kontoplan,
    hent_for_alle,
    hent_og_gem_kontoplan,
    kunder_til_kontoplan,
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
