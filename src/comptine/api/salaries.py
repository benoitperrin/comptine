"""Domestic-worker verification (API PAJE021)."""

from __future__ import annotations

from datetime import date
from typing import Any

from comptine.api._base import ApiCategory


class Salaries(ApiCategory):
    def verify(
        self,
        *,
        pajemploi_number: str | None = None,
        nir: str | None = None,
        date_of_birth: date | None = None,
        identification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """API PAJE021 — Verifier un compte Pajemploi pour un salarie particulier.

        Needs one of: Pajemploi number, NIR + DOB, or a full identification body.
        """
        params: dict[str, str] = {}
        if pajemploi_number is not None:
            params["numeroPajeSalarie"] = pajemploi_number
        if nir is not None:
            params["nirSalarie"] = nir
        if date_of_birth is not None:
            params["dtNaissanceSalarie"] = date_of_birth.isoformat()
        # Spring rejects a body-less POST with 400 "Required request body is missing",
        # so always send an object — empty when the query params carry the identity.
        body = {"inputIdentificationSalarie": identification} if identification else {}
        resp = self._client.post("/tiersdecl/v1/paje/salaries/verifier", params=params, json=body)
        return self._json(resp) or {}
