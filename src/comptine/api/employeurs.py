"""Private-employer verification (API PAJE020).

The only employer-scoped GET in the spec: verify that a particulier-employeur
is registered with Pajemploi.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from comptine.api._base import ApiCategory


class Employeurs(ApiCategory):
    def verify(self, *, pajemploi_number: str, date_of_birth: date) -> dict[str, Any]:
        """API PAJE020 — Verifier un compte Pajemploi pour un particulier employeur.

        Returns the ``OutputVerificationPe`` payload (status, identity, account state).
        """
        resp = self._client.get(
            f"/tiersdecl/v1/paje/employeurs/{pajemploi_number}/verifier",
            params={"dtNaissanceEmployeur": date_of_birth.isoformat()},
        )
        return self._json(resp) or {}

    def verify_children(
        self,
        *,
        pajemploi_number: str,
        date_of_birth: date,
        children: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """API PAJE022 — Verification des enfants ouvrant droit d'un particulier employeur."""
        resp = self._client.post(
            f"/tiersdecl/v1/paje/employeurs/{pajemploi_number}/enfants/verifier",
            params={"dtNaissanceEmployeur": date_of_birth.isoformat()},
            json={"inputEnfantPe": children},
        )
        return self._json(resp) or {}
