"""Betalingsnøglen (FIK +71): fakturanummer + to kontrolcifre – eksemplet fra FarPay (faktura 742)."""

import pytest

from app.opkraevning.fik import UgyldigFIK, betalings_id, fik_linje, kontrolciffer, laes_fik


def test_eksemplet_fra_farpay():
    assert betalings_id(742) == "000000000074203"
    assert fik_linje("742", "80679858") == "+71<000000000074203 +80679858<"


def test_aflaesning_giver_fakturanummer_og_kunde():
    f = laes_fik("+71<000000000074203 +80679858<")
    assert (f.fakturanummer, f.fi_kreditornummer) == ("742", "80679858")
    assert laes_fik("000000000074203").fakturanummer == "742"
    assert laes_fik(" +71< 000000000074203 +80679858 ").fakturanummer == "742"


@pytest.mark.parametrize("nr", [1, 9, 10, 586, 7420, 123456789012])
def test_frem_og_tilbage(nr):
    assert laes_fik(betalings_id(nr)).fakturanummer == str(nr)


@pytest.mark.parametrize("tekst", [
    "000000000074204",                 # forkert kontrolciffer
    "00000000074203",                  # 14 cifre
    "+73<000000000074203 +80679858<",  # anden kortart
    "+71<000000000074203 +8067985<",   # kreditornr. 7 cifre
    "000000000000000",                 # intet fakturanummer
    "faktura 742",
])
def test_gaetter_aldrig(tekst):
    with pytest.raises(UgyldigFIK):
        laes_fik(tekst)


def test_kontrolciffer_luhn():
    assert kontrolciffer("7992739871") == "3"   # standardeksemplet for Luhn
