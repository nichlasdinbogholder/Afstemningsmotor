"""Dansk tid: datoer i lovregler (10 dage mellem rykkere, bankdage) regnes i dansk tid,
og danske helligdage/bankdage regnes ét sted."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TIDSZONE = ZoneInfo("Europe/Copenhagen")


def dansk_dato(tidspunkt: datetime) -> date:
    """Datoen i Danmark for et tidspunkt med tidszone (fx 23:30 UTC = næste dag i Danmark om sommeren)."""
    return tidspunkt.astimezone(TIDSZONE).date()


def paaskedag(aar: int) -> date:
    """Påskedag (gregoriansk, "anonym" algoritme)."""
    a, b, c = aar % 19, aar // 100, aar % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    maaned = (h + l - 7 * m + 114) // 31
    return date(aar, maaned, (h + l - 7 * m + 114) % 31 + 1)


def helligdage(aar: int) -> set[date]:
    """Danske helligdage og banklukkedage (store bededag er afskaffet fra 2024)."""
    p = paaskedag(aar)
    dage = {date(aar, 1, 1), p - timedelta(days=3), p - timedelta(days=2), p + timedelta(days=1),
            p + timedelta(days=39), p + timedelta(days=50), date(aar, 6, 5),
            date(aar, 12, 24), date(aar, 12, 25), date(aar, 12, 26), date(aar, 12, 31)}
    if aar < 2024:
        dage.add(p + timedelta(days=26))
    return dage


def nte_hverdag(aar: int, maaned: int, n: int) -> date:
    """Den n'te hverdag (mandag–fredag, ikke helligdag) i måneden."""
    dag, fundet, fri = date(aar, maaned, 1), 0, helligdage(aar)
    while True:
        if dag.weekday() < 5 and dag not in fri:
            fundet += 1
            if fundet == n:
                return dag
        dag += timedelta(days=1)


def er_bankdag(dag: date) -> bool:
    return dag.weekday() < 5 and dag not in helligdage(dag.year)


def bankdage_tilbage(dag: date, antal: int) -> date:
    """Den bankdag, der ligger `antal` bankdage før `dag` (dag selv tæller ikke)."""
    while antal > 0:
        dag -= timedelta(days=1)
        if er_bankdag(dag):
            antal -= 1
    return dag


def naeste_bankdag(dag: date) -> date:
    """Første bankdag EFTER `dag`."""
    dag += timedelta(days=1)
    while not er_bankdag(dag):
        dag += timedelta(days=1)
    return dag
