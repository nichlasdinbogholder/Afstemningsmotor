"""Fælles udtræk af fund til visning (kommandolinjen og webdelen).

Et fund er AKTUELT, når seneste kørsel af dets regel for kunden stadig fandt det
(last_seen_at >= seneste koert_at). Fund slettes aldrig; ikke-aktuelle fund vises kun på ønske.
"""

from sqlalchemy import Select, exists, select

from app.rules.models import Finding, RuleRun

ORDEN = {"high": 0, "medium": 1, "low": 2}

# Fund fra kassekladdekontrollen i natkørslen er ikke en registreret regel – navnet står her.
EKSTRA_REGELNAVNE = {"fejlkonto_kassekladde": "Postering på fejlkonto i kassekladde"}


def regelnavne() -> dict[str, str]:
    from app.rules.base import alle_regler

    return {**{r.code: r.name_da for r in alle_regler()}, **EKSTRA_REGELNAVNE}


def aktuel_udtryk():
    """Sandt, når ingen kørsel af fundets regel for kunden er sket EFTER fundet sidst blev set
    (= seneste kørsel fandt det stadig). Slås op i indekset (client_id, rule_code, koert_at)."""
    return ~exists().where(RuleRun.client_id == Finding.client_id, RuleRun.rule_code == Finding.rule_code,
                           RuleRun.koert_at > Finding.last_seen_at)


def fund_med_aktuel(client_id: int | None = None) -> Select:
    """select(Finding, aktuel) – evt. begrænset til én kunde."""
    stmt = select(Finding, aktuel_udtryk().label("aktuel"))
    if client_id is not None:
        stmt = stmt.where(Finding.client_id == client_id)
    return stmt


def sorteringsnoegle(f: Finding):
    return (ORDEN.get(f.severity, 9), f.period_start or f.first_seen_at.date(), f.id)
