"""Betalingsnøglen (FIK, kortart 71): +71<BETALINGS-ID +FI-KREDITORNR<

Betalings-id'et er 15 cifre: foranstillede nuller, fakturanummeret og til sidst TO kontrolcifre
(bekræftet af bogholderiet ud fra FarPay: faktura 742 → 000000000074203). Det sidste ciffer er et
modulus 10-kontrolciffer (Luhn) over alle cifrene før det; det næstsidste har i FarPay været 0.
FI-kreditornummeret (8 cifre) er KUNDENS – det kobler en indbetaling til kunden (clients.fi_kreditornummer).

`laes_fik(...)` bruges, når indbetalinger aflæses (trin 6): den gætter aldrig – et forkert kontrolciffer,
forkert længde eller kortart giver `UgyldigFIK`.
"""

import re
from dataclasses import dataclass

KORTART = "71"
ID_LAENGDE = 15
FAST_CIFFER = "0"   # næstsidste ciffer, som FarPay har sat det


class UgyldigFIK(ValueError):
    pass


def kontrolciffer(cifre: str) -> str:
    """Modulus 10 (Luhn): hvert andet ciffer fra højre (begyndende med det yderste) ganges med 2."""
    total = 0
    for i, c in enumerate(reversed(cifre)):
        n = int(c) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return str((10 - total % 10) % 10)


def betalings_id(fakturanummer: str | int) -> str:
    nr = str(int(str(fakturanummer)))   # kun cifre – fejler på bogstaver
    krop = nr + FAST_CIFFER
    if len(krop) + 1 > ID_LAENGDE:
        raise UgyldigFIK(f"Fakturanummer {nr} er for langt til et betalings-id")
    return (krop + kontrolciffer(krop)).zfill(ID_LAENGDE)


def fik_linje(fakturanummer: str | int, fi_kreditornummer: str) -> str:
    if not re.fullmatch(r"\d{8}", fi_kreditornummer or ""):
        raise UgyldigFIK("FI-kreditornummeret skal være 8 cifre")
    return f"+{KORTART}<{betalings_id(fakturanummer)} +{fi_kreditornummer}<"


@dataclass(frozen=True)
class Fik:
    betalings_id: str
    fakturanummer: str
    fi_kreditornummer: str | None


def laes_fik(tekst: str) -> Fik:
    """Aflæs en FIK-linje ("+71<000000000074203 +80679858<") eller blot betalings-id'et (15 cifre)."""
    t = (tekst or "").strip()
    m = re.fullmatch(r"\+?(\d{2})\s*<\s*(\d+)\s*\+\s*(\d+)\s*<?", t)
    if m:
        kortart, bid, kreditor = m.groups()
        if kortart != KORTART:
            raise UgyldigFIK(f"Kortart {kortart} – kun +71 understøttes")
    elif re.fullmatch(r"\d+", t):
        bid, kreditor = t, None
    else:
        raise UgyldigFIK("Ikke en FIK-linje")
    if len(bid) != ID_LAENGDE:
        raise UgyldigFIK(f"Betalings-id skal være {ID_LAENGDE} cifre (har {len(bid)})")
    if kontrolciffer(bid[:-1]) != bid[-1]:
        raise UgyldigFIK("Kontrolcifferet passer ikke")
    if kreditor is not None and len(kreditor) != 8:
        raise UgyldigFIK("FI-kreditornummeret skal være 8 cifre")
    nr = bid[:-2].lstrip("0")
    if not nr:
        raise UgyldigFIK("Betalings-id'et indeholder intet fakturanummer")
    return Fik(betalings_id=bid, fakturanummer=nr, fi_kreditornummer=kreditor)
