"""Beskyttelse mod at tokens havner i logs, fejlbeskeder eller udskrifter.

To lag:
1. `HemmeligtToken` pakker et token ind, så print(), str(), repr() og
   f-strenge kun viser en maskeret værdi (fx '****abcd').
2. Alle tokens, programmet har set, bliver registreret. Et filter på Pythons
   logning og på udskrivning af ukontrollerede fejl erstatter dem med den
   maskerede værdi – også hvis nogen ved en fejl logger den rå tekst.
"""

import logging
import sys
import threading
import traceback

_MASKE = "****"

_kendte_hemmeligheder: set[str] = set()
_laas = threading.Lock()
_installeret = False


def maskér(værdi: str | None, synlige: int = 4) -> str:
    """Vis kun de sidste tegn, fx '****abcd'. Korte værdier vises slet ikke."""
    if not værdi or len(værdi) <= synlige * 3:
        return _MASKE
    return _MASKE + værdi[-synlige:]


class HemmeligtToken:
    """Et dekrypteret token, der ikke kan komme til at blive vist ved et uheld."""

    __slots__ = ("_værdi",)

    def __init__(self, værdi: str) -> None:
        registrer_hemmelighed(værdi)
        object.__setattr__(self, "_værdi", værdi)

    def klartekst(self) -> str:
        """Den rigtige værdi. Brug den KUN direkte i kaldet til e-conomic/Dinero."""
        return self._værdi

    def __setattr__(self, *_):
        raise AttributeError("HemmeligtToken kan ikke ændres")

    def __repr__(self) -> str:
        return f"HemmeligtToken({maskér(self._værdi)})"

    def __str__(self) -> str:
        return maskér(self._værdi)

    def __format__(self, _spec: str) -> str:
        return maskér(self._værdi)

    def __reduce__(self):
        # Må ikke kunne gemmes til fil/cache (pickle) i klartekst.
        raise TypeError("HemmeligtToken kan ikke gemmes eller kopieres ud")

    def __eq__(self, andet: object) -> bool:
        return isinstance(andet, HemmeligtToken) and andet._værdi == self._værdi

    __hash__ = None  # type: ignore[assignment]


def registrer_hemmelighed(værdi: str) -> None:
    """Husk en hemmelighed, så den bliver skjult i logs og fejludskrifter."""
    if not værdi:
        return
    with _laas:
        _kendte_hemmeligheder.add(værdi)
    installer_beskyttelse()


def rediger(tekst: str) -> str:
    """Erstat alle kendte hemmeligheder i en tekst med den maskerede værdi."""
    # Længste først, så et token, der indeholder et andet, skjules helt.
    for hemmelighed in sorted(_kendte_hemmeligheder, key=len, reverse=True):
        if hemmelighed in tekst:
            tekst = tekst.replace(hemmelighed, maskér(hemmelighed))
    return tekst


def _lav_log_record_factory(gammel):
    formatter = logging.Formatter()

    def factory(*args, **kwargs):
        record = gammel(*args, **kwargs)
        if not _kendte_hemmeligheder:
            return record
        try:
            record.msg = rediger(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "[logbesked skjult: kunne ikke kontrolleres for hemmeligheder]"
            record.args = ()
        if record.exc_info:
            record.exc_text = rediger(formatter.formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = rediger(record.stack_info)
        return record

    return factory


def _skriv_redigeret_fejl(exc_type, exc, tb) -> None:
    tekst = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(rediger(tekst))


def installer_beskyttelse() -> None:
    """Slå filtreringen til for logning og ukontrollerede fejl (kun én gang)."""
    global _installeret
    with _laas:
        if _installeret:
            return
        logging.setLogRecordFactory(_lav_log_record_factory(logging.getLogRecordFactory()))
        sys.excepthook = _skriv_redigeret_fejl
        threading.excepthook = lambda a: _skriv_redigeret_fejl(
            a.exc_type, a.exc_value, a.exc_traceback
        )
        _installeret = True
