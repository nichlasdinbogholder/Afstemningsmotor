"""Aflæs et kontoudtog i PDF – uanset grossist og layout.

Udtogene ser forskellige ud (Bygma, Brdr. Dahl, AO, Davidsen, Fog …), så der er ingen
skabelon pr. grossist. I stedet:

1. Teksten hentes med ordenes placering på siden. Er PDF'en indscannet (et billede uden
   tekst), læses den med tekstgenkendelse (Tesseract) – lokalt, intet sendes ud.
2. Kolonneoverskriften findes (Beløb / Debet / Kredit / Saldo). Hver linje, der starter
   med en dato, er en post; beløbet tages fra beløbskolonnen (Kredit giver minus,
   Saldo-kolonnen springes over).
3. KONTROL: primosaldo + alle linjer skal give ultimosaldo – på øret. Gør de ikke det,
   er udtoget ikke læst sikkert, og det må ikke bruges (en medarbejder ser på det).

Beløbene gemmes med grossistens fortegn (faktura +, betaling/kreditnota −).
"""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

BELOEB = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}-?$|^-?\d+,\d{2}-?$")
DATO_SEP = re.compile(r"^(\d{1,2})[-./](\d{1,2})[-./](\d{2}|\d{4})$")
DATO_KORT = re.compile(r"^(\d{2})(\d{2})(\d{2})$")  # 310726 (fx Fog)
PERIODE = re.compile(r"(\d{1,2}[-./]\d{1,2}[-./]\d{2,4}|\d{6})\s*-\s*(\d{1,2}[-./]\d{1,2}[-./]\d{2,4}|\d{6})")

KOLONNER = {"beløb": "beloeb", "beloeb": "beloeb", "debet": "debet", "kredit": "kredit", "saldo": "saldo"}
PRIMO_ORD = ("primo", "overført", "overfoert")
ULTIMO_ORD = ("ultimo",)
ULTIMO_RESERVE = ("tilgodehavende",)


class PdfFejl(Exception):
    pass


@dataclass
class Ord:
    tekst: str
    x0: float
    x1: float


@dataclass
class PdfUdtog:
    linjer: list[dict]
    primo: Decimal | None
    ultimo: Decimal | None
    periode_fra: date | None
    periode_til: date | None
    ocr: bool = False
    advarsler: list[str] = field(default_factory=list)

    @property
    def sum_linjer(self) -> Decimal:
        return sum((l["beloeb"] for l in self.linjer), Decimal("0.00"))

    @property
    def kontrol_ok(self) -> bool:
        return self.ultimo is not None and (self.primo or Decimal("0")) + self.sum_linjer == self.ultimo

    def kontrol_tekst(self) -> str:
        p = self.primo if self.primo is not None else Decimal("0.00")
        u = "mangler" if self.ultimo is None else _kr(self.ultimo)
        status = "STEMMER" if self.kontrol_ok else "STEMMER IKKE"
        return f"Primo {_kr(p)} + {len(self.linjer)} linjer {_kr(self.sum_linjer)} = ultimo {u}: {status}"


def _kr(b: Decimal) -> str:
    return f"{b:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def tolk_pdf_beloeb(tekst: str) -> Decimal:
    t = tekst.strip()
    minus = t.startswith("-") or t.endswith("-")
    t = t.strip("-").replace(".", "").replace(",", ".")
    b = Decimal(t).quantize(Decimal("0.01"))
    return -b if minus else b


def tolk_pdf_dato(tekst: str, kort: bool = False) -> date | None:
    m = DATO_SEP.match(tekst) or (DATO_KORT.match(tekst) if kort else None)
    if not m:
        return None
    d, mdr, aar = (int(g) for g in m.groups())
    if aar < 100:
        aar += 2000
    try:
        return date(aar, mdr, d)
    except ValueError:
        return None


def _norm(tekst: str) -> str:
    return tekst.lower().strip(".:;,()")


# --- Ord med placering ----------------------------------------------------------------


def _linjer_fra_ord(ord_: list[tuple[float, float, float, str]], tolerance: float) -> list[list[Ord]]:
    """(top, x0, x1, tekst) -> linjer sorteret oppefra, ord sorteret fra venstre."""
    linjer: list[tuple[float, list[Ord]]] = []
    for top, x0, x1, t in sorted(ord_):
        if linjer and abs(linjer[-1][0] - top) <= tolerance:
            linjer[-1] = (top, linjer[-1][1] + [Ord(t, x0, x1)])  # følg linjen, hvis den "hopper" lidt
        else:
            linjer.append((top, [Ord(t, x0, x1)]))
    return [sorted(l, key=lambda o: o.x0) for _, l in linjer]


def _sider_med_tekst(sti: Path) -> list[list[list[Ord]]]:
    import pdfplumber

    sider = []
    with pdfplumber.open(sti) as pdf:
        for side in pdf.pages:
            ord_ = [(w["top"], w["x0"], w["x1"], w["text"]) for w in side.extract_words() if w.get("upright", True)]
            sider.append([_saml_roerende(l) for l in _linjer_fra_ord(ord_, tolerance=3)])
    return sider


def _saml_roerende(linje: list[Ord]) -> list[Ord]:
    """Nogle PDF'er (fx Stark) skriver tegnene enkeltvis, så ordene bliver til '2 3 -0 9 -2 6'.
    Ord, der rører hinanden (intet mellemrum), samles igen."""
    samlet: list[Ord] = []
    for o in linje:
        if samlet and abs(o.x0 - samlet[-1].x1) < 0.5:
            samlet[-1] = Ord(samlet[-1].tekst + o.tekst, samlet[-1].x0, o.x1)
        else:
            samlet.append(o)
    return samlet


def _sider_med_ocr(sti: Path) -> list[list[list[Ord]]]:
    import pdfplumber

    if shutil.which("tesseract") is None:
        raise PdfFejl("PDF'en er indscannet, og tekstgenkendelse (tesseract) er ikke installeret")
    sider = []
    with pdfplumber.open(sti) as pdf, tempfile.TemporaryDirectory() as mappe:
        for nr, side in enumerate(pdf.pages):
            billede = Path(mappe) / f"side{nr}.png"
            side.to_image(resolution=300).save(billede)
            tsv = subprocess.run(["tesseract", str(billede), "-", "-l", "dan", "--psm", "6", "tsv"],
                                 capture_output=True, text=True, check=True).stdout
            grupper: dict[tuple, list] = {}
            for raekke in tsv.splitlines()[1:]:
                f = raekke.split("\t")
                if len(f) < 12 or not f[11].strip():
                    continue
                noegle = (int(f[2]), int(f[3]), int(f[4]))  # blok, afsnit, linje
                v, top, b = int(f[6]), int(f[7]), int(f[8])
                grupper.setdefault(noegle, []).append((top, v, v + b, f[11].strip()))
            linjer = sorted(grupper.values(), key=lambda g: min(o[0] for o in g))
            sider.append([sorted((Ord(t, x0, x1) for _, x0, x1, t in g), key=lambda o: o.x0) for g in linjer])
    return sider


# --- Tolkning ----------------------------------------------------------------------


def _er_overskrift(linje: list[Ord]) -> dict[str, float] | None:
    kol = {KOLONNER[_norm(o.tekst)]: o.x1 for o in linje if _norm(o.tekst) in KOLONNER}
    if not ({"beloeb", "debet", "kredit"} & kol.keys()):
        return None
    if not any("dato" in _norm(o.tekst) for o in linje) and len(kol) < 2:
        return None
    return kol


def _foerste_dato(linje: list[Ord], kort: bool) -> tuple[int, date] | None:
    """Datoen, der starter linjen. Tekstgenkendelse kan sætte lidt støj foran (fx 'z')."""
    for i, o in enumerate(linje[:2]):
        d = tolk_pdf_dato(o.tekst, kort)
        if d:
            return i, d
        if len(o.tekst) > 3 or BELOEB.match(o.tekst):
            return None
    return None


def _vaelg_beloeb(beloeb: list[Ord], kol: dict[str, float]) -> Decimal | None:
    if not beloeb:
        return None
    if not kol:
        return tolk_pdf_beloeb(beloeb[0].tekst) if len(beloeb) == 1 else None
    valgt: dict[str, Ord] = {}
    for o in beloeb:  # højrestillede tal: nærmeste kolonne målt på højre kant
        navn = min(kol, key=lambda k: abs(kol[k] - o.x1))
        valgt.setdefault(navn, o)
    if "beloeb" in valgt:
        return tolk_pdf_beloeb(valgt["beloeb"].tekst)
    if "debet" in valgt or "kredit" in valgt:
        d = tolk_pdf_beloeb(valgt["debet"].tekst) if "debet" in valgt else Decimal("0.00")
        k = tolk_pdf_beloeb(valgt["kredit"].tekst) if "kredit" in valgt else Decimal("0.00")
        return d - k
    return None


def _saldo_paa_linje(linjer: list[list[Ord]], i: int, noegleord: tuple[str, ...]) -> Decimal | None:
    """Beløbet ud for et nøgleord – på samme linje, ellers lige under ordet på næste linje."""
    linje = linjer[i]
    pos = next((j for j, o in enumerate(linje) if any(_norm(o.tekst).startswith(n) for n in noegleord)), None)
    if pos is None:
        return None
    efter = [o for o in linje[pos:] if BELOEB.match(o.tekst)]
    if efter:
        return tolk_pdf_beloeb(efter[-1].tekst if any(n in ULTIMO_RESERVE for n in noegleord) else efter[0].tekst)
    if i + 1 < len(linjer):
        under = [o for o in linjer[i + 1] if BELOEB.match(o.tekst)]
        if under:
            x = (linje[pos].x0 + linje[pos].x1) / 2
            return tolk_pdf_beloeb(min(under, key=lambda o: abs((o.x0 + o.x1) / 2 - x)).tekst)
    return None


def tolk_sider(sider: list[list[list[Ord]]], ocr: bool = False) -> PdfUdtog:
    """Kernen: fra ord med placering til linjer, primo, ultimo og periode."""
    alle = [l for side in sider for l in side]
    starter = [l[0].tekst for l in alle if l] + [l[1].tekst for l in alle if len(l) > 1]
    kort = sum(bool(DATO_KORT.match(t)) for t in starter) > sum(bool(DATO_SEP.match(t)) for t in starter)

    linjer, primo, ultimo, reserve, advarsler = [], None, None, None, []
    for side in sider:
        kol: dict[str, float] | None = None
        for i, linje in enumerate(side):
            if not linje:
                continue
            ord_ = {_norm(o.tekst) for o in linje}
            if primo is None and any(o.startswith(PRIMO_ORD) for o in ord_):
                primo = _saldo_paa_linje(side, i, PRIMO_ORD)
            if any(o.startswith(ULTIMO_ORD) for o in ord_):
                ultimo = _saldo_paa_linje(side, i, ULTIMO_ORD) or ultimo
                break  # posterne slutter ved ultimo (bagefter kommer fx en forfaldsoversigt)
            if reserve is None and any(o.startswith(ULTIMO_RESERVE) for o in ord_):
                reserve = _saldo_paa_linje(side, i, ULTIMO_RESERVE)
            ny_kol = _er_overskrift(linje)
            if ny_kol:
                kol = ny_kol
                continue
            if kol is None:
                continue
            start = _foerste_dato(linje, kort)
            if start is None:
                continue
            idx, dato = start
            rest = linje[idx + 1:]
            beloeb = [o for o in rest if BELOEB.match(o.tekst)]
            b = _vaelg_beloeb(beloeb, kol)
            if b is None:
                if beloeb:
                    advarsler.append(f"Linje {' '.join(o.tekst for o in linje)!r}: beløbet kunne ikke placeres")
                continue
            tekst_ord = [o for o in rest if not BELOEB.match(o.tekst) and not tolk_pdf_dato(o.tekst, kort)]
            reference = None
            for o in tekst_ord[:2]:
                if o.tekst.isdigit() and len(o.tekst) >= 3:
                    reference = o.tekst
                    break
                if not o.tekst.isdigit():
                    break
            tekst = " ".join(o.tekst for o in tekst_ord if o.tekst != reference) or None
            linjer.append({"linje_nr": len(linjer) + 1, "dato": dato, "reference": reference, "tekst": tekst,
                           "beloeb": b, "raa_data": {"linje": " ".join(o.tekst for o in linje)}})

    if ultimo is None:
        ultimo = reserve
    fra = til = None
    for side in sider:
        for linje in side:
            m = PERIODE.search(" ".join(o.tekst for o in linje))
            if m and fra is None:
                f, t = tolk_pdf_dato(m.group(1), True), tolk_pdf_dato(m.group(2), True)
                if f and t and f <= t:
                    fra, til = f, t
    if fra is None and linjer:
        fra, til = min(l["dato"] for l in linjer), max(l["dato"] for l in linjer)
        advarsler.append("Ingen periode på udtoget – bruger første og sidste linjes dato")
    return PdfUdtog(linjer, primo, ultimo, fra, til, ocr, advarsler)


def laes_pdf(sti: Path) -> PdfUdtog:
    """Aflæs en PDF. Kontrollér altid `kontrol_ok`, før resultatet bruges."""
    try:
        sider = _sider_med_tekst(sti)
    except Exception as fejl:  # noqa: BLE001 – ødelagt/krypteret PDF
        raise PdfFejl(f"PDF'en kunne ikke åbnes: {type(fejl).__name__}") from None
    udtog = tolk_sider(sider)
    if not udtog.linjer or not udtog.kontrol_ok:
        try:
            ocr = tolk_sider(_sider_med_ocr(sti), ocr=True)
        except PdfFejl:
            if udtog.linjer:
                return udtog
            raise
        if ocr.kontrol_ok or not udtog.linjer:
            return ocr
    return udtog


def main(argv: list[str] | None = None) -> int:
    """Prøv aflæsningen af en PDF uden at gemme noget: python -m app.kontoudtog.pdf <fil.pdf>"""
    import sys

    sti = Path((argv or sys.argv[1:])[0])
    try:
        u = laes_pdf(sti)
    except PdfFejl as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2
    print(f"{sti.name}: {len(u.linjer)} linjer{' (tekstgenkendelse)' if u.ocr else ''}, "
          f"periode {u.periode_fra or '?'} – {u.periode_til or '?'}")
    for l in u.linjer:
        print(f"  {l['dato']:%d.%m.%Y}  {l['reference'] or '':>12}  {_kr(l['beloeb']):>14}  {l['tekst'] or ''}")
    for a in u.advarsler:
        print(f"  ADVARSEL: {a}")
    print(u.kontrol_tekst())
    return 0 if u.kontrol_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
