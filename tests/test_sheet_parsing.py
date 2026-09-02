"""Tests for the pure parsing helpers inside :mod:`pajemploi.sheet`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from comptine.sheet import (
    ColumnMap,
    HoursSource,
    _column_letter,
    _parse_hours,
    _parse_money,
    _parse_month_label,
    _reference,
    _row_to_month_input,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  2\u202f560,16  € ", Decimal("2560.16")),
        ("60,00 €", Decimal("60.00")),
        ("", Decimal("0")),
        ("  -    € ", Decimal("0")),
        ("1234,5", Decimal("1234.5")),
    ],
)
def test_parse_money(raw: str, expected: Decimal) -> None:
    assert _parse_money(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("151,67", Decimal("151.67")),
        ("89,30", Decimal("89.30")),
        ("0", Decimal("0")),
        ("", Decimal("0")),
    ],
)
def test_parse_hours(raw: str, expected: Decimal) -> None:
    assert _parse_hours(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("avr. 2026", date(2026, 4, 1)),
        ("janv. 2026", date(2026, 1, 1)),
        ("août 2026", date(2026, 8, 1)),
        ("déc. 2027", date(2027, 12, 1)),
    ],
)
def test_parse_month_label(raw: str, expected: date) -> None:
    assert _parse_month_label(raw) == expected


def test_parse_month_label_rejects_garbage() -> None:
    assert _parse_month_label("totally not a month") is None


# --- Column mapping -------------------------------------------------------------
#
# The header row below is the real "Suivi" layout as of 2026-08-28, after three
# "jours ouvrés" columns were inserted after G. Every payroll column therefore sits
# three places to the right of where the connector used to look for it.

HEADER_ROW = [
    "",
    "",
    "Pris\n(heures)",
    "Pris\n(jours ouvrables)",
    "Acquis",
    "Solde\n(jours ouvrables)",
    "Solde\n(semaines)",
    "Pris\n(jours ouvrés)",
    "Acquis\n(jours ouvrés)",
    "Solde\n(jours ouvrés)",
    "Heures\ncompl.",
    "Heures d\u2019absence",
    "Salaire brut",
    "Salaire net",
    "Transport",
    "Virement",
    "Virement réel",
    "Heures effectives",
    "Taux horaire brut du mois",
    "Salaire brut Contrat",
    "IR prélevé\nà la source",
]

AUGUST_ROW = [
    "août 2026",
    "",
    "91,4",
    "14,0",
    "3,1",
    "1,7",
    "0,3",
    "13,0",
    "2,6",
    "0,1",
    "0,0",
    "0,0",
    "  2\u202f560,16  € ",
    "  2\u202f000,00  € ",
    "  49,75  \u20ac ",
    "  2\u202f000,00  € ",
    "  2\u202f030,00  € ",
    "148",
    "17,36",
    "  2\u202f560,16  € ",
    "  49,75  \u20ac ",
]


def test_column_map_resolves_by_header_not_by_index() -> None:
    cols = ColumnMap.from_header(HEADER_ROW)
    # Line breaks inside a header must not matter.
    assert cols.index("Heures compl.") == 10
    assert cols.index("Salaire net") == 13
    assert cols.index("Heures effectives") == 17
    # "Pris (jours ouvrables)" and "Pris (jours ouvrés)" are distinct columns.
    assert cols.index("Pris (jours ouvrables)") == 3
    assert cols.index("Pris (jours ouvrés)") == 7


def test_column_map_reports_a_missing_header() -> None:
    cols = ColumnMap.from_header(HEADER_ROW)
    with pytest.raises(KeyError, match="No column headed"):
        cols.index("Colonne inexistante")
    assert cols.optional_index("N° volet") is None


def test_reference_block_accepts_both_orientations() -> None:
    values = [
        HEADER_ROW,
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "151,67",
            "Heures par mois",
        ],
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Rapport net/brut",
            "0,7812",
        ],
    ]
    assert _reference(values, "Heures par mois") == Decimal("151.67")
    assert _reference(values, "Rapport net/brut") == Decimal("0.7812")
    assert _reference(values, "Absent") is None


def test_hours_source_mensualisation_ignores_the_real_hours_of_the_month() -> None:
    """August has 148 real hours but every pay slip so far declares 152."""
    cols = ColumnMap.from_header(HEADER_ROW)
    month = _row_to_month_input(
        AUGUST_ROW,
        cols,
        period_start=date(2026, 8, 1),
        hours_source=HoursSource.MENSUALISATION,
        monthly_hours=Decimal("151.67"),
    )
    assert month.effective_hours == Decimal("151.67")  # → nbHeures 152 once rounded
    assert month.net_salary == Decimal("2000.00")
    assert month.transport_reimbursement == Decimal("49.75")
    assert month.paid_leave_days_taken == Decimal("14.0")


def test_hours_source_sheet_uses_the_effective_hours_column() -> None:
    cols = ColumnMap.from_header(HEADER_ROW)
    month = _row_to_month_input(
        AUGUST_ROW,
        cols,
        period_start=date(2026, 8, 1),
        hours_source=HoursSource.SHEET,
        monthly_hours=Decimal("151.67"),
    )
    assert month.effective_hours == Decimal("148")


def test_mensualisation_requires_the_reference_constant() -> None:
    cols = ColumnMap.from_header(HEADER_ROW)
    with pytest.raises(ValueError, match="Heures par mois"):
        _row_to_month_input(
            AUGUST_ROW,
            cols,
            period_start=date(2026, 8, 1),
            hours_source=HoursSource.MENSUALISATION,
            monthly_hours=None,
        )


@pytest.mark.parametrize("index,letter", [(0, "A"), (17, "R"), (25, "Z"), (26, "AA"), (28, "AC")])
def test_column_letter(index: int, letter: str) -> None:
    assert _column_letter(index) == letter
