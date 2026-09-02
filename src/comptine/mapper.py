"""Map canonical domain models to API wire payloads (TDPAJE 1.5.1).

Reference: the OpenAPI spec at ``docs/api/openapi.json``.

Two body shapes matter here:

* :func:`to_declaration_ged` builds an ``InputDeclarationGed`` for
  ``predeclarer`` / ``declarer`` on a Garde d'enfants à domicile activity.
* :func:`to_estimation_ged` builds an ``InputEstimationGed`` for ``estimer``
  (no side effects).

Both deliberately omit optional fields when the corresponding canonical value
is zero or missing — the Urssaf API is strict about unknown fields and about
``mntSalaireNetMensuel`` being strictly positive.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from comptine.config import Enfant
from comptine.models import (
    CostCalculationMode,
    Employee,
    MonthInput,
)

# OpenAPI says salary fields are doubles with multipleOf 0.01.
_MONEY_QUANT = Decimal("0.01")

# Day of the employment month on which the declarative window opens (TDPAJE FAQ).
DECLARATION_WINDOW_OPENS_DAY = 25


class DeclarationWindowError(ValueError):
    """Raised when a declaration is attempted before its window opens.

    The TDPAJE declarative period for an employment month opens on the 25th of
    that month; declaring earlier is rejected server-side with ER_API_DECLA_0000.
    We surface this locally so the caller fails fast without a wasted API call.
    """


def check_declaration_window(period_start: date, *, today: date) -> None:
    """Raise :class:`DeclarationWindowError` if the window has not opened yet.

    The window for employment month M opens on M-25. There is no hard deadline,
    so late declarations are allowed. We only guard the early case.
    """
    window_open = period_start.replace(day=DECLARATION_WINDOW_OPENS_DAY)
    if today < window_open:
        raise DeclarationWindowError(
            f"Declarative window for {period_start:%Y-%m} opens on "
            f"{window_open.isoformat()} (the 25th of the employment month); "
            f"today is {today.isoformat()}. Declaring earlier would be rejected "
            "with ER_API_DECLA_0000."
        )


def _money(value: Decimal) -> float:
    """Quantise to 2 decimals and return as a float (the API expects numbers)."""
    return float(value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP))


def _hours_int(value: Decimal) -> int:
    """Round hours to the nearest integer (the API field ``nbHeures`` is int32)."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _cp_days(value: Decimal) -> float:
    """Quantise paid-leave days to a half-day step (multipleOf 0.5 per spec)."""
    return float((value * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2)


def _mode_calcul(mode: CostCalculationMode) -> str:
    """Translate the canonical calculation mode to the single-letter wire code."""
    return {CostCalculationMode.REAL: "R", CostCalculationMode.FLAT: "F"}[mode]


def _input_sp(employee: Employee) -> dict[str, Any]:
    """Build the ``inputSp`` block identifying the worker."""
    sp: dict[str, Any] = {}
    if employee.pajemploi_number:
        sp["numeroPajeSalarie"] = employee.pajemploi_number
    if employee.nir:
        sp["nirSalarie"] = employee.nir
    return sp


def _upper_ascii(value: str) -> str:
    """ "Grégoire" → "GREGOIRE": InputEnfant only accepts [A-Z'\\-\\s]."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def children_to_wire(enfants: list[Enfant]) -> list[dict[str, Any]]:
    """Build the ``inputDeclEnfant`` array from the configured children."""
    return [
        {
            "nomEnfant": _upper_ascii(e.nom),
            "prenomEnfant": _upper_ascii(e.prenom),
            "dtNaissanceEnfant": e.date_naissance.isoformat(),
        }
        for e in enfants
    ]


def to_declaration_ged(
    month: MonthInput,
    *,
    employee: Employee,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an ``InputDeclarationGed`` body.

    The ``net`` salary expected by the API is the **monthly net** before transport
    reimbursement and indemnités kilométriques (see :class:`MonthInput`); those are
    declared in their own fields (``mntFraisTransport``, ``mntIndemnitesKilometriques``)
    and shown on the bulletin as "Indemnités" on top of the net déclaré.
    """
    if employee.pajemploi_number is None and not employee.nir:
        raise ValueError(
            "Employee is missing both pajemploi_number and nir; at least one is required."
        )
    if month.net_salary is None:
        raise ValueError(
            "MonthInput.net_salary is required for a declaration (mntSalaireNetMensuel)."
        )
    if month.net_salary > 0 and _hours_int(month.effective_hours) == 0:
        raise ValueError(
            "MonthInput.effective_hours is 0 while net_salary is positive — refusing to "
            "build a zero-hour declaration (hours column not filled yet?)."
        )
    if not children:
        raise ValueError(
            "A GED declaration needs at least one child (inputDeclEnfant): the CMG is "
            "computed per child, and the API answers 500 ERREUR_TECHNIQUE without it."
        )

    # Unlike the flat InputEstimationGed, InputDeclarationGed nests the period, hours
    # and pay under inputDeclCommun; sending them at the root leaves it null server-side
    # and /ged/predeclarer answers 500 ERREUR_TECHNIQUE.
    commun: dict[str, Any] = {
        "dtDebutPeriode": month.period_start.isoformat(),
        "dtFinPeriode": month.period_end.isoformat(),
        "dtPaiementSalaire": month.payment_date.isoformat(),
        "nbHeures": _hours_int(month.effective_hours),
        "mntSalaireNetMensuel": _money(month.net_salary),
    }

    if month.paid_leave_days_taken and month.paid_leave_days_taken > 0:
        commun["nbJoursCongesPayes"] = _cp_days(month.paid_leave_days_taken)
    if month.advance_payment and month.advance_payment > 0:
        commun["mntAcompteSalarie"] = _money(month.advance_payment)
    if month.kilometric_indemnities and month.kilometric_indemnities > 0:
        commun["mntIndemnitesKilometriques"] = _money(month.kilometric_indemnities)
    if month.net_hourly_salary is not None:
        commun["mntSalaireHoraireNet"] = _money(month.net_hourly_salary)
    if month.specific_hours and month.specific_hours > 0:
        commun["nbHeuresSpecifiques"] = _hours_int(month.specific_hours)

    body: dict[str, Any] = {
        "inputSp": _input_sp(employee),
        "inputDeclCommun": commun,
        "cdModeCalcul": _mode_calcul(month.cost_calculation_mode),
    }

    if month.transport_reimbursement and month.transport_reimbursement > 0:
        body["mntFraisTransport"] = _money(month.transport_reimbursement)

    if children:
        body["inputDeclEnfant"] = children

    return body


def to_estimation_ged(
    month: MonthInput,
    *,
    employer_postal_code: str | None = None,
    employee_postal_code: str | None = None,
    has_complement_activity: bool = False,
    youngest_child_age_band: str = "2",
    overtime_25_hours: int = 0,
    overtime_50_hours: int = 0,
) -> dict[str, Any]:
    """Build an ``InputEstimationGed`` body for the ``/ged/estimer`` endpoint.

    Args:
        youngest_child_age_band: One of ``"1"`` (0-3 yrs, no complement),
            ``"2"`` (3-6 yrs), ``"3"`` (>6 yrs). Drives the CMG band.
        overtime_25_hours: Optional ``nbHrSupMajA25``.
        overtime_50_hours: Optional ``nbHrSupMajA50``.
    """
    if month.net_salary is None:
        raise ValueError("MonthInput.net_salary is required for an estimation.")
    if youngest_child_age_band not in {"1", "2", "3"}:
        raise ValueError("youngest_child_age_band must be '1', '2' or '3'.")

    body: dict[str, Any] = {
        "mntSalaireNetMensuel": _money(month.net_salary),
        "cdModeCalcul": _mode_calcul(month.cost_calculation_mode),
        "nbHeures": _hours_int(month.effective_hours),
        "complementActiviteOk": has_complement_activity,
        "indcEnfantPlusJeuneAgarder": youngest_child_age_band,
    }
    if overtime_25_hours:
        body["nbHrSupMajA25"] = overtime_25_hours
    if overtime_50_hours:
        body["nbHrSupMajA50"] = overtime_50_hours
    if employer_postal_code:
        body["cdPostalEmployeur"] = employer_postal_code
    if employee_postal_code:
        body["cdPostalSalarie"] = employee_postal_code
    return body
