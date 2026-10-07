"""e-conomic-adapteren: de fire faste funktioner oversat fra e-conomics REST API.

Endpoints og felter (fra e-conomics JSON-skemaer, restapi.e-conomic.com):
- /accounts      : accountNumber, name, accountType, debitCredit, vatAccount.vatCode,
                   barred, blockDirectEntries, balance, draftBalance   (kontoplan)
- /customers     : customerNumber, name, corporateIdentificationNumber,
                   paymentTerms.paymentTermsNumber, barred, balance
- /suppliers     : supplierNumber, name, corporateIdentificationNumber,
                   paymentTerms.paymentTermsNumber, supplierGroup.supplierGroupNumber  (e-conomic leverer ingen saldo
                   på leverandører – saldo er derfor altid None)
- /journals      : journalNumber, name                       (kassekladder)
- /journals/{nr}/entries : journalEntryNumber, voucher.voucherNumber, date,
                   account.accountNumber, contraAccount.accountNumber, text, amount,
                   currency.code, entryType                (kladdelinjer, ikke bogført)
- /accounting-years                 : year ("2026" eller "2025/2026"), fromDate, toDate, closed
- /accounting-years/{år}/entries    : entryNumber, voucherNumber, date, dueDate,
                   account.accountNumber, text, amount, amountInBaseCurrency, currency, entryType,
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
from app.adaptere.economic.klient import EconomicFejl, EconomicKlient, UsikkerListe, app_secret_token
from app.adaptere.regnskab.base import (
    ENTRY_TYPER,
    DEBET_KREDIT,
    KONTOTYPER,
    AabenPost,
    Adresse,
    Debitor,
    DelvisHentet,
    Faktura,
    FakturaLinje,
    ForMangeKald,
    Konto,
    Kassekladde,
    KladdePost,
    Kunde,
    Leverandoer,
    Postering,
    PosteringsSvar,
    Regnskabsaar,
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


def _tekst(vaerdi) -> str | None:
    return str(vaerdi).strip() or None if vaerdi is not None else None


def _i_liste(vaerdi, tilladte: tuple, felt: str):
    if vaerdi is not None and vaerdi not in tilladte:
        raise EconomicFejl(f"Ukendt {felt} '{vaerdi}' fra e-conomic")
    return vaerdi


def oversaet_konto(d: dict) -> Konto:
    return Konto(
        kontonummer=_kraev(d, "accountNumber", "en konto"),
        navn=_kraev(d, "name", "en konto"),
        kontotype=_i_liste(d.get("accountType"), KONTOTYPER, "accountType"),
        debet_kredit=_i_liste(d.get("debitCredit"), DEBET_KREDIT, "debitCredit"),
        momskode=(d.get("vatAccount") or {}).get("vatCode"),
        spaerret=d.get("barred"),
        direkte_posteringer_blokeret=d.get("blockDirectEntries"),
        saldo=_decimal(d.get("balance")),
        kladdesaldo=_decimal(d.get("draftBalance")),
        raa_data=d,
    )


def oversaet_kunde(d: dict) -> Kunde:
    return Kunde(
        kundenummer=_kraev(d, "customerNumber", "en kunde"),
        navn=_kraev(d, "name", "en kunde"),
        cvr=d.get("corporateIdentificationNumber"),
        betalingsbetingelse=_nummer(d.get("paymentTerms"), "paymentTermsNumber"),
        spaerret=d.get("barred"),
        saldo=_decimal(d.get("balance")),
    )


def oversaet_debitor(d: dict) -> Debitor:
    """Feltnavne set i rå JSON fra /customers (Connect El, 07.10.2026)."""
    return Debitor(
        kundenummer=_kraev(d, "customerNumber", "en kunde"),
        navn=_kraev(d, "name", "en kunde"),
        cvr=_tekst(d.get("corporateIdentificationNumber")),
        email=_tekst(d.get("email")),
        ean=_tekst(d.get("ean")),
        adresse=_tekst(d.get("address")),
        postnr=_tekst(d.get("zip")),
        by=_tekst(d.get("city")),
        land=_tekst(d.get("country")),
        gruppe=_nummer(d.get("customerGroup"), "customerGroupNumber"),
    )


def oversaet_faktura(d: dict) -> Faktura:
    """Feltnavne set i rå JSON fra /invoices/booked (Connect El, 07.10.2026)."""
    return Faktura(
        nummer=_kraev(d, "bookedInvoiceNumber", "en faktura"),
        kundenummer=_kraev(d.get("customer") or {}, "customerNumber", "en fakturas kunde"),
        dato=_dato(_kraev(d, "date", "en faktura")),
        forfaldsdato=_dato(_kraev(d, "dueDate", "en faktura")),
        beloeb=_decimal(_kraev(d, "grossAmount", "en faktura")),
        restbeloeb=_decimal(_kraev(d, "remainder", "en faktura")),
        valuta=_kraev(d, "currency", "en faktura"),
        ean=_tekst((d.get("recipient") or {}).get("ean")),
        ordrenummer=d.get("orderNumber"),
        oevrig_ref=_tekst((d.get("references") or {}).get("other")),
        netto=_decimal(d.get("netAmount")),
        moms=_decimal(d.get("vatAmount")),
        modtager=_adresse(d.get("recipient")),
        levering=_adresse(d.get("delivery")),
        overskrift=_tekst((d.get("notes") or {}).get("heading")),
        tekst=_tekst((d.get("notes") or {}).get("textLine1")),
    )


def _adresse(d: dict | None) -> Adresse | None:
    if not d:
        return None
    a = Adresse(navn=_tekst(d.get("name")), adresse=_tekst(d.get("address")), postnr=_tekst(d.get("zip")),
                by=_tekst(d.get("city")))
    return None if a == Adresse(None, None, None, None) else a


def oversaet_fakturalinje(d: dict) -> FakturaLinje:
    """Feltnavne set i rå JSON fra /invoices/booked/{nr} (Connect El, 07.10.2026). unitCostPrice hentes ikke."""
    return FakturaLinje(
        linjenummer=_kraev(d, "lineNumber", "en fakturalinje"),
        varenummer=_tekst((d.get("product") or {}).get("productNumber")),
        beskrivelse=_tekst(d.get("description")),
        antal=_decimal(d.get("quantity")),
        enhed=_tekst((d.get("unit") or {}).get("name")),
        stykpris=_decimal(d.get("unitNetPrice")),
        rabat_procent=_decimal(d.get("discountPercentage")),
        beloeb=_decimal(d.get("totalNetAmount")),
    )


def oversaet_leverandoer(d: dict) -> Leverandoer:
    return Leverandoer(
        leverandoernummer=_kraev(d, "supplierNumber", "en leverandør"),
        navn=_kraev(d, "name", "en leverandør"),
        cvr=d.get("corporateIdentificationNumber"),
        betalingsbetingelse=_nummer(d.get("paymentTerms"), "paymentTermsNumber"),
        saldo=None,  # e-conomic leverer ikke saldo på leverandører
        gruppe=_nummer(d.get("supplierGroup"), "supplierGroupNumber"),
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
        beloeb_dkk=_decimal(d.get("amountInBaseCurrency")),
        fakturanummer=_tekst(d.get("supplierInvoiceNumber") or d.get("invoiceNumber")),
        raa_data=d,
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
        entry_type=_entry_type(d.get("entryType")),
    )


def oversaet_kassekladde(d: dict) -> Kassekladde:
    return Kassekladde(nummer=_kraev(d, "journalNumber", "en kassekladde"), navn=d.get("name"))


def oversaet_kladdepost(kladde_nummer: int, d: dict) -> KladdePost:
    return KladdePost(
        kladde_nummer=kladde_nummer,
        linje_id=d.get("journalEntryNumber"),
        bilagsnummer=_nummer(d.get("voucher"), "voucherNumber"),
        dato=_dato(d.get("date")),
        konto=_nummer(d.get("account"), "accountNumber"),
        modkonto=_nummer(d.get("contraAccount"), "accountNumber"),
        tekst=d.get("text"),
        beloeb=_decimal(d.get("amount")),
        valuta=_nummer(d.get("currency"), "code"),
        entry_type=d.get("entryType"),
        modpart=_modpart(d),
        fakturanummer=_tekst(d.get("supplierInvoiceNumber") or d.get("invoiceNumber")),
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
        """Regnskabsårene, ældste først (fx "2025/2026" før "2026"), kodet til adressen."""
        aar = [_kraev(a, "year", "et regnskabsår") for a in self._klient.hent_alle("/accounting-years")]
        return [kod_id(a) for a in sorted(aar)]

    def fetch_accounting_years(self) -> list[Regnskabsaar]:
        return [Regnskabsaar(navn=str(_kraev(a, "year", "et regnskabsår")), fra=_dato(a.get("fromDate")),
                             til=_dato(a.get("toDate")), lukket=a.get("closed"))
                for a in self._klient.hent_alle("/accounting-years")]

    def fetch_accounts(self) -> list[Konto]:
        return [oversaet_konto(d) for d in self._klient.hent_alle("/accounts")]

    def fetch_customers(self) -> list[Kunde]:
        return [oversaet_kunde(d) for d in self._klient.hent_alle("/customers")]

    def fetch_debtors(self) -> list[Debitor]:
        return [oversaet_debitor(d) for d in self._klient.hent_alle("/customers")]

    def fetch_invoices(self) -> list[Faktura]:
        return [oversaet_faktura(d) for d in self._klient.hent_alle("/invoices/booked")]

    def fetch_invoice_lines(self, nummer: int) -> list[FakturaLinje]:
        """Linjerne findes kun på den enkelte faktura – ét kald pr. faktura."""
        d = self._klient.hent_en(f"/invoices/booked/{int(nummer)}")
        return [oversaet_fakturalinje(x) for x in d.get("lines") or []]

    def fetch_suppliers(self) -> list[Leverandoer]:
        return [oversaet_leverandoer(d) for d in self._klient.hent_alle("/suppliers")]

    def fetch_entries(self, efter: str | None) -> PosteringsSvar:
        """Poster med entryNumber > `efter`, år for år (ældste først), sorteret efter entryNumber.

        Stopper hentningen midtvejs, rejses `DelvisHentet` med de poster, der nåede at
        komme, og et SIKKERT bogmærke: kun hvis fejlen skete i det sidste regnskabsår,
        og posterne dér kom i stigende rækkefølge, kan bogmærket flyttes til den sidst
        hentede post – ellers kunne en lavere post i et senere år blive sprunget over.
        """
        if efter is not None and not efter.isdigit():
            raise EconomicFejl(f"Ugyldigt bogmærke for entries: '{efter}'")
        filter = f"entryNumber$gt:{efter}" if efter is not None else None
        aar_liste = self._regnskabsaar()
        poster: list[Postering] = []
        for i, aar in enumerate(aar_liste):
            sidste_i_aaret, stigende = None, True
            try:
                for d in self._klient.hent_alle(f"/accounting-years/{aar}/entries",
                                                filter=filter, sort="entryNumber"):
                    if efter is not None and d.get("entryNumber", 0) <= int(efter):
                        continue
                    p = oversaet_postering(d)
                    if sidste_i_aaret is not None and p.bogfoert_id <= sidste_i_aaret:
                        stigende = False
                    sidste_i_aaret = p.bogfoert_id
                    poster.append(p)
            except (EconomicFejl, ForMangeKald) as fejl:
                if isinstance(fejl, UsikkerListe):
                    raise
                sikker = efter
                if i == len(aar_liste) - 1 and stigende and sidste_i_aaret is not None:
                    sikker = str(sidste_i_aaret)
                raise DelvisHentet(fejl, poster, sikker, "id" if sikker is not None else None) from None
        hoejeste = max((p.bogfoert_id for p in poster), default=None)
        ny_cursor = str(max(hoejeste, int(efter or 0))) if hoejeste is not None else efter
        return PosteringsSvar(poster, ny_cursor, "id" if ny_cursor is not None else None)

    def fetch_open_entries(self) -> list[AabenPost]:
        return [
            oversaet_aaben_post(d)
            for aar in self._regnskabsaar()
            for d in self._klient.hent_alle(f"/accounting-years/{aar}/entries", filter="remainder$ne:0")
            if er_aaben(d)
        ]


    def fetch_journals(self) -> list[Kassekladde]:
        return [oversaet_kassekladde(d) for d in self._klient.hent_alle("/journals")]

    def fetch_journal_entries(self, nummer: int) -> list[KladdePost]:
        return [oversaet_kladdepost(nummer, d)
                for d in self._klient.hent_alle(f"/journals/{int(nummer)}/entries")]


@registrer_adapter(SYSTEM)
def lav_economic_adapter(session: Session, client_id: int, transport=None, vent=None) -> EconomicAdapter:
    """`transport`/`vent` bruges kun af tests (simuleret e-conomic, ingen ventetid)."""
    adgang = hent_adgang(session, client_id, SYSTEM)
    return EconomicAdapter(EconomicKlient(
        app_secret_token=app_secret_token(),
        agreement_grant_token=adgang.token,
        base_url=get_settings().economic_api_base_url,
        transport=transport,
        vent=vent,
    ))
