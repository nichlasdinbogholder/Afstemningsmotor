"""Matchmotor: kontoudtog (statement_lines) mod bogføringen (entries).

For hvert kontoudtog sammenlignes linjerne med posteringerne på udtogets modstykke
(finanskonto og/eller kunde/leverandør) i udtogets periode:

  Trin 1 – eksakt: udtogets reference = bilagsnummer eller leverandørens fakturanummer
           (feltet "Fakturanr." ved bogføringen) OG samme beløb. Foranstillede nuller tæller ikke.
  Trin 2 – samme beløb og dato inden for ±DATO_TOLERANCE dage.
  Trin 3 – resten er umatchet og bliver til fund:
           `mangler_i_bogfoering`   – linje på udtoget uden postering i bogføringen (ligger den
                                      i en ubogført kassekladde, står det i fundet)
           `mangler_paa_kontoudtog` – postering i perioden uden linje på udtoget

Hver linje og hver postering kan kun bruges i ét match. Inden for et trin vælges
parret med den mindste datoforskel først. Udtogets fortegn ("samme"/"modsat") vendes,
før der sammenlignes – så motoren er ens for grossister, skattekontoen og banken.
Matchet gemmes på linjen (match_entry_id, match_trin), så matchprocenten kan måles
(scripts/matchprocent.sql). Læser kun vores egen database.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.kontoudtog.models import Statement, StatementLine
from app.rules.base import FindingDraft, registrer_regel

DATO_TOLERANCE = 5  # dage (trin 2)


@dataclass
class UdtogResultat:
    udtog: Statement
    umatchede_linjer: list[StatementLine] = field(default_factory=list)
    umatchede_poster: list = field(default_factory=list)  # rækker fra POSTER_SQL
    matchet: int = 0


def _norm_ref(tekst) -> str | None:
    t = str(tekst).strip().upper().lstrip("0") if tekst is not None else ""
    return t or None


def _samme_reference(reference: str | None, p) -> bool:
    ref = _norm_ref(reference)
    return ref is not None and ref in (_norm_ref(p.bilagsnummer), _norm_ref(p.fakturanummer))


def _bogfoert_beloeb(udtog: Statement, linje: StatementLine) -> Decimal:
    return linje.beloeb if udtog.fortegn == "samme" else -linje.beloeb


def _par(linjer, poster, betingelse, udtog, max_dage=None) -> list[tuple]:
    """Mulige par (linje, postering) med samme beløb – sorteret med mindst datoforskel først.
    Posteringerne slås op efter beløb, så det ikke bliver "alle mod alle"."""
    efter_beloeb: dict[Decimal, list] = {}
    for p in poster:
        efter_beloeb.setdefault(p.beloeb, []).append(p)
    par = []
    for l in linjer:
        for p in efter_beloeb.get(_bogfoert_beloeb(udtog, l), ()):
            if not betingelse(l, p):
                continue
            dage = abs((p.dato - l.dato).days) if p.dato else 10**6
            if max_dage is not None and dage > max_dage:
                continue
            par.append((dage, l.dato, l.id, p.id, l, p))
    return sorted(par, key=lambda x: x[:4])


# Posteringerne læses med SQL (ikke via tabelmodellen), så regel-laget ikke trækker
# adapter-laget med ind – regler må aldrig kunne nå et regnskabssystem.
POSTER_SQL = text("""
    SELECT id, bogfoert_id, bilagsnummer, fakturanummer, dato, kontonummer, tekst, beloeb
    FROM entries
    WHERE client_id = :client_id
      AND dato BETWEEN :fra AND :til
      AND (CAST(:konto AS integer) IS NULL OR kontonummer = :konto)
      AND (CAST(:modpart AS varchar) IS NULL OR modpart = :modpart)
    ORDER BY dato, bogfoert_id
""")


def match_udtog(session: Session, udtog: Statement) -> UdtogResultat:
    linjer = list(session.scalars(select(StatementLine).where(StatementLine.statement_id == udtog.id)
                                  .order_by(StatementLine.linje_nr)))
    tolerance = timedelta(days=DATO_TOLERANCE)
    poster = session.execute(POSTER_SQL, {
        "client_id": udtog.client_id, "konto": udtog.kontonummer, "modpart": udtog.modpart,
        "fra": udtog.periode_fra - tolerance, "til": udtog.periode_til + tolerance,
    }).all()

    match: dict[int, tuple] = {}  # linje-id -> (postering, trin)
    brugte: set[int] = set()
    trin = [
        ("bilag_beloeb", lambda l, p: _samme_reference(l.reference, p), None),
        ("beloeb_dato", lambda l, p: True, DATO_TOLERANCE),
    ]
    for navn, betingelse, max_dage in trin:
        frie_linjer = [l for l in linjer if l.id not in match]
        frie_poster = [p for p in poster if p.id not in brugte]
        for _, _, _, _, l, p in _par(frie_linjer, frie_poster, betingelse, udtog, max_dage):
            if l.id in match or p.id in brugte:
                continue
            match[l.id] = (p, navn)
            brugte.add(p.id)

    # Gem matchet på linjerne (nulstil først – resultatet afhænger af de aktuelle data).
    session.execute(update(StatementLine).where(StatementLine.statement_id == udtog.id)
                    .values(match_entry_id=None, match_trin=None))
    for linje_id, (p, navn) in match.items():
        session.execute(update(StatementLine).where(StatementLine.id == linje_id)
                        .values(match_entry_id=p.id, match_trin=navn))
    return UdtogResultat(
        udtog=udtog,
        umatchede_linjer=[l for l in linjer if l.id not in match],
        # Kun posteringer INDEN FOR perioden kan mangle på udtoget (tolerancen er kun til match).
        umatchede_poster=[p for p in poster if p.id not in brugte
                          and p.dato is not None and udtog.periode_fra <= p.dato <= udtog.periode_til],
        matchet=len(match),
    )


def match_kunde(session: Session, client_id: int) -> list[UdtogResultat]:
    udtog = session.scalars(select(Statement).where(Statement.client_id == client_id)
                            .order_by(Statement.periode_fra, Statement.id))
    return [match_udtog(session, u) for u in udtog]


def _match_til_regel(session: Session, client_id: int) -> list[UdtogResultat]:
    """De to regler bruger samme matchning: inden for én kørsel af koer_regler matches kunden
    kun én gang (resultatet ligger i session.info["regel_cache"], som koer_regler rydder)."""
    cache = session.info.get("regel_cache")
    if cache is None:  # reglen køres alene
        return match_kunde(session, client_id)
    noegle = ("matchmotor", client_id)
    if noegle not in cache:
        cache[noegle] = match_kunde(session, client_id)
    return cache[noegle]


# --- Fund ----------------------------------------------------------------------------


def _modstykke(u: Statement) -> str:
    dele = []
    if u.modpart:
        dele.append(u.modpart.replace(":", " "))
    if u.kontonummer is not None:
        dele.append(f"konto {u.kontonummer}")
    return ", ".join(dele)


def _kr(beloeb: Decimal) -> str:
    return f"{beloeb:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kr."


def _hash(*dele) -> str:
    return hashlib.sha256("|".join(str(d) for d in dele).encode()).hexdigest()[:32]


def _udtog_detail(u: Statement) -> dict:
    return {"kontoudtog_id": u.id, "kilde": u.kilde, "kontonummer": u.kontonummer, "modpart": u.modpart,
            "periode_fra": u.periode_fra.isoformat(), "periode_til": u.periode_til.isoformat(),
            "fortegn": u.fortegn, "kildefil": u.kildefil}


KLADDE_SQL = text("""
    SELECT kladde_nummer, kladde_navn, bilagsnummer, dato, konto, modkonto, modpart, fakturanummer, beloeb
    FROM journal_entries WHERE client_id = :client_id
""")


def _i_kassekladde(kladde: list, u: Statement, l: StatementLine):
    """Ligger linjen klar i en kassekladde (ikke bogført)? Først på fakturanummer, ellers samme
    beløb inden for DATO_TOLERANCE på udtogets leverandør/konto. Fortegnet i en kladde følger
    kladdens egen logik, så der sammenlignes uden fortegn."""
    ref = _norm_ref(l.reference)
    if ref:
        for k in kladde:
            if _norm_ref(k.fakturanummer) == ref:
                return k
    beloeb = abs(l.beloeb)
    for k in kladde:
        if (k.beloeb is not None and abs(k.beloeb) == beloeb and k.dato is not None
                and abs((k.dato - l.dato).days) <= DATO_TOLERANCE
                and ((u.modpart and k.modpart == u.modpart)
                     or (u.kontonummer is not None and u.kontonummer in (k.konto, k.modkonto)))):
            return k
    return None


@registrer_regel
class ManglerIBogfoering:
    code = "mangler_i_bogfoering"
    version = 1
    name_da = "Mangler i bogføring"

    def run(self, session: Session, client_id: int, since: date | None) -> list[FindingDraft]:
        udkast = []
        kladde = list(session.execute(KLADDE_SQL, {"client_id": client_id}))
        for r in _match_til_regel(session, client_id):
            u = r.udtog
            set_foer: Counter = Counter()
            for l in r.umatchede_linjer:
                # Fingerprint uden udtogets id, så et genindlæst udtog giver de SAMME fund.
                noegle = (client_id, u.kilde, u.kontonummer, u.modpart, l.dato, (l.reference or "").strip(),
                          l.beloeb)
                set_foer[noegle] += 1
                k = _i_kassekladde(kladde, u, l)
                i_kladde = (f" – ligger i kassekladde {k.kladde_navn or k.kladde_nummer}"
                            f"{f' (bilag {k.bilagsnummer})' if k.bilagsnummer else ''}, ikke bogført") if k else ""
                udkast.append(FindingDraft(
                    fingerprint=_hash("linje", *noegle, set_foer[noegle]),
                    severity="medium",
                    title=(f"Mangler i bogføring: {_kr(l.beloeb)} den {l.dato:%d.%m.%Y}"
                           f"{f' (ref. {l.reference})' if l.reference else ''} – kontoudtog fra "
                           f"{u.kilde}, {_modstykke(u)}{i_kladde}"),
                    detail={**_udtog_detail(u), "linje_nr": l.linje_nr, "dato": l.dato.isoformat(),
                            "reference": l.reference, "tekst": l.tekst, "beloeb_paa_udtog": str(l.beloeb),
                            "beloeb_i_bogfoering": str(_bogfoert_beloeb(u, l)),
                            "kassekladde": ({"nummer": k.kladde_nummer, "navn": k.kladde_navn,
                                             "bilagsnummer": k.bilagsnummer,
                                             "dato": k.dato.isoformat() if k.dato else None,
                                             "beloeb": str(k.beloeb)} if k else None)},
                    entry_ids=[], period_start=l.dato, period_end=l.dato,
                ))
        return udkast


@registrer_regel
class ManglerPaaKontoudtog:
    code = "mangler_paa_kontoudtog"
    version = 1
    name_da = "Mangler på kontoudtog"

    def run(self, session: Session, client_id: int, since: date | None) -> list[FindingDraft]:
        udkast = []
        for r in _match_til_regel(session, client_id):
            u = r.udtog
            for p in r.umatchede_poster:
                udkast.append(FindingDraft(
                    fingerprint=_hash("post", client_id, u.kilde, u.kontonummer, u.modpart, p.bogfoert_id),
                    severity="medium",
                    title=(f"Mangler på kontoudtog: {_kr(p.beloeb)} bogført {p.dato:%d.%m.%Y}"
                           f"{f' (bilag {p.bilagsnummer})' if p.bilagsnummer else ''} – "
                           f"{u.kilde}, {_modstykke(u)}"),
                    detail={**_udtog_detail(u), "posteringsnummer": p.bogfoert_id,
                            "bilagsnummer": p.bilagsnummer, "dato": p.dato.isoformat(),
                            "konto": p.kontonummer, "tekst": p.tekst, "beloeb": str(p.beloeb)},
                    entry_ids=[p.id], period_start=p.dato, period_end=p.dato,
                ))
        return udkast
