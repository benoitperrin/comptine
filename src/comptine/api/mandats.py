"""Mandate operations.

Per the official OpenAPI spec (TDPAJE 1.5.1), only two operations exist on
``/mandats``:

* ``POST /mandats?numeroPaje=...&dateNaissance=...`` registers a mandate.
* ``DELETE /mandats?numeroPaje=...&dateNaissance=...`` cancels one.

There is no list/get endpoint: a third-party declarant cannot enumerate the
employers it can declare for.
"""

from __future__ import annotations

from datetime import date

from comptine.api._base import ApiCategory

PATH = "/tiersdecl/v1/paje/mandats"


class Mandats(ApiCategory):
    def register(
        self, *, employer_pajemploi_number: str, employer_date_of_birth: date
    ) -> dict[str, object]:
        """API PAJE030 — Enregistrement de mandat.

        Args:
            employer_pajemploi_number: ``numeroPaje`` of the private employer.
            employer_date_of_birth: ``dateNaissance`` (yyyy-MM-dd), used as a second-factor check.
        """
        resp = self._client.post(
            PATH,
            params={
                "numeroPaje": employer_pajemploi_number,
                "dateNaissance": employer_date_of_birth.isoformat(),
            },
            json={},
            headers={"Content-Type": "application/json"},
        )
        return self._json(resp) or {}

    def cancel(
        self, *, employer_pajemploi_number: str, employer_date_of_birth: date
    ) -> dict[str, object]:
        """API PAJE031 — Annulation de mandat."""
        resp = self._client.delete(
            PATH,
            params={
                "numeroPaje": employer_pajemploi_number,
                "dateNaissance": employer_date_of_birth.isoformat(),
            },
        )
        return self._json(resp) or {}
