"""Svends opslag i kundens regnskab (fase 4, trin 13).

Et lille, fast sæt LÆSENDE funktioner, som modellen kan bede om. Data hentes DIREKTE fra kundens
regnskabssystem via adapter-laget (`hent_adapter`) – med kundens token, som kun adapter-laget kender.

Faste rammer:
- Kun læsning. Der findes ingen funktion her, der skriver noget – heller ikke til kassekladder.
- Serveren kontrollerer ved HVERT opslag, at kunden er samtalens kunde, OG at medarbejderen stadig må slå op
  hos den (ansvarlig – eller administrator). Et kunde-id fra modellen bruges aldrig ukontrolleret.
- Højst ASSISTANT_MAX_ROWS rækker pr. opslag. Er der flere, afkortes listen, og svaret siger det.
- Personoplysninger fjernes, før data når modellen: CPR-numre, bank-/kontonumre og teksten på
  posteringer på lønkonti (der står ofte medarbejdernavne).
- Hvert opslag skrives i `assistant_messages.lookups` (funktion, kunde, argumenter, antal rækker).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from app.adaptere.adgang import AdgangMangler
from app.adaptere.regnskab.base import ForMangeKald, hent_adapter
from app.config import get_settings
from app.db import ny_session
from app.kunder.models import Client
from app.personale.models import Staff

log = logging.getLogger(__name__)

SYSTEMNAVNE = {"economic": "e-conomic", "dinero": "Dinero"}

# --- Personoplysninger --------------------------------------------------------

_CPR = re.compile(r"(?<!\d)\d{6}[- ]?\d{4}(?!\d)")
_IBAN = re.compile(r"\bDK\d{2}(?: ?\d{4}){3} ?\d{2}\b", re.IGNORECASE)
_BANKKONTO = re.compile(r"(?<!\d)\d{4}[- ]\d{6,10}(?!\d)")
# Konti, hvor posteringsteksten ofte indeholder medarbejdernavne. Teksten skjules – beløbene bliver.
LOENORD = ("løn", "gage", "feriepenge", "feriegodtgørelse", "atp", "am-bidrag", "a-skat", "pension")


def skjul_tekst(tekst) -> str | None:
    """Fjern CPR-numre og bank-/kontonumre fra en tekst. Tal laves til tekst først (e-conomic sender fx
    fakturanumre som tal)."""
    if tekst is None:
        return None
    tekst = str(tekst)
    tekst = _IBAN.sub("[kontonr. skjult]", tekst)
    tekst = _BANKKONTO.sub("[kontonr. skjult]", tekst)
    return _CPR.sub("[CPR skjult]", tekst)


def er_loenkonto(kontonavn: str | None) -> bool:
    navn = (kontonavn or "").lower()
    return any(o in navn for o in LOENORD)


# --- Funktionerne, som modellen får at se --------------------------------------

_KUNDE = {"client_id": {"type": "integer", "description": "Kundens id (samtalens kunde)"}}
_PERIODE = {
    "fra": {"type": "string", "description": "Første dag i perioden, ÅÅÅÅ-MM-DD"},
    "til": {"type": "string", "description": "Sidste dag i perioden, ÅÅÅÅ-MM-DD"},
}
_KONTO = {"konto": {"type": "integer", "description": "Kontonummer i kundens kontoplan"}}


def _skema(egenskaber: dict) -> dict:
    return {"type": "object", "properties": egenskaber, "required": list(egenskaber), "additionalProperties": False}


VAERKTOEJER = [
    {"name": "get_kontoplan", "description": "Kundens kontoplan: kontonummer, navn, type, momskode og saldo.",
     "input_schema": _skema(_KUNDE)},
    {"name": "get_saldo", "description": "Summen af bevægelserne på én konto i en periode (ikke primosaldo) "
                                         "og antal posteringer.",
     "input_schema": _skema({**_KUNDE, **_KONTO, **_PERIODE})},
    {"name": "get_posteringer", "description": "De enkelte posteringer på én konto i en periode.",
     "input_schema": _skema({**_KUNDE, **_KONTO, **_PERIODE,
                             "limit": {"type": "integer", "description": "Højst så mange rækker"}})},
    {"name": "get_aabne_debitorer", "description": "Kundens åbne (ubetalte) poster hos debitorer.",
     "input_schema": _skema(_KUNDE)},
    {"name": "get_aabne_kreditorer", "description": "Kundens åbne (ubetalte) poster hos kreditorer.",
     "input_schema": _skema(_KUNDE)},
    {"name": "get_moms", "description": "Bevægelserne på kundens momskonti i en periode. Momskontiene findes ud fra "
                                        "momskoderne i kundens egen opsætning. Er IKKE en momsangivelse.",
     "input_schema": _skema({**_KUNDE, **_PERIODE})},
    {"name": "get_kundeinfo", "description": "Kundens navn, CVR, regnskabsår og regnskabssystem.",
     "input_schema": _skema(_KUNDE)},
]
NAVNE = tuple(v["name"] for v in VAERKTOEJER)


class OpslagsFejl(Exception):
    """Et opslag, der ikke kan udføres. Beskeden er dansk og sendes til modellen – aldrig tekniske detaljer."""


def _dato(argumenter: dict, navn: str) -> date:
    try:
        return date.fromisoformat(str(argumenter[navn]))
    except (KeyError, ValueError):
        raise OpslagsFejl(f"'{navn}' skal være en dato som ÅÅÅÅ-MM-DD") from None


def _periode(argumenter: dict) -> tuple[date, date]:
    fra, til = _dato(argumenter, "fra"), _dato(argumenter, "til")
    if til < fra:
        raise OpslagsFejl("Perioden slutter, før den starter")
    return fra, til


def _konto(argumenter: dict) -> int:
    try:
        return int(argumenter["konto"])
    except (KeyError, TypeError, ValueError):
        raise OpslagsFejl("'konto' skal være et kontonummer") from None


def _kr(v: Decimal | None) -> str | None:
    return None if v is None else str(v.quantize(Decimal("0.01")))


def _beloeb(p) -> Decimal:
    """Beløbet i kundens grundvaluta. Mangler det, gættes der ikke."""
    if p.beloeb_dkk is not None:
        return p.beloeb_dkk
    raise OpslagsFejl(f"Postering {p.bogfoert_id} mangler beløb i grundvaluta – kan ikke lægges sammen")


@dataclass
class Opslag:
    """Opslag for ÉN samtale: én medarbejder og én kunde. Bruges af stream_svar, mens et svar laves."""

    staff_id: int
    client_id: int
    lookups: list = field(default_factory=list)
    afkortet: bool = False
    konti: set = field(default_factory=set)  # kontonumre, opslagene har vist modellen (bruges af prøven)
    _konti: list | None = None
    _perioder: dict = field(default_factory=dict)

    def udfoer(self, navn: str, argumenter: dict) -> dict:
        """Udfør ét opslag og log det. Returnerer det, modellen får – altid en dict, også ved fejl."""
        post = {"funktion": navn, "kunde_id": argumenter.get("client_id"), "argumenter": argumenter,
                "antal_raekker": 0, "tidspunkt": datetime.now(timezone.utc).isoformat()}
        try:
            with ny_session() as session:
                self._kontroller(session, argumenter.get("client_id"))
                if navn not in NAVNE:
                    raise OpslagsFejl(f"Ukendt opslag '{navn}'")
                resultat = getattr(self, "_" + navn)(session, argumenter)
            post["antal_raekker"] = resultat.pop("_antal", 0)
            if resultat.get("afkortet"):
                post["afkortet"] = True
                self.afkortet = True
            if resultat.get("konto") is not None:
                self.konti.add(resultat["konto"])
            self.konti.update(r["konto"] for r in resultat.get("raekker", []) if r.get("konto") is not None)
            return resultat
        except OpslagsFejl as fejl:
            post["fejl"] = str(fejl)
            return {"fejl": str(fejl)}
        except ForMangeKald:
            post["fejl"] = "for mange kald"
            return {"fejl": "Regnskabssystemet beder os vente lidt. Sig til medarbejderen, at opslaget kan prøves "
                            "igen om et øjeblik."}
        except AdgangMangler:
            post["fejl"] = "ingen adgang"
            return {"fejl": "Kunden har ingen aktiv adgang (token) til sit regnskabssystem i hub'en. Sig det til "
                            "medarbejderen."}
        except Exception as fejl:  # noqa: BLE001 – modellen får en kort dansk besked; aldrig den tekniske fejl
            log.error("Svends opslag %s fejlede (%s)", navn, type(fejl).__name__)
            post["fejl"] = "regnskabssystemet svarede med en fejl"
            return {"fejl": "Opslaget i kundens regnskabssystem mislykkedes. Sig det til medarbejderen."}
        finally:
            self.lookups.append(post)

    def _kontroller(self, session, client_id) -> None:
        """Kunde-id'et fra modellen bruges ALDRIG ukontrolleret."""
        from app.assistent.samtale import er_tilknyttet

        if client_id != self.client_id:
            raise OpslagsFejl("Afvist: du kan kun slå op hos samtalens kunde")
        medarbejder = session.get(Staff, self.staff_id)
        if medarbejder is None or not medarbejder.aktiv or not er_tilknyttet(session, medarbejder, self.client_id):
            raise OpslagsFejl("Afvist: medarbejderen må ikke slå op hos denne kunde")

    # --- Hentning (med genbrug inden for samme svar, så e-conomic ikke spørges to gange) --

    def _hent_konti(self, session) -> list:
        if self._konti is None:
            with hent_adapter(session, self.client_id) as p:
                self._konti = p.fetch_accounts()
        return self._konti

    def _hent_periode(self, session, fra: date, til: date) -> list:
        if (fra, til) not in self._perioder:
            with hent_adapter(session, self.client_id) as p:
                self._perioder[(fra, til)] = p.fetch_entries_period(fra, til)
        return self._perioder[(fra, til)]

    def _kontonavn(self, session, konto: int) -> str:
        for k in self._hent_konti(session):
            if k.kontonummer == konto:
                return k.navn
        raise OpslagsFejl(f"Konto {konto} findes ikke i kundens kontoplan")

    def _afkort(self, raekker: list, loft: int | None = None) -> dict:
        maks = get_settings().assistant_max_rows
        loft = maks if loft is None else max(1, min(int(loft), maks))
        ud = {"raekker": raekker[:loft], "antal_i_alt": len(raekker), "_antal": min(len(raekker), loft)}
        if len(raekker) > loft:
            ud["afkortet"] = True
            ud["besked"] = (f"Listen er afkortet til {loft} af {len(raekker)} rækker. Sig det i svaret, og hent "
                            "ikke videre – spørg hellere mere præcist (kortere periode eller én konto).")
        return ud

    # --- De syv opslag -----------------------------------------------------------

    def _get_kontoplan(self, session, a: dict) -> dict:
        raekker = [{"konto": k.kontonummer, "navn": skjul_tekst(k.navn), "type": k.kontotype,
                    "momskode": k.momskode, "saldo": _kr(k.saldo)} for k in self._hent_konti(session)]
        return self._afkort(raekker)

    def _get_saldo(self, session, a: dict) -> dict:
        konto, (fra, til) = _konto(a), _periode(a)
        navn = self._kontonavn(session, konto)
        poster = [p for p in self._hent_periode(session, fra, til) if p.kontonummer == konto]
        return {"konto": konto, "navn": skjul_tekst(navn), "fra": fra.isoformat(), "til": til.isoformat(),
                "bevaegelse_i_perioden": _kr(sum((_beloeb(p) for p in poster), Decimal(0))),
                "antal_posteringer": len(poster),
                "bemaerk": "Summen af bevægelser i perioden – ikke saldoen inkl. primo.", "_antal": 1}

    def _get_posteringer(self, session, a: dict) -> dict:
        konto, (fra, til) = _konto(a), _periode(a)
        navn = self._kontonavn(session, konto)
        skjul = er_loenkonto(navn)
        poster = sorted((p for p in self._hent_periode(session, fra, til) if p.kontonummer == konto),
                        key=lambda p: (p.dato, p.bogfoert_id))
        raekker = [{"dato": p.dato.isoformat() if p.dato else None, "bilag": p.bilagsnummer,
                    "tekst": "[tekst skjult – lønkonto]" if skjul else skjul_tekst(p.tekst),
                    "beloeb": _kr(p.beloeb_dkk), "valuta": p.valuta, "type": p.entry_type} for p in poster]
        ud = self._afkort(raekker, a.get("limit"))
        ud.update({"konto": konto, "navn": skjul_tekst(navn),
                   "sum_alle": _kr(sum((_beloeb(p) for p in poster), Decimal(0)))})
        return ud

    def _aabne(self, session, type_: str) -> dict:
        with hent_adapter(session, self.client_id) as p:
            poster = [x for x in p.fetch_open_entries() if x.type == type_]
            parter = ({k.kundenummer: k.navn for k in p.fetch_customers()} if type_ == "debitor"
                      else {k.leverandoernummer: k.navn for k in p.fetch_suppliers()})
        poster.sort(key=lambda x: (x.forfaldsdato or date.max, x.bogfoert_id))
        raekker = [{"nummer": x.partnummer, "navn": skjul_tekst(parter.get(x.partnummer)),
                    "bilag": x.bilagsnummer, "faktura": skjul_tekst(x.fakturanummer),
                    "dato": x.dato.isoformat() if x.dato else None,
                    "forfald": x.forfaldsdato.isoformat() if x.forfaldsdato else None,
                    "beloeb": _kr(x.beloeb), "rest": _kr(x.restbeloeb), "valuta": x.valuta} for x in poster]
        ud = self._afkort(raekker)
        ud["rest_i_alt_pr_valuta"] = {v: _kr(sum((x.restbeloeb for x in poster if x.valuta == v), Decimal(0)))
                                      for v in sorted({x.valuta or "?" for x in poster})}
        return ud

    def _get_aabne_debitorer(self, session, a: dict) -> dict:
        return self._aabne(session, "debitor")

    def _get_aabne_kreditorer(self, session, a: dict) -> dict:
        return self._aabne(session, "kreditor")

    def _get_moms(self, session, a: dict) -> dict:
        fra, til = _periode(a)
        with hent_adapter(session, self.client_id) as p:
            momskoder = p.fetch_vat_accounts()
        konti: dict[int, list[str]] = {}
        for m in momskoder:
            for nr in (m.kontonummer, m.modkontonummer):
                if nr is not None:
                    konti.setdefault(nr, [])
                    if m.kode not in konti[nr]:
                        konti[nr].append(m.kode)
        if not konti:
            raise OpslagsFejl("Kunden har ingen momskoder med en momskonto i sin opsætning")
        navne = {k.kontonummer: k.navn for k in self._hent_konti(session)}
        poster = self._hent_periode(session, fra, til)
        raekker = []
        for nr in sorted(konti):
            paa_konto = [x for x in poster if x.kontonummer == nr]
            raekker.append({"konto": nr, "navn": skjul_tekst(navne.get(nr)),
                            "momskoder": konti[nr], "antal_posteringer": len(paa_konto),
                            "bevaegelse_i_perioden": _kr(sum((_beloeb(x) for x in paa_konto), Decimal(0)))})
        ud = self._afkort(raekker)
        ud.update({"fra": fra.isoformat(), "til": til.isoformat(),
                   "bemaerk": "Bevægelser på momskontiene som de står i bogføringen. Det er IKKE en momsangivelse – "
                              "sig ikke, hvad der skal indberettes."})
        return ud

    def _get_kundeinfo(self, session, a: dict) -> dict:
        kunde = session.get(Client, self.client_id)
        with hent_adapter(session, self.client_id) as p:
            aar = p.fetch_accounting_years()
        return {"navn": kunde.navn, "kundenummer": kunde.kundenummer, "cvr": kunde.cvr,
                "regnskabssystem": SYSTEMNAVNE.get(kunde.regnskabssystem, kunde.regnskabssystem),
                "regnskabsaar": [{"navn": x.navn, "fra": x.fra.isoformat() if x.fra else None,
                                  "til": x.til.isoformat() if x.til else None, "afsluttet": x.lukket}
                                 for x in sorted(aar, key=lambda x: x.fra or date.min)],
                "_antal": 1}
