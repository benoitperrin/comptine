"""Estimate social contributions (no side effects).

Two endpoints, one per regime:

* ``POST /ama/estimer`` — Assistante maternelle agréée.
* ``POST /ged/estimer`` — Garde d'enfants à domicile.

The bodies (``InputEstimationAma`` / ``InputEstimationGed``) differ slightly,
but both produce an :class:`OutputEstimation` whose key field is
``mntCotiEmplAcharge`` (the employer's net cost).
"""

from __future__ import annotations

from typing import Any

from comptine.api._base import ApiCategory


class Estimer(ApiCategory):
    def ged(self, body: dict[str, Any]) -> dict[str, Any]:
        """API PAJE041 — Estimer pour une activité de garde d'enfant à domicile (GED)."""
        resp = self._client.post("/tiersdecl/v1/paje/ged/estimer", json=body)
        return self._json(resp) or {}

    def ama(self, body: dict[str, Any]) -> dict[str, Any]:
        """API PAJE040 — Estimer pour une activité d'assistante maternelle agréée (AMA)."""
        resp = self._client.post("/tiersdecl/v1/paje/ama/estimer", json=body)
        return self._json(resp) or {}
