"""Pre-declare a Pajemploi activity (draft state, not yet billed).

* ``POST /employeurs/{n}/ama/predeclarer`` — Assistante maternelle.
* ``POST /employeurs/{n}/ged/predeclarer`` — Garde d'enfants à domicile.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from comptine.api._base import ApiCategory


class Predeclarer(ApiCategory):
    def ged(
        self,
        *,
        employer_pajemploi_number: str,
        employer_date_of_birth: date,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """API PAJE046 — Pré-déclarer le salaire pour une garde à domicile (GED)."""
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{employer_pajemploi_number}/ged/predeclarer",
            params={"dtNaissanceEmployeur": employer_date_of_birth.isoformat()},
            json=body,
        )
        return self._json(resp) or {}

    def ama(
        self,
        *,
        employer_pajemploi_number: str,
        employer_date_of_birth: date,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """API PAJE045 — Pré-déclarer le salaire d'une assistante maternelle (AMA)."""
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{employer_pajemploi_number}/ama/predeclarer",
            params={"dtNaissanceEmployeur": employer_date_of_birth.isoformat()},
            json=body,
        )
        return self._json(resp) or {}
