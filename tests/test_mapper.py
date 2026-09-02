"""Tests for the canonical → wire mapper (TDPAJE 1.5.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from comptine.config import Enfant
from comptine.mapper import (
    DeclarationWindowError,
    check_declaration_window,
    children_to_wire,
    to_declaration_ged,
    to_estimation_ged,
)
from comptine.models import (
    CostCalculationMode,
    Employee,
    MonthInput,
)

CHILDREN = [{"nomEnfant": "DUPONT", "prenomEnfant": "LEA", "dtNaissanceEnfant": "2020-01-01"}]
"""A GED declaration is always about at least one child (see the mapper guard)."""


@pytest.fixture
def reference_december_2025() -> tuple[MonthInput, Employee]:
    """Real numbers from a December 2025 pay slip."""
    month = MonthInput(
        period_start=date(2025, 12, 1),
        period_end=date(2025, 12, 31),
        payment_date=date(2025, 12, 31),
        effective_hours=Decimal("80"),
        normal_hours=Decimal("80"),
        paid_leave_days_taken=Decimal("3.0"),
        gross_salary=Decimal("1046.06"),
        net_salary=Decimal("817.18"),
        transport_reimbursement=Decimal("50.75"),
        cost_calculation_mode=CostCalculationMode.REAL,
    )
    employee = Employee(
        pajemploi_number="00000000000000",
        last_name="DUPONT",
        first_name="CLAIRE",
    )
    return month, employee


def test_declaration_ged_required_fields_present(
    reference_december_2025: tuple[MonthInput, Employee],
) -> None:
    month, employee = reference_december_2025
    body = to_declaration_ged(month, employee=employee, children=CHILDREN)
    assert "inputSp" in body
    assert body["cdModeCalcul"], "cdModeCalcul is the only field required at the root"
    for key in (
        "dtDebutPeriode",
        "dtFinPeriode",
        "dtPaiementSalaire",
        "nbHeures",
        "mntSalaireNetMensuel",
    ):
        assert key in body["inputDeclCommun"], f"Missing required field {key}"


def test_declaration_ged_nests_period_and_pay_under_input_decl_commun(
    reference_december_2025: tuple[MonthInput, Employee],
) -> None:
    """InputDeclarationGed is nested, unlike the flat InputEstimationGed.

    Sending the period/hours/pay fields at the root leaves ``inputDeclCommun``
    null server-side, and /ged/predeclarer answers 500 ERREUR_TECHNIQUE.
    """
    month, employee = reference_december_2025
    body = to_declaration_ged(month, employee=employee, children=CHILDREN)

    assert body["inputDeclCommun"] == {
        "dtDebutPeriode": "2025-12-01",
        "dtFinPeriode": "2025-12-31",
        "dtPaiementSalaire": "2025-12-31",
        "nbHeures": 80,
        "mntSalaireNetMensuel": 817.18,
        "nbJoursCongesPayes": 3.0,
    }
    # Those fields must no longer sit at the root.
    for moved in (
        "dtDebutPeriode",
        "dtFinPeriode",
        "dtPaiementSalaire",
        "nbHeures",
        "mntSalaireNetMensuel",
        "nbJoursCongesPayes",
        "mntSalaireHoraireNet",
        "nbHeuresSpecifiques",
        "mntAcompteSalarie",
        "mntIndemnitesKilometriques",
    ):
        assert moved not in body, f"{moved} belongs under inputDeclCommun, not at the root"

    # ...while these stay at the root, per InputDeclarationGed.
    assert set(body) <= {
        "inputSp",
        "inputDeclCommun",
        "inputDeclEnfant",
        "cdModeCalcul",
        "mntFraisTransport",
        "nbHrSupMajA25",
        "nbHrSupMajA50",
        "finDeContrat",
    }


def test_declaration_ged_requires_at_least_one_child(
    reference_december_2025: tuple[MonthInput, Employee],
) -> None:
    """A GED declaration without inputDeclEnfant makes the server answer 500.

    Garde d'enfants à domicile always has a child, and the CMG is computed per
    child; refuse client-side rather than trip an ERREUR_TECHNIQUE.
    """
    month, employee = reference_december_2025
    with pytest.raises(ValueError, match="child"):
        to_declaration_ged(month, employee=employee, children=[])


def test_declaration_ged_serialises_correctly(
    reference_december_2025: tuple[MonthInput, Employee],
) -> None:
    month, employee = reference_december_2025
    body = to_declaration_ged(month, employee=employee, children=CHILDREN)
    assert body["inputSp"] == {"numeroPajeSalarie": "00000000000000"}
    assert body["cdModeCalcul"] == "R"
    commun = body["inputDeclCommun"]
    assert commun["dtDebutPeriode"] == "2025-12-01"
    assert commun["dtFinPeriode"] == "2025-12-31"
    assert commun["dtPaiementSalaire"] == "2025-12-31"
    assert commun["nbHeures"] == 80
    assert commun["mntSalaireNetMensuel"] == 817.18
    assert commun["nbJoursCongesPayes"] == 3.0


def test_declaration_ged_skips_zero_optional_fields(
    reference_december_2025: tuple[MonthInput, Employee],
) -> None:
    month, employee = reference_december_2025
    commun = to_declaration_ged(month, employee=employee, children=CHILDREN)["inputDeclCommun"]
    # Acompte was zero — it must not appear at all (the API rejects unknown shapes).
    assert "mntAcompteSalarie" not in commun
    assert "mntIndemnitesKilometriques" not in commun
    assert "nbHeuresSpecifiques" not in commun


def test_declaration_ged_includes_optionals_when_set() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("152"),
        normal_hours=Decimal("152"),
        net_salary=Decimal("2000.00"),
        advance_payment=Decimal("300"),
        kilometric_indemnities=Decimal("45.50"),
        specific_hours=Decimal("4"),
    )
    employee = Employee(pajemploi_number="00000000000000", last_name="X", first_name="Y")
    commun = to_declaration_ged(month, employee=employee, children=CHILDREN)["inputDeclCommun"]
    assert commun["mntAcompteSalarie"] == 300.00
    assert commun["mntIndemnitesKilometriques"] == 45.50
    assert commun["nbHeuresSpecifiques"] == 4


def test_declaration_ged_includes_transport_reimbursement(
    reference_december_2025: tuple[MonthInput, Employee],
) -> None:
    # The real December 2025 bulletin lists "Frais de transport: 50,75 €" among the
    # declared inputs and folds it into the net à payer.
    month, employee = reference_december_2025
    body = to_declaration_ged(month, employee=employee, children=CHILDREN)
    assert body["mntFraisTransport"] == 50.75


def test_declaration_ged_skips_zero_transport_reimbursement() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("152"),
        normal_hours=Decimal("152"),
        net_salary=Decimal("2000"),
    )
    employee = Employee(pajemploi_number="00000000000000", last_name="X", first_name="Y")
    body = to_declaration_ged(month, employee=employee, children=CHILDREN)
    assert "mntFraisTransport" not in body


def test_children_to_wire_uppercases_and_strips_accents() -> None:
    """InputEnfant demands unaccented uppercase names (pattern [A-Z'\\-\\s]*)."""
    enfants = [
        Enfant(nom="Dupont", prenom="Grégoire", date_naissance=date(2013, 5, 2)),
        Enfant(nom="Dupont", prenom="Lea", date_naissance=date(2020, 1, 1)),
    ]
    assert children_to_wire(enfants) == [
        {"nomEnfant": "DUPONT", "prenomEnfant": "GREGOIRE", "dtNaissanceEnfant": "2013-05-02"},
        {"nomEnfant": "DUPONT", "prenomEnfant": "LEA", "dtNaissanceEnfant": "2020-01-01"},
    ]


def test_declaration_ged_with_children() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("152"),
        normal_hours=Decimal("152"),
        net_salary=Decimal("2000"),
    )
    employee = Employee(pajemploi_number="00000000000000", last_name="X", first_name="Y")
    children = [{"nomEnfant": "DUPONT", "prenomEnfant": "LEA", "dtNaissanceEnfant": "2020-01-01"}]
    body = to_declaration_ged(month, employee=employee, children=children)
    assert body["inputDeclEnfant"] == children


def test_declaration_ged_raises_without_id() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("1"),
        normal_hours=Decimal("1"),
        net_salary=Decimal("1"),
    )
    employee = Employee(last_name="X", first_name="Y")
    with pytest.raises(ValueError, match="pajemploi_number"):
        to_declaration_ged(month, employee=employee, children=CHILDREN)


def test_declaration_ged_raises_without_net_salary() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("1"),
        normal_hours=Decimal("1"),
    )
    employee = Employee(pajemploi_number="00000000000000", last_name="X", first_name="Y")
    with pytest.raises(ValueError, match="net_salary"):
        to_declaration_ged(month, employee=employee, children=CHILDREN)


def test_declaration_ged_raises_on_zero_hours_with_positive_net() -> None:
    # A half-filled Sheet row (net typed in, hours column still empty) must not
    # silently build a zero-hour declaration.
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("0"),
        normal_hours=Decimal("0"),
        net_salary=Decimal("2000"),
    )
    employee = Employee(pajemploi_number="00000000000000", last_name="X", first_name="Y")
    with pytest.raises(ValueError, match="effective_hours"):
        to_declaration_ged(month, employee=employee, children=CHILDREN)


def test_estimation_ged_shape() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("152"),
        normal_hours=Decimal("152"),
        net_salary=Decimal("2000"),
    )
    body = to_estimation_ged(
        month,
        employer_postal_code="35700",
        employee_postal_code="35000",
        youngest_child_age_band="2",
    )
    assert body["mntSalaireNetMensuel"] == 2000.00
    assert body["nbHeures"] == 152
    assert body["cdModeCalcul"] == "R"
    assert body["complementActiviteOk"] is False
    assert body["indcEnfantPlusJeuneAgarder"] == "2"
    assert body["cdPostalEmployeur"] == "35700"
    assert body["cdPostalSalarie"] == "35000"


def test_estimation_ged_rejects_invalid_age_band() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("152"),
        normal_hours=Decimal("152"),
        net_salary=Decimal("2000"),
    )
    with pytest.raises(ValueError, match="youngest_child_age_band"):
        to_estimation_ged(month, youngest_child_age_band="0")


def test_cp_days_rounds_to_half() -> None:
    month = MonthInput(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        payment_date=date(2026, 6, 30),
        effective_hours=Decimal("152"),
        normal_hours=Decimal("152"),
        net_salary=Decimal("2000"),
        paid_leave_days_taken=Decimal("2.3"),  # snapped to 2.5
    )
    employee = Employee(pajemploi_number="00000000000000", last_name="X", first_name="Y")
    commun = to_declaration_ged(month, employee=employee, children=CHILDREN)["inputDeclCommun"]
    assert commun["nbJoursCongesPayes"] == 2.5


# --- Declaration window guard (TDPAJE FAQ rule) ----------------------------------


def test_window_open_on_the_25th() -> None:
    # Employment month June 2026; window opens 2026-06-25.
    check_declaration_window(date(2026, 6, 1), today=date(2026, 6, 25))  # exactly open
    check_declaration_window(date(2026, 6, 1), today=date(2026, 7, 6))  # late is fine


def test_window_closed_before_the_25th() -> None:
    with pytest.raises(DeclarationWindowError, match="ER_API_DECLA_0000"):
        check_declaration_window(date(2026, 6, 1), today=date(2026, 6, 24))


def test_window_uses_employment_month_not_today_month() -> None:
    # Declaring May employment on June 10: May window (opened 2026-05-25) is long open.
    check_declaration_window(date(2026, 5, 1), today=date(2026, 6, 10))
    # But declaring June employment on June 10 is too early.
    with pytest.raises(DeclarationWindowError):
        check_declaration_window(date(2026, 6, 1), today=date(2026, 6, 10))
