"""Shared base class for API category modules."""

from __future__ import annotations

from typing import Any

from comptine.client import ApiClient


class ApiCategory:
    """Holds a reference to the :class:`ApiClient` and surfaces small helpers."""

    def __init__(self, client: ApiClient) -> None:
        self._client = client

    def _json(self, response: Any) -> Any:
        """Decode a JSON response; returns ``None`` for empty bodies."""
        if not response.content:
            return None
        return response.json()
