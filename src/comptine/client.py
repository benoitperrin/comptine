"""HTTP client primitives: OAuth token cache and retrying HTTP session."""

from __future__ import annotations

import logging
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Self

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from comptine.config import Config, Environment, OAuthSettings

logger = logging.getLogger(__name__)

_REFRESH_MARGIN_SECONDS = 60
"""Refresh the access token this many seconds before it would expire."""


@dataclass(frozen=True)
class CachedToken:
    access_token: str
    token_type: str
    expires_at: float  # epoch seconds

    @property
    def is_fresh(self) -> bool:
        return time.time() < self.expires_at - _REFRESH_MARGIN_SECONDS


class ApiError(RuntimeError):
    """Raised when the Pajemploi API returns a non-success response."""

    def __init__(
        self,
        status_code: int,
        code: str | None,
        message: str,
        details: Any = None,
        request_id: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(f"[HTTP {status_code}] {code or '?'}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.request_id = request_id
        self.url = url

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "request_id": self.request_id,
            "url": self.url,
        }


class OAuthTokenCache:
    """In-memory cache for OAuth2 client_credentials tokens.

    Tokens are refreshed automatically when within ``_REFRESH_MARGIN_SECONDS`` of expiry.
    There is no on-disk persistence: a sandbox token only lives for an hour anyway, and
    avoiding disk state keeps the threat model tiny.
    """

    def __init__(
        self,
        oauth_url: str,
        settings: OAuthSettings,
        http_client: httpx.Client,
    ) -> None:
        self._oauth_url = oauth_url
        self._settings = settings
        self._http = http_client
        self._cached: CachedToken | None = None

    def get(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._cached is not None and self._cached.is_fresh:
            return self._cached.access_token
        self._cached = self._fetch()
        return self._cached.access_token

    def invalidate(self) -> None:
        self._cached = None

    def _fetch(self) -> CachedToken:
        logger.debug("Fetching new OAuth token from %s", self._oauth_url)
        resp = self._http.post(
            self._oauth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "scope": self._settings.scope,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise ApiError(
                status_code=resp.status_code,
                code="oauth_error",
                message=f"OAuth token request failed: {resp.text[:300]}",
                url=self._oauth_url,
            )
        body = resp.json()
        return CachedToken(
            access_token=body["access_token"],
            token_type=body.get("token_type", "Bearer"),
            expires_at=time.time() + int(body.get("expires_in", 3600)),
        )


class ApiClient(AbstractContextManager["ApiClient"]):
    """Authenticated HTTP client for the Pajemploi API.

    Use as a context manager so the underlying ``httpx.Client`` is closed cleanly::

        with ApiClient.from_config(cfg) as api:
            api.get("/td-paje/v1/employeurs")
    """

    def __init__(
        self,
        env: Environment,
        token_cache: OAuthTokenCache,
        http: httpx.Client,
    ) -> None:
        self.env = env
        self._token_cache = token_cache
        self._http = http

    @classmethod
    def from_config(cls, cfg: Config) -> Self:
        http = httpx.Client(
            timeout=cfg.http_timeout_seconds,
            headers={
                "User-Agent": "comptine/0.1 (+https://github.com/benoitperrin/comptine)",
                "Accept": "application/json",
            },
        )
        token_cache = OAuthTokenCache(cfg.env.oauth_url, cfg.oauth, http)
        return cls(env=cfg.env, token_cache=token_cache, http=http)

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def base_url(self) -> str:
        return self.env.api_base

    # --- low-level HTTP -------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
        retry_on_401: bool = True,
    ) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        request_headers = {
            "Authorization": f"Bearer {self._token_cache.get()}",
            **(headers or {}),
        }
        logger.debug("HTTP %s %s", method, url)
        resp = self._http.request(method, url, params=params, json=json, headers=request_headers)

        if resp.status_code == 401 and retry_on_401:
            logger.info("Received 401; invalidating token and retrying once")
            self._token_cache.invalidate()
            return self.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
                expect_json=expect_json,
                retry_on_401=False,
            )

        if resp.status_code >= 400:
            raise _make_api_error(resp, expect_json=expect_json)

        return resp

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)


def _make_api_error(resp: httpx.Response, *, expect_json: bool) -> ApiError:
    """Decode an error response into a structured ApiError."""
    code: str | None = None
    message: str = resp.reason_phrase or "Unknown error"
    details: Any = None
    if expect_json and resp.headers.get("content-type", "").startswith("application/json"):
        try:
            body = resp.json()
            details = body
            # The gateway returns business errors as a *list* of {code, message,
            # description} — e.g. [{"code": "ER_API_MANDAT_VERIFICATION", ...}].
            # Reading only the dict shape turned those into a bare "400".
            first = body[0] if isinstance(body, list) and body else body
            if isinstance(first, dict):
                code = first.get("code") or first.get("erreur") or first.get("error_code")
                message = (
                    first.get("message")
                    or first.get("libelle")
                    or first.get("description")
                    or first.get("error_description")
                    or message
                )
        except ValueError:
            details = resp.text[:500]
    else:
        details = resp.text[:500]
    return ApiError(
        status_code=resp.status_code,
        code=code,
        message=message,
        details=details,
        request_id=(
            resp.headers.get("x-request-id")
            or resp.headers.get("x-correlationid")
            or resp.headers.get("x-correlation-id")
        ),
        url=str(resp.request.url),
    )
