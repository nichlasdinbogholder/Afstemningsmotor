"""e-conomic-adapteren: de fire faste funktioner oversat fra e-conomics REST API.

Endpoints og felter (fra e-conomics JSON-skemaer, restapi.e-conomic.com):
- /customers     : customerNumber, name, corporateIdentificationNumber,
                   paymentTerms.paymentTermsNumber, barred, balance
- /suppliers     : supplierNumber, name, corporateIdentificationNumber,
                   paymentTerms.paymentTermsNumber  (e-conomic leverer ingen saldo
                   på leverandører – saldo er derfor altid None)
- /accounting-years                 : year ("2026" eller "2025/2026")
- /accounting-years/{år}/entries    : entryNumber, voucherNumber, date, dueDate,
                   account.accountNumber, text, amount, currency, entryType,
                   customer.customerNumber, supplier.supplierNumber, invoiceNumber,
                   supplierInvoiceNumber, remainder
Regnskabsår i adressen kodes med e-conomics skema, fx "2025/2026" -> "2025_6_2026".

Poster (entries) hentes inkrementelt: cursor = højeste entryNumber, vi har set,
og der filtreres med `entryNumber$gt:<cursor>` i alle regnskabsår (en rettelse i
et gammelt år får et nyt, højere entryNumber). Åbne poster hentes altid fuldt:
alle poster med restbeløb (`remainder$ne:0`) i alle regnskabsår – og for en
sikkerheds skyld filtreres der også her i koden.
"""

from datetime import date
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.adaptere.adgang import hent_adgang
from app.adaptere.economic.klient import EconomicFejl, EconomicKlient, app_secret_token
from app.adaptere.regnskab.base import (
    ENTRY_TYPER,
    AabenPost,
    Kunde,
    Leverandoer,
    Postering,
    PosteringsSvar,
    registrer_adapter,
)
from app.config import get_settings

SYSTEM = "economic"

# e-conomics egen kodning af tegn i id'er i adressen.
_ERSTATNINGER = {
    "<": "0", ">": "1", "*": "2", "%": "3", ":": "4", "&": "5", "/": "6",
    "\\": "7", "_": "8", " ": "9", "?": "10", ".": "11", "#": "12", "+": "13",
}


def kod_id(vaerdi: str) -> str:
    """Kod et id til brug i e-conomic-adresser, fx '2025/2026' -> '2025_6_2026'."""
    if not vaerdi:
        raise EconomicFejl("Tomt id kan ikke bruges i en adresse")
    return "".join(
        f"_{_ERSTATNINGER[t]}_" if t in _ERSTATNINGER else quote(t, safe="-") for t in vaerdi
    )


# --- Oversættelse: e-conomic -> vores format (gætter aldrig) -----------------


def _kraev(data: dict, felt: str, hvad: str):
    if data.get(felt) is None:
        raise EconomicFejl(f"e-conomic sendte {hvad} uden '{felt}' – kan ikke gemmes")
    return data[felt]


def _decimal(vaerdi) -> Decimal | None:
    return None if vaerdi is None else Decimal(str(vaerdi))


def _dato(vaerdi) -> date | None:
    return None if vaerdi is None else date.fromisoformat(vaerdi)


def _nummer(handler: dict | None, felt: str) -> int | None:
    return None if not handler else handler.get(felt)


def oversaet_kunde(d: dict) -> Kunde:
    return Kunde(
        kundenummer=_kraev(d, "customerNumber", "en kunde"),
        navn=_kraev(d, "name", "en kunde"),
        cvr=d.get("corporateIdentificationNumber"),
        betalingsbetingelse=_nummer(d.get("paymentTerms"), "paymentTermsNumber"),
        spaerret=d.get("barred"),
        saldo=_decimal(d.get("balance")),
    )


def oversaet_leverandoer(d: dict) -> Leverandoer:
    return Leverandoer(
        leverandoernummer=_kraev(d, "supplierNumber", "en leverandør"),
        navn=_kraev(d, "name", "en leverandør"),
        cvr=d.get("corporateIdentificationNumber"),
        betalingsbetingelse=_nummer(d.get("paymentTerms"), "paymentTermsNumber"),
        saldo=None,  # e-conomic leverer ikke saldo på leverandører
    )


def _modpart(d: dict) -> str | None:
    kunde = _nummer(d.get("customer"), "customerNumber")
    leverandoer = _nummer(d.get("supplier"), "supplierNumber")
    if kunde is not None and leverandoer is not None:
        raise EconomicFejl(f"Post {d.get('entryNumber')} har både kunde og leverandør")
    if kunde is not None:
        return f"debitor:{kunde}"
    if leverandoer is not None:
        return f"kreditor:{leverandoer}"
    return None


def _entry_type(vaerdi: str | None) -> str | None:
    if vaerdi is not None and vaerdi not in ENTRY_TYPER:
        raise EconomicFejl(f"Ukendt entryType '{vaerdi}' fra e-conomic")
    return vaerdi


def oversaet_postering(d: dict) -> Postering:
    return Postering(
        bogfoert_id=_kraev(d, "entryNumber", "en post"),
        bilagsnummer=d.get("voucherNumber"),
        dato=_dato(d.get("date")),
        kontonummer=_nummer(d.get("account"), "accountNumber"),
        tekst=d.get("text"),
        beloeb=_decimal(d.get("amount")),
        modpart=_modpart(d),
        valuta=d.get("currency"),
        entry_type=_entry_type(d.get("entryType")),
    )


def er_aaben(d: dict) -> bool:
    """En åben post: har restbeløb forskelligt fra 0 og hører til en kunde eller leverandør."""
    rest = d.get("remainder")
    return rest is not None and Decimal(str(rest)) != 0 and _modpart(d) is not None


def oversaet_aaben_post(d: dict) -> AabenPost:
    modpart = _modpart(d)
    if modpart is None:
        raise EconomicFejl(f"Post {d.get('entryNumber')} har hverken kunde eller leverandør")
    type_, nummer = modpart.split(":")
    return AabenPost(
        bogfoert_id=_kraev(d, "entryNumber", "en åben post"),
        type=type_,
        partnummer=int(nummer),
        partnavn=None,  # står ikke på posten i e-conomic – udfyldes fra customers/suppliers
        bilagsnummer=d.get("voucherNumber"),
        fakturanummer=d.get("invoiceNumber") if type_ == "debitor" else d.get("supplierInvoiceNumber"),
        dato=_dato(d.get("date")),
        forfaldsdato=_dato(d.get("dueDate")),
        beloeb=_decimal(d.get("amount")),
        restbeloeb=_decimal(_kraev(d, "remainder", "en åben post")),
        valuta=d.get("currency"),
    )


# --- Adapteren --------------------------------------------------------------


class EconomicAdapter:
    system = SYSTEM

    def __init__(self, klient: EconomicKlient) -> None:
        self._klient = klient

    def __enter__(self) -> "EconomicAdapter":
        return self

    def __exit__(self, *args) -> None:
        self._klient.__exit__(*args)

    def _regnskabsaar(self) -> list[str]:
        return [kod_id(_kraev(a, "year", "et regnskabsår")) for a in self._klient.hent_alle("/accounting-years")]

    def hent_customers(self) -> list[Kunde]:
        return [oversaet_kunde(d) for d in self._klient.hent_alle("/customers")]

    def hent_suppliers(self) -> list[Leverandoer]:
        return [oversaet_leverandoer(d) for d in self._klient.hent_alle("/suppliers")]

    def hent_entries(self, efter: str | None) -> PosteringsSvar:
        if efter is not None and not efter.isdigit():
            raise EconomicFejl(f"Ugyldigt bogmærke for entries: '{efter}'")
        filter = f"entryNumber$gt:{efter}" if efter is not None else None
        poster = [
            oversaet_postering(d)
            for aar in self._regnskabsaar()
            for d in self._klient.hent_alle(f"/accounting-years/{aar}/entries", filter=filter)
            if efter is None or d.get("entryNumber", 0) > int(efter)
        ]
        hoejeste = max((p.bogfoert_id for p in poster), default=None)
        ny_cursor = str(hoejeste) if hoejeste is not None else efter
        return PosteringsSvar(poster, ny_cursor, "id" if ny_cursor is not None else None)

    def hent_open_entries(self) -> list[AabenPost]:
        return [
            oversaet_aaben_post(d)
            for aar in self._regnskabsaar()
            for d in self._klient.hent_alle(f"/accounting-years/{aar}/entries", filter="remainder$ne:0")
            if er_aaben(d)
        ]


@registrer_adapter(SYSTEM)
def lav_economic_adapter(session: Session, client_id: int) -> EconomicAdapter:
    adgang = hent_adgang(session, client_id, SYSTEM)
    return EconomicAdapter(EconomicKlient(
        app_secret_token=app_secret_token(),
        agreement_grant_token=adgang.token,
        base_url=get_settings().economic_api_base_url,
    ))
