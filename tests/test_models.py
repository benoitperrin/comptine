"""Tests for the canonical domain models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from comptine.models import (
    Address,
    CostCalculationMode,
    Employee,
    Employer,
    MonthInput,
)


class TestMonthInput:
    def test_accepts_decimal(self) -> None:
        m = MonthInput(
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            payment_date=date(2026, 6, 30),
            effective_hours=Decimal("151.67"),
            normal_hours=Decimal("151.67"),
            gross_salary=Decimal("2560.16"),
        )
        assert m.gross_salary == Decimal("2560.16")

    def test_coerces_french_money_string(self) -> None:
        m = MonthInput(
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            payment_date=date(2026, 6, 30),
            effective_hours="151,67",
            normal_hours="151,67",
            gross_salary="  2\u202f560,16  € ",
            transport_reimbursement="60,00 €",
        )
        assert m.gross_salary == Decimal("2560.16")
        assert m.transport_reimbursement == Decimal("60.00")
        assert m.effective_hours == Decimal("151.67")

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — pydantic raises ValidationError
            MonthInput(
                period_start=date(2026, 6, 1),
                period_end=date(2026, 6, 30),
                payment_date=date(2026, 6, 30),
                effective_hours=Decimal("0"),
                normal_hours=Decimal("0"),
                gross_salary=Decimal("0"),
                bogus_field=42,  # type: ignore[call-arg]
            )

    def test_defaults_calculation_mode_to_real(self) -> None:
        m = MonthInput(
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            payment_date=date(2026, 6, 30),
            effective_hours=Decimal("0"),
            normal_hours=Decimal("0"),
            gross_salary=Decimal("0"),
        )
        assert m.cost_calculation_mode == CostCalculationMode.REAL


def test_employer_keeps_pajemploi_number_optional() -> None:
    e = Employer(last_name="DUPONT", first_name="BENOÎT")
    assert e.pajemploi_number is None


def test_employee_with_full_payload() -> None:
    e = Employee(
        pajemploi_number="00000000000000",
        last_name="DUPONT",
        first_name="CLAIRE",
        address=Address(
            line1="17 RUE DU VAL HERVELIN",
            postal_code="22690",
            city="PLEUDIHEN SUR RANCE",
        ),
        profession="Garde d'enfants à domicile",
        naf_code="9700Z",
    )
    assert e.address is not None
    assert e.address.country == "FR"
