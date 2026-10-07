"""Dansk tid: datoer i lovregler (10 dage mellem rykkere, bankdage) regnes i dansk tid."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

TIDSZONE = ZoneInfo("Europe/Copenhagen")


def dansk_dato(tidspunkt: datetime) -> date:
    """Datoen i Danmark for et tidspunkt med tidszone (fx 23:30 UTC = næste dag i Danmark om sommeren)."""
    return tidspunkt.astimezone(TIDSZONE).date()
