"""Afstemningsreglerne 1–3 mod open_entries: hvad de finder, hvad de lader være, og idempotens."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.afstemning.models import Finding
from app.afstemning.regler import koer_regler
from app.kunder.models import Client
from app.regnskab.models import OpenEntryCache

DATO = date(2026, 10, 4)  # fast beregningsdato -> 6 måneder før = 2026-04-04


def _kunde(session, nr, status="aktiv"):
    k = Client(navn=f"Regel {nr}", kundenummer=nr, regnskabssystem="economic", status=status)
    session.add(k)
    session.flush()
    return k


_naeste_id = iter(range(1, 10_000))


def _post(session, kunde, *, type="debitor", part=1, beloeb="1000.00", rest="1000.00",
          valuta="DKK", entry_type="customerInvoice", forfald=date(2026, 9, 1)):
    p = OpenEntryCache(
        client_id=kunde.id, bogfoert_id=next(_naeste_id), type=type, partnummer=part,
        partnavn=f"Part {part}", bilagsnummer=1, fakturanummer="F1", dato=date(2026, 8, 1),
        forfaldsdato=forfald, beloeb=Decimal(beloeb), restbeloeb=Decimal(rest), valuta=valuta,
        entry_type=entry_type,
    )
    session.add(p)
    session.flush()
    return p.bogfoert_id


def _fund(session, kunde, regel):
    return set(session.scalars(select(Finding.kilde_id).where(
        Finding.client_id == kunde.id, Finding.regel == regel)))


def _koer(session, kunde):
    return koer_regler(session, kunde.id, DATO)


# --- Regel 1: udlignede poster med restbeløb 0,01–99,99 kr. -----------------


def test_regel_1_finder_smaa_restbeloeb_og_graenserne(db_session):
    k = _kunde(db_session, "R1")
    med = {
        _post(db_session, k, beloeb="1000.00", rest="0.01"),        # nedre grænse
        _post(db_session, k, beloeb="1000.00", rest="99.99"),       # øvre grænse
        _post(db_session, k, type="kreditor", beloeb="-500.00", rest="-12.50",
              entry_type="supplierInvoice"),                        # kreditor: negative beløb
    }
    uden = {
        _post(db_session, k, beloeb="1000.00", rest="100.00"),      # for stort
        _post(db_session, k, beloeb="50.00", rest="50.00"),         # ikke udlignet (intet betalt)
        _post(db_session, k, beloeb="1000.00", rest="5.00", valuta="EUR"),  # ikke kr.
        _post(db_session, k, beloeb="1000.00", rest="5.00", valuta=None),   # ukendt valuta
    }
    _koer(db_session, k)
    assert _fund(db_session, k, "smaa_restbeloeb") == med
    assert not (_fund(db_session, k, "smaa_restbeloeb") & uden)


# --- Regel 2: betaling uden faktura at udligne ------------------------------


def test_regel_2_betaling_uden_aaben_faktura(db_session):
    k = _kunde(db_session, "R2")
    # Part 1: betaling, ingen åben faktura -> fund
    alene = _post(db_session, k, part=1, beloeb="-800.00", rest="-800.00", entry_type="customerPayment")
    # Part 2: betaling + åben faktura hos samme kunde -> ikke fund
    _post(db_session, k, part=2, beloeb="-300.00", rest="-300.00", entry_type="customerPayment")
    _post(db_session, k, part=2, beloeb="300.00", rest="300.00", entry_type="customerInvoice")
    # Part 3: faktura findes, men hos en ANDEN part -> part 3's betaling er stadig et fund
    p3 = _post(db_session, k, part=3, beloeb="-100.00", rest="-100.00", entry_type="customerPayment")
    _post(db_session, k, part=4, beloeb="100.00", rest="100.00", entry_type="customerInvoice")
    # Kreditor: betaling uden åben leverandørfaktura -> fund
    kred = _post(db_session, k, type="kreditor", part=9, beloeb="2000.00", rest="2000.00",
                 entry_type="supplierPayment")
    # Ukendt posttype (ikke synkroniseret endnu) -> aldrig fund
    _post(db_session, k, part=5, beloeb="-50.00", rest="-50.00", entry_type=None)

    _koer(db_session, k)
    assert _fund(db_session, k, "betaling_uden_faktura") == {alene, p3, kred}


def test_regel_2_faktura_med_samme_fortegn_kan_ikke_udligne(db_session):
    k = _kunde(db_session, "R2b")
    # En kreditnota (negativ) kan ikke udligne en negativ betaling – kun modsat fortegn tæller.
    b = _post(db_session, k, part=1, beloeb="-200.00", rest="-200.00", entry_type="customerPayment")
    _post(db_session, k, part=1, beloeb="-50.00", rest="-50.00", entry_type="customerInvoice")
    _koer(db_session, k)
    assert _fund(db_session, k, "betaling_uden_faktura") == {b}


# --- Regel 3: forfalden mere end 6 måneder tilbage --------------------------


def test_regel_3_mere_end_6_maaneder_og_graensen(db_session):
    k = _kunde(db_session, "R3")
    gammel = _post(db_session, k, forfald=date(2026, 4, 3))       # 6 mdr + 1 dag -> fund
    praecis = _post(db_session, k, forfald=date(2026, 4, 4))      # præcis 6 mdr -> ikke fund
    _post(db_session, k, forfald=date(2026, 9, 1))                # nyere -> ikke fund
    _post(db_session, k, forfald=None)                            # ingen forfaldsdato -> ikke fund
    kred = _post(db_session, k, type="kreditor", beloeb="-10.00", rest="-10.00",
                 entry_type="supplierInvoice", forfald=date(2025, 1, 31))
    _koer(db_session, k)
    assert _fund(db_session, k, "forfalden_over_6_mdr") == {gammel, kred}
    assert praecis not in _fund(db_session, k, "forfalden_over_6_mdr")


# --- Idempotens og afgrænsning ----------------------------------------------


def test_reglerne_er_idempotente(db_session):
    k = _kunde(db_session, "ID")
    _post(db_session, k, rest="10.00")
    _post(db_session, k, part=7, beloeb="-80.00", rest="-80.00", entry_type="customerPayment")
    _post(db_session, k, forfald=date(2025, 1, 1))

    foerste = _koer(db_session, k)
    antal_foerst = db_session.scalar(select(func.count()).select_from(Finding).where(Finding.client_id == k.id))
    anden = _koer(db_session, k)
    antal_efter = db_session.scalar(select(func.count()).select_from(Finding).where(Finding.client_id == k.id))

    assert sum(foerste.values()) == antal_foerst > 0
    assert anden == {"lukket": 0, "smaa_restbeloeb": 0, "betaling_uden_faktura": 0,
                     "forfalden_over_6_mdr": 0}
    assert antal_efter == antal_foerst


def test_databasen_afviser_dublet_i_findings(db_session):
    k = _kunde(db_session, "DUB")
    sql = text("INSERT INTO findings (client_id, regel, kilde_id, beskrivelse) "
               "VALUES (:k, 'smaa_restbeloeb', 1, 'x')")
    db_session.execute(sql, {"k": k.id})
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(sql, {"k": k.id})


@pytest.mark.parametrize("status", ["pause", "opsagt"])
def test_kunder_paa_pause_eller_opsagt_faar_ingen_fund(db_session, status):
    k = _kunde(db_session, f"P-{status}", status=status)
    _post(db_session, k, rest="10.00")
    _post(db_session, k, forfald=date(2024, 1, 1))
    assert sum(_koer(db_session, k).values()) == 0


def test_en_kunde_ad_gangen_roerer_ikke_andre(db_session):
    a, b = _kunde(db_session, "A1"), _kunde(db_session, "B1")
    _post(db_session, a, rest="10.00")
    _post(db_session, b, rest="10.00")
    koer_regler(db_session, a.id, DATO)
    assert _fund(db_session, a, "smaa_restbeloeb") and not _fund(db_session, b, "smaa_restbeloeb")


# --- Automatisk lukning og genåbning ----------------------------------------


def _finding(session, kunde, regel, kilde_id):
    session.expire_all()
    return session.scalars(select(Finding).where(
        Finding.client_id == kunde.id, Finding.regel == regel, Finding.kilde_id == kilde_id)).one()


def test_betalt_post_lukker_fundet_med_aarsag(db_session):
    k = _kunde(db_session, "L1")
    post = _post(db_session, k, forfald=date(2025, 1, 1))
    _koer(db_session, k)
    assert _finding(db_session, k, "forfalden_over_6_mdr", post).status == "aaben"

    # Posten betales: den forsvinder fra open_entries ved næste synkronisering.
    db_session.execute(text("DELETE FROM open_entries WHERE client_id = :k AND bogfoert_id = :b"),
                       {"k": k.id, "b": post})
    resultat = _koer(db_session, k)

    f = _finding(db_session, k, "forfalden_over_6_mdr", post)
    assert resultat["lukket"] == 1
    assert f.status == "loest" and f.loest_tidspunkt is not None
    assert f.loest_aarsag == "Posten er ikke længere åben (udlignet eller betalt)"
    assert _koer(db_session, k)["lukket"] == 0  # idempotent: intet at lukke anden gang


def test_aendret_restbeloeb_lukker_fundet_og_kan_genaabne_det(db_session):
    k = _kunde(db_session, "L2")
    post = _post(db_session, k, beloeb="1000.00", rest="10.00")
    _koer(db_session, k)
    assert _finding(db_session, k, "smaa_restbeloeb", post).status == "aaben"

    # Udligningen tilbageføres delvist: restbeløbet stiger til 150 kr. -> uden for reglen.
    db_session.execute(text("UPDATE open_entries SET restbeloeb = 150 WHERE client_id = :k AND bogfoert_id = :b"),
                       {"k": k.id, "b": post})
    _koer(db_session, k)
    f = _finding(db_session, k, "smaa_restbeloeb", post)
    assert f.status == "loest" and f.loest_aarsag == "Reglens betingelse er ikke længere opfyldt"

    # Restbeløbet falder igen til 20 kr. -> samme fund genåbnes (ingen dublet).
    db_session.execute(text("UPDATE open_entries SET restbeloeb = 20 WHERE client_id = :k AND bogfoert_id = :b"),
                       {"k": k.id, "b": post})
    resultat = _koer(db_session, k)
    f = _finding(db_session, k, "smaa_restbeloeb", post)
    assert resultat["smaa_restbeloeb"] == 1
    assert (f.status, f.loest_tidspunkt, f.loest_aarsag, f.restbeloeb) == ("aaben", None, None, Decimal("20.00"))
    assert db_session.scalar(select(func.count()).select_from(Finding).where(
        Finding.client_id == k.id, Finding.kilde_id == post)) == 1


def test_regel_2_lukkes_naar_en_faktura_dukker_op(db_session):
    k = _kunde(db_session, "L3")
    betaling = _post(db_session, k, part=1, beloeb="-500.00", rest="-500.00", entry_type="customerPayment")
    _koer(db_session, k)
    _post(db_session, k, part=1, beloeb="500.00", rest="500.00", entry_type="customerInvoice")
    _koer(db_session, k)
    f = _finding(db_session, k, "betaling_uden_faktura", betaling)
    assert f.status == "loest" and f.loest_aarsag == "Reglens betingelse er ikke længere opfyldt"


def test_afviste_fund_roeres_aldrig(db_session):
    k = _kunde(db_session, "L4")
    post = _post(db_session, k, forfald=date(2025, 1, 1))
    _koer(db_session, k)
    db_session.execute(text("UPDATE findings SET status = 'afvist' WHERE client_id = :k"), {"k": k.id})
    db_session.execute(text("DELETE FROM open_entries WHERE client_id = :k"), {"k": k.id})
    assert _koer(db_session, k)["lukket"] == 0
    assert _finding(db_session, k, "forfalden_over_6_mdr", post).status == "afvist"


def test_fund_hos_kunde_paa_pause_lukkes_ikke(db_session):
    k = _kunde(db_session, "L5")
    post = _post(db_session, k, forfald=date(2025, 1, 1))
    _koer(db_session, k)
    k.status = "pause"
    db_session.flush()
    db_session.execute(text("DELETE FROM open_entries WHERE client_id = :k"), {"k": k.id})
    assert _koer(db_session, k)["lukket"] == 0
    assert _finding(db_session, k, "forfalden_over_6_mdr", post).status == "aaben"


def test_lukning_af_en_kunde_roerer_ikke_andre(db_session):
    a, b = _kunde(db_session, "LA"), _kunde(db_session, "LB")
    pa = _post(db_session, a, forfald=date(2025, 1, 1))
    pb = _post(db_session, b, forfald=date(2025, 1, 1))
    koer_regler(db_session, None, DATO)
    db_session.execute(text("DELETE FROM open_entries WHERE client_id IN (:a, :b)"), {"a": a.id, "b": b.id})
    koer_regler(db_session, a.id, DATO)
    assert _finding(db_session, a, "forfalden_over_6_mdr", pa).status == "loest"
    assert _finding(db_session, b, "forfalden_over_6_mdr", pb).status == "aaben"
