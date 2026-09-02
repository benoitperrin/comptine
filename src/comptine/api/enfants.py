"""Children verification (API PAJE022).

Answers the question the declaration itself cannot: *which* children open the
right (``enfant ouvrant droit``) for a given private employer. Only those belong
in ``inputDeclEnfant`` — the CMG is granted per opening child, not per child in
the household.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from comptine.api._base import ApiCategory
from comptine.config import Enfant
from comptine.mapper import children_to_wire

OPENS_THE_RIGHT = (1, 2)
"""``reponseStatutEnfant``: 1 = found, 2 = found through a partial search."""


class Enfants(ApiCategory):
    def verify(
        self,
        *,
        employer_pajemploi_number: str,
        employer_date_of_birth: date,
        enfants: list[Enfant],
    ) -> dict[str, Any]:
        """API PAJE022 — Vérification des enfants ouvrant droit d'un particulier employeur."""
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{employer_pajemploi_number}/enfants/verifier",
            params={"dtNaissanceEmployeur": employer_date_of_birth.isoformat()},
            json={"inputEnfantPe": children_to_wire(enfants)},
        )
        return self._json(resp) or {}

    def opening_the_right(
        self,
        *,
        employer_pajemploi_number: str,
        employer_date_of_birth: date,
        enfants: list[Enfant],
    ) -> list[Enfant]:
        """Return only the children the SI Pajemploi recognises as opening the right.

        The API echoes each submitted child back with a status, so we match on the
        wire payload rather than trusting the response order.
        """
        out = self.verify(
            employer_pajemploi_number=employer_pajemploi_number,
            employer_date_of_birth=employer_date_of_birth,
            enfants=enfants,
        )
        results: list[dict[str, Any]] = out.get("verificationEnfantPe") or []
        opening_wire = {
            (
                r.get("inputEnfantPe", {}).get("nomEnfant"),
                r.get("inputEnfantPe", {}).get("prenomEnfant"),
                r.get("inputEnfantPe", {}).get("dtNaissanceEnfant"),
            )
            for r in results
            if r.get("reponseStatutEnfant") in OPENS_THE_RIGHT
        }
        return [
            enfant
            for enfant, wire in zip(enfants, children_to_wire(enfants), strict=True)
            if (wire["nomEnfant"], wire["prenomEnfant"], wire["dtNaissanceEnfant"]) in opening_wire
        ]
