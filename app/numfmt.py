"""Aniq foizlar: butun sonlar bilan, «yarimdan yuqoriga» yaxlitlash (12.5% -> 13%, 6.25% -> 6.3%).

Oddiy Python round() va «:.1f» juft songa yaxlitlaydi (12.5 -> 12, 6.25 -> 6.2) va suzuvchi nuqta xatosi
sababli ba'zan 28.5 ni 28 deb beradi. Bu modul Fraction (kasr) bilan hisoblaydi, shuning uchun
natija har doim maktabda o'rgatilgan qoidaga mos keladi.
"""
from __future__ import annotations

from fractions import Fraction
from math import floor

_LIMIT = 10 ** 6


def _fr(x) -> Fraction:
    if isinstance(x, (int, Fraction)):
        return Fraction(x)
    # suzuvchi nuqtali sonlar (masalan 0.285): asl kasrni tiklaymiz, 0.28499999... bo'lib qolmasin
    return Fraction(x).limit_denominator(_LIMIT)


def _half_up(f: Fraction) -> int:
    return floor(f + Fraction(1, 2))


def round_half_up(x, nd: int = 0):
    """x ni nd xonagacha «yarimdan yuqoriga» yaxlitlaydi. nd=0 bo'lsa int, aks holda float."""
    scale = 10 ** nd
    k = _half_up(_fr(x) * scale)
    return k if nd == 0 else float(Fraction(k, scale))


def pct_int(num, den) -> int:
    """num/den foizi, butun songa. den=0 bo'lsa 0."""
    if not den:
        return 0
    return _half_up(_fr(num) / _fr(den) * 100)


def pct_1(num, den) -> float:
    """num/den foizi, bitta kasrgacha (50.0, 33.3). den=0 bo'lsa 0.0."""
    if not den:
        return 0.0
    k = _half_up(_fr(num) / _fr(den) * 1000)
    return float(Fraction(k, 10))
