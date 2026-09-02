"""Associate a worker to a private employer (API PAJE023)."""

from __future__ import annotations

from datetime import date
from typing import Any

from comptine.api._base import ApiCategory


class Associer(ApiCategory):
    def link(
        self,
        *,
        employer_pajemploi_number: str,
        employer_date_of_birth: date,
        employee_date_of_birth: date,
        employee_pajemploi_number: str | None = None,
        employee_nir: str | None = None,
        creation_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """API PAJE023 — Associer un salarie a un particulier employeur."""
        params: dict[str, str] = {
            "dtNaissanceEmployeur": employer_date_of_birth.isoformat(),
            "dtNaissanceSalarie": employee_date_of_birth.isoformat(),
        }
        if employee_pajemploi_number is not None:
            params["numeroPajeSalarie"] = employee_pajemploi_number
        if employee_nir is not None:
            params["nirSalarie"] = employee_nir
        # Spring rejects a body-less POST with 415 before any business rule runs,
        # so always send an object — empty when the worker already exists.
        body = {"inputCreationSalarie": creation_body} if creation_body else {}
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{employer_pajemploi_number}/salarie/associer",
            params=params,
            json=body,
        )
        return self._json(resp) or {}
