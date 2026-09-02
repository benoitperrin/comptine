"""Google Sheets integration for the household's monthly tracking workbook.

The Sheet is the source of truth for monthly payroll inputs. Its "Suivi" tab is
laid out with one row per month, plus a reference block a few columns to the
right that holds the contract constants (hours/week, hours/month, monthly gross,
transport rate, net/gross ratio, PAS rate...).

**Columns are resolved by header label, never by a fixed index.** On 2026-08-28
three columns ("jours ouvrés") were inserted after G; every hard-coded index then
pointed one payroll field too far left — silently, since every neighbouring column
also holds a number. Reading the header row costs nothing and makes the next
insertion a non-event.

Reading:
    >>> from comptine.sheet import MonthlySheetReader
    >>> reader = MonthlySheetReader.from_config(cfg, salarie_handle="nounou")
    >>> month = reader.read_month("2026-06")

Writing back: after a successful declaration, ``write_back`` stamps the
declaration id and date into the row so the next run is idempotent. It writes
**only** under headers it can find (see :data:`HEADER_DECL_ID` and friends) and
raises :class:`WriteBackUnavailable` otherwise — the previous version wrote to a
hard-coded S:U, which today lands on three live formula columns.
"""

from __future__ import annotations

import json
import logging
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from comptine.config import Config, Salarie
from comptine.models import CostCalculationMode, MonthInput

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
"""Scope used for both reading and writing the workbook.

We use the broader ``drive`` scope instead of ``spreadsheets`` so the connector
can reuse an existing user OAuth token that was granted Drive access (e.g. the
token issued by Benoît's personal MCP client). Both scopes give full Sheets API
access.
"""

# French month abbreviations as they appear in the "Suivi" sheet ("avr. 2026").
_MONTH_FR = {
    "janv.": 1,
    "févr.": 2,
    "mars": 3,
    "avr.": 4,
    "mai": 5,
    "juin": 6,
    "juil.": 7,
    "août": 8,
    "sept.": 9,
    "oct.": 10,
    "nov.": 11,
    "déc.": 12,
}

COL_MONTH = 0
"""The month column (A) is the only one addressed by index: its header cell is empty."""

# Header labels, exactly as they read in row 1 of the "Suivi" tab (whitespace is
# normalised before matching, so a line break inside a header is irrelevant).
HEADER_CP_PRIS_HOURS = "Pris (heures)"
HEADER_CP_PRIS_DAYS = "Pris (jours ouvrables)"
HEADER_HEURES_COMPL = "Heures compl."
HEADER_HEURES_ABSENCE = "Heures d\u2019absence"
HEADER_SALAIRE_BRUT = "Salaire brut"
HEADER_SALAIRE_NET = "Salaire net"
HEADER_TRANSPORT = "Transport"
HEADER_HEURES_EFFECTIVES = "Heures effectives"

# Optional write-back columns. Absent from the sheet today: `write_back` refuses
# rather than guessing a free column (it used to overwrite S:U, which now carries
# "Taux horaire brut du mois", "Salaire brut Contrat" and "IR prélevé à la source").
HEADER_DECL_ID = "N° volet"
HEADER_DECL_DATE = "Déclaré le"
HEADER_DECL_NET_PAID = "Net payé"

# Labels of the reference block (value sits in the cell immediately left or right).
REF_HOURS_PER_MONTH = "Heures par mois"
REF_HOURS_PER_WEEK = "Heures par semaine"


class HoursSource(StrEnum):
    """Where ``nbHeures`` comes from.

    ``MENSUALISATION`` — the contract's monthly hours (reference block, 151,67 →
    152), minus unpaid absences, plus complementary hours. This is what every
    Pajemploi pay slip issued so far carries, and what a mensualised CDI owes:
    a fixed monthly gross implies fixed monthly hours, and paid leave does not
    reduce them (the slip has a separate "jours de congés payés" box).

    ``SHEET`` — the "Heures effectives" column, which since the "(calculs)
    Semaines" tab was repaired counts the *real* hours of each month (147 to 161
    depending on how the weekdays fall). Declaring those would break continuity
    with the slips already filed.
    """

    MENSUALISATION = "mensualisation"
    SHEET = "sheet"


class WriteBackUnavailable(RuntimeError):
    """Raised when the sheet has no column to write the declaration reference to."""


def _parse_money(s: str) -> Decimal:
    """Parse "  2 560,16  € " → Decimal('2560.16').

    Accepts ``\\u202f`` (narrow no-break space) and ``\\xa0`` (no-break space)
    as thousand separators, and ``,`` as the decimal mark.
    """
    if not s:
        return Decimal("0")
    s = s.replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    s = s.replace("€", "").strip()
    if not s or s in {"-", "—"}:
        return Decimal("0")
    s = s.replace(",", ".")
    return Decimal(s)


def _parse_hours(s: str) -> Decimal:
    if not s:
        return Decimal("0")
    return Decimal(s.replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace(",", "."))


def _parse_month_label(label: str) -> date | None:
    """Parse "avr. 2026" → date(2026, 4, 1)."""
    m = re.match(r"\s*([^\s.]+\.?)\s+(\d{4})\s*", label)
    if not m:
        return None
    raw_month, year = m.group(1), int(m.group(2))
    if raw_month not in _MONTH_FR:
        return None
    return date(year, _MONTH_FR[raw_month], 1)


def _normalise(label: str) -> str:
    """Collapse whitespace and case so "Heures\\ncompl." matches "Heures compl.".

    Both apostrophes are folded to the curly one: the sheet uses U+2019, a
    hand-typed constant might not.
    """
    return re.sub(r"\s+", " ", label).strip().casefold().replace("'", "\u2019")


@dataclass(frozen=True)
class ColumnMap:
    """Header label → 0-based column index, for one "Suivi" header row."""

    by_label: dict[str, list[int]]

    @classmethod
    def from_header(cls, header_row: list[str]) -> ColumnMap:
        by_label: dict[str, list[int]] = {}
        for index, raw in enumerate(header_row):
            if not raw or not raw.strip():
                continue
            by_label.setdefault(_normalise(raw), []).append(index)
        return cls(by_label=by_label)

    def index(self, label: str) -> int:
        """Return the column holding ``label``; raise if missing or ambiguous."""
        hits = self.by_label.get(_normalise(label), [])
        if not hits:
            raise KeyError(
                f"No column headed {label!r} in the 'Suivi' tab. Known headers: "
                + ", ".join(sorted(self.by_label))
            )
        if len(hits) > 1:
            raise KeyError(f"Header {label!r} appears in {len(hits)} columns: {hits}")
        return hits[0]

    def optional_index(self, label: str) -> int | None:
        try:
            return self.index(label)
        except KeyError:
            return None

    @property
    def width(self) -> int:
        return 1 + max((i for hits in self.by_label.values() for i in hits), default=0)


def _column_letter(index: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA'."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


class MonthlySheetReader:
    """Reader for a household-employee Google Sheet.

    Despite its name, this class is generic: pass any :class:`Salarie` handle.
    """

    def __init__(self, salarie: Salarie, credentials_path: Path) -> None:
        if salarie.sheet_id is None:
            raise ValueError(f"Salarié has no sheet_id: {salarie}")
        self._salarie = salarie
        self._credentials_path = credentials_path
        self._service: Any | None = None

    @classmethod
    def from_config(cls, cfg: Config, salarie_handle: str) -> MonthlySheetReader:
        salarie = cfg.salaries[salarie_handle]
        creds_path = Path(cfg.google_credentials_path or "~/.config/pajemploi/google-token.json")
        return cls(salarie, creds_path.expanduser())

    @property
    def service(self) -> Any:
        if self._service is None:
            creds = self._load_credentials()
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def _load_credentials(self) -> Credentials:
        """Build credentials, tolerating tokens that omit the ``scopes`` field.

        Some MCP-issued tokens at ``~/.config/google/drive_token.json`` only
        carry ``token`` + ``refresh_token`` + client info. We attach the
        connector's scope list at load time so refresh works.
        """
        with self._credentials_path.open(encoding="utf-8") as f:
            data = json.load(f)
        return Credentials(  # type: ignore[no-untyped-call]
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            scopes=SCOPES,
        )

    def _values(self) -> list[list[str]]:
        # Read wide: the reference block and the PAS grid live to the right of the
        # payroll columns, and read_month needs both.
        range_name = f"{self._salarie.sheet_tab}!A1:BZ200"
        try:
            resp = (
                self.service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self._salarie.sheet_id,
                    range=range_name,
                    valueRenderOption="FORMATTED_VALUE",
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Sheet read failed: {e}") from e
        rows: list[list[str]] = resp.get("values", [])
        return rows

    def read_month(
        self,
        month: str | date,
        *,
        hours_source: HoursSource = HoursSource.MENSUALISATION,
    ) -> MonthInput:
        """Read the row for ``month`` (either ``"YYYY-MM"`` or a :class:`date`).

        Paid-leave days, gross and net salary and transport are taken straight
        from the row; the declared hours follow ``hours_source`` (see
        :class:`HoursSource`). The payment date defaults to the last day of the
        period (Pajemploi accepts both real and end-of-period dates; the Sheet
        doesn't track it).
        """
        target = _as_first_of_month(month)
        values = self._values()
        cols = ColumnMap.from_header(values[0] if values else [])
        monthly_hours = _reference(values, REF_HOURS_PER_MONTH)

        for raw_row in values[1:]:
            row = _pad(raw_row, cols.width)
            if _parse_month_label(row[COL_MONTH]) == target:
                return _row_to_month_input(
                    row,
                    cols,
                    period_start=target,
                    hours_source=hours_source,
                    monthly_hours=monthly_hours,
                )
        raise KeyError(f"No row found for month {month} in sheet {self._salarie.sheet_id}")

    def write_back(
        self,
        month: str | date,
        *,
        declaration_id: str,
        declared_at: date,
        net_paid: Decimal | None = None,
    ) -> None:
        """Write the declaration id, date and (optionally) net paid back to the row.

        Raises:
            WriteBackUnavailable: if the sheet carries no ``N° volet`` header.
                Callers must treat this as a warning, never as a failure — the
                declaration itself has already been filed at that point.
        """
        target = _as_first_of_month(month)
        values = self._values()
        cols = ColumnMap.from_header(values[0] if values else [])

        cells: list[tuple[int, str]] = []
        for label, value in (
            (HEADER_DECL_ID, declaration_id),
            (HEADER_DECL_DATE, declared_at.isoformat()),
            (HEADER_DECL_NET_PAID, f"{net_paid}" if net_paid is not None else ""),
        ):
            index = cols.optional_index(label)
            if index is not None:
                cells.append((index, value))
        if not cells:
            raise WriteBackUnavailable(
                "The 'Suivi' tab has no write-back columns. Add headers "
                f"{HEADER_DECL_ID!r}, {HEADER_DECL_DATE!r} and {HEADER_DECL_NET_PAID!r} "
                "to row 1 (in free columns), or pass --no-write-back."
            )

        for row_index, raw_row in enumerate(values[1:], start=2):  # 1-based, +1 header
            row = _pad(raw_row, cols.width)
            if _parse_month_label(row[COL_MONTH]) == target:
                self._write_cells(row_index, cells)
                return
        raise KeyError(f"No row to write to for month {month}")

    def _write_cells(self, row_index: int, cells: list[tuple[int, str]]) -> None:
        """Write one cell per resolved column — never a contiguous span."""
        data = [
            {
                "range": f"{self._salarie.sheet_tab}!{_column_letter(col)}{row_index}",
                "values": [[value]],
            }
            for col, value in cells
        ]
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self._salarie.sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()


def _as_first_of_month(month: str | date) -> date:
    if isinstance(month, str):
        year, m = (int(x) for x in month.split("-"))
        return date(year, m, 1)
    return month.replace(day=1)


def _pad(row: list[str], width: int) -> list[str]:
    return row + [""] * max(0, width - len(row))


def _reference(values: list[list[str]], label: str) -> Decimal | None:
    """Find a constant in the reference block.

    The block is not consistently oriented: "35 | Heures par semaine" puts the
    value on the left, "Taux PAS personnalisé | 2,40 %" on the right. We accept
    either, taking the first neighbour that parses as a number.
    """
    wanted = _normalise(label)
    for row in values:
        for index, cell in enumerate(row):
            if _normalise(cell or "") != wanted:
                continue
            for neighbour in (index - 1, index + 1):
                if 0 <= neighbour < len(row):
                    try:
                        value = _parse_hours(row[neighbour])
                    except (ArithmeticError, ValueError):  # a label, not a number
                        continue
                    if value > 0:
                        return value
    return None


def _row_to_month_input(
    row: list[str],
    cols: ColumnMap,
    *,
    period_start: date,
    hours_source: HoursSource,
    monthly_hours: Decimal | None,
) -> MonthInput:
    last_day = monthrange(period_start.year, period_start.month)[1]
    period_end = date(period_start.year, period_start.month, last_day)

    gross = _parse_money(row[cols.index(HEADER_SALAIRE_BRUT)])
    net = _parse_money(row[cols.index(HEADER_SALAIRE_NET)])
    transport = _parse_money(row[cols.index(HEADER_TRANSPORT)])
    cp_pris_days = _parse_hours(row[cols.index(HEADER_CP_PRIS_DAYS)])
    heures_compl = _parse_hours(row[cols.index(HEADER_HEURES_COMPL)])
    heures_absence = _parse_hours(row[cols.index(HEADER_HEURES_ABSENCE)])
    sheet_hours = _parse_hours(row[cols.index(HEADER_HEURES_EFFECTIVES)])

    if hours_source is HoursSource.MENSUALISATION:
        if monthly_hours is None:
            raise ValueError(
                f"hours_source=mensualisation needs the reference constant "
                f"{REF_HOURS_PER_MONTH!r} in the sheet; it was not found. "
                "Use hours_source=sheet to fall back on the 'Heures effectives' column."
            )
        # Paid leave does not reduce the declared hours: the slip carries them in
        # its own box ("y compris les heures d'absence pour congés payés").
        effective_hours = monthly_hours - heures_absence + heures_compl
    else:
        effective_hours = sheet_hours

    normal_hours = effective_hours - heures_compl

    # The Pajemploi API consumes net salary (``mntSalaireNetMensuel``). The Sheet
    # tracks both ("Salaire brut" and "Salaire net"). We pass both through so the
    # mapper has the canonical net and the brut is available for sanity checks.
    return MonthInput(
        period_start=period_start,
        period_end=period_end,
        payment_date=period_end,
        effective_hours=effective_hours,
        normal_hours=normal_hours,
        overtime_25_hours=heures_compl,
        overtime_50_hours=Decimal("0"),
        paid_leave_days_taken=cp_pris_days,
        gross_salary=gross if gross > 0 else None,
        net_salary=net if net > 0 else None,
        advance_payment=Decimal("0"),
        kilometric_indemnities=Decimal("0"),
        transport_reimbursement=transport,
        cost_calculation_mode=CostCalculationMode.REAL,
    )


__all__ = ["ColumnMap", "HoursSource", "MonthInput", "MonthlySheetReader", "WriteBackUnavailable"]
