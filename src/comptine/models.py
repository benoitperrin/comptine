"""Domain models inferred from the Pajemploi pay slip ("bulletin de salaire") and from
the public description of the TDPAJE API.

These models are deliberately conservative about field names — they capture the
*meaning* of each field as it appears on the pay slip, not the (unknown) JSON
field names of the API. The mapper layer translates between this canonical shape
and whatever the Urssaf API actually accepts.

When the OpenAPI specification becomes available, the API layer will likely add a
thin transport-shaped model for each endpoint; this file stays as the stable
domain language used by the CLI and the Sheet integration.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CostCalculationMode(StrEnum):
    """How the contributions are computed."""

    REAL = "real"  # "Salaire réel" — based on actual gross
    FLAT = "flat"  # "Forfaitaire" — based on the legal minimum wage; deprecated since 2019


class ContractType(StrEnum):
    """The two regimes accessible via TDPAJE."""

    GED = "ged"  # Garde d'enfants à domicile
    AM = "am"  # Assistant maternel agréé


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line1: str
    line2: str | None = None
    postal_code: str
    city: str
    country: str = "FR"


class Employer(BaseModel):
    """Particulier-employeur (private employer)."""

    model_config = ConfigDict(extra="forbid")
    pajemploi_number: str | None = Field(
        default=None,
        description='Pajemploi-internal employer reference, e.g. "Y1234567890123".',
    )
    last_name: str
    first_name: str
    address: Address | None = None
    nir: str | None = Field(
        default=None, description="French social security number (NIR), 15 digits."
    )


class Employee(BaseModel):
    """Salarié (domestic worker)."""

    model_config = ConfigDict(extra="forbid")
    pajemploi_number: str | None = Field(
        default=None,
        description='Pajemploi-internal employee reference, e.g. "00000000000000".',
    )
    last_name: str
    first_name: str
    address: Address | None = None
    nir: str | None = None
    profession: str | None = Field(
        default=None, description='Pajemploi profession label, e.g. "Garde d\'enfants à domicile".'
    )
    naf_code: str | None = Field(default=None, description='NAF/APE code, e.g. "9700Z".')


class Mandate(BaseModel):
    """Mandate granting a third-party declarant the right to declare for an employer."""

    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    tiers_siret: str
    employer_pajemploi_number: str
    status: str | None = None
    granted_at: date | None = None
    revoked_at: date | None = None


class MonthInput(BaseModel):
    """Canonical monthly inputs for a declaration, as read from the Sheet.

    Naming follows the Pajemploi pay slip layout (see a real December
    2025 slip used as reference): every quantity on the slip's "Éléments pris en
    compte" section is represented here.
    """

    model_config = ConfigDict(extra="forbid")
    period_start: date
    period_end: date
    payment_date: date

    effective_hours: Decimal = Field(description='"Nombre d\'heures effectives"')
    normal_hours: Decimal = Field(description='"Nombre d\'heures normales"')
    overtime_25_hours: Decimal = Field(
        default=Decimal("0"), description="Heures supplémentaires majorées à 25 %."
    )
    overtime_50_hours: Decimal = Field(
        default=Decimal("0"), description="Heures supplémentaires majorées à 50 %."
    )

    paid_leave_days_taken: Decimal = Field(
        default=Decimal("0"), description='"Nombre de jours de congés payés" pris ce mois.'
    )

    specific_hours: Decimal = Field(
        default=Decimal("0"), description='"Nombre d\'heures spécifiques" (e.g. night work).'
    )

    gross_salary: Decimal | None = Field(
        default=None, description='"Salaire brut" (information only).'
    )
    net_salary: Decimal | None = Field(
        default=None,
        description='"Salaire net mensuel" — the API field ``mntSalaireNetMensuel``.',
    )
    net_hourly_salary: Decimal | None = Field(
        default=None, description='"Salaire horaire net" — optional (``mntSalaireHoraireNet``).'
    )
    advance_payment: Decimal = Field(
        default=Decimal("0"), description='"Acompte" déjà versé ce mois.'
    )
    kilometric_indemnities: Decimal = Field(
        default=Decimal("0"), description='"Indemnités kilométriques".'
    )
    transport_reimbursement: Decimal = Field(
        default=Decimal("0"),
        description='"Frais de transport" — the API field ``mntFraisTransport``.',
    )

    cost_calculation_mode: CostCalculationMode = CostCalculationMode.REAL

    @field_validator(
        "effective_hours",
        "normal_hours",
        "overtime_25_hours",
        "overtime_50_hours",
        "specific_hours",
        "paid_leave_days_taken",
        "gross_salary",
        "net_salary",
        "net_hourly_salary",
        "advance_payment",
        "kilometric_indemnities",
        "transport_reimbursement",
        mode="before",
    )
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        if isinstance(v, str):
            v = v.replace("\u202f", "").replace("\xa0", "").replace(" ", "")
            v = v.replace("€", "").strip()
            if v == "":
                return None
            v = v.replace(",", ".")
        return Decimal(str(v))


class ContributionLine(BaseModel):
    """One line of social-contribution computation, as printed on the pay slip."""

    model_config = ConfigDict(extra="forbid")
    label: str
    base: Decimal
    employee_rate_pct: Decimal | None = None
    employee_amount: Decimal | None = None
    employer_rate_pct: Decimal | None = None
    employer_amount: Decimal | None = None


class DeclarationResult(BaseModel):
    """What we get back after a successful ``declarer`` call.

    The numeric details mirror the pay slip; the connector exposes them so the
    caller can verify before persisting or paying.
    """

    model_config = ConfigDict(extra="forbid")
    declaration_id: str = Field(description='"Volet social n°", e.g. "2026001X00000".')
    submitted_at: date
    period_start: date
    period_end: date
    payment_date: date

    employer_pajemploi_number: str
    employee_pajemploi_number: str

    gross_salary: Decimal
    contributions: list[ContributionLine] = Field(default_factory=list)
    total_employee_contributions: Decimal | None = None
    total_employer_contributions: Decimal | None = None

    net_declared: Decimal | None = None
    net_with_indemnities: Decimal | None = None
    net_taxable: Decimal | None = None
    withholding_tax_rate_pct: Decimal | None = None
    withholding_tax_amount: Decimal | None = None
    net_paid: Decimal | None = None

    bulletin_pdf_url: str | None = None
