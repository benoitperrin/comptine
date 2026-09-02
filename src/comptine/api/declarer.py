"""Submit a final declaration (cotisations are billed).

* ``POST /employeurs/{n}/ama/declarer`` — Assistante maternelle.
* ``POST /employeurs/{n}/ged/declarer`` — Garde d'enfants à domicile.

The response (``OutputDeclaration``) carries ``referenceDocumentaire`` — the
"volet social n°" printed on the official pay slip.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from comptine.api._base import ApiCategory


class Declarer(ApiCategory):
    def ged(
        self,
        *,
        employer_pajemploi_number: str,
        employer_date_of_birth: date,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """API PAJE051 — Déclarer le salaire d'une garde à domicile (GED)."""
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{employer_pajemploi_number}/ged/declarer",
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
        """API PAJE050 — Déclarer le salaire d'une assistante maternelle (AMA)."""
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{employer_pajemploi_number}/ama/declarer",
            params={"dtNaissanceEmployeur": employer_date_of_birth.isoformat()},
            json=body,
        )
        return self._json(resp) or {}
