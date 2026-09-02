"""Tests for the OAuth token cache and retrying HTTP client."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx

from comptine.client import ApiClient, ApiError, CachedToken, OAuthTokenCache
from comptine.config import Environment, OAuthSettings


@pytest.fixture
def oauth_settings() -> OAuthSettings:
    return OAuthSettings(client_id="cid", client_secret="csecret")


@respx.mock
def test_token_fetched_once_then_cached(oauth_settings: OAuthSettings) -> None:
    route = respx.post("https://api-edi.urssaf.fr/api/oauth/v1/token").mock(
        return_value=httpx.Response(200, json={"access_token": "TOK", "expires_in": 3600}),
    )
    with httpx.Client() as http:
        cache = OAuthTokenCache(Environment.SANDBOX.oauth_url, oauth_settings, http)
        assert cache.get() == "TOK"
        assert cache.get() == "TOK"
    assert route.call_count == 1


@respx.mock
def test_token_refreshed_when_expired(oauth_settings: OAuthSettings) -> None:
    route = respx.post("https://api-edi.urssaf.fr/api/oauth/v1/token").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "TOK", "expires_in": 1},  # already inside the refresh margin
        ),
    )
    with httpx.Client() as http:
        cache = OAuthTokenCache(Environment.SANDBOX.oauth_url, oauth_settings, http)
        cache.get()
        time.sleep(0)  # no need to actually wait; expires_in=1 is below the 60s margin
        cache.get()
    assert route.call_count == 2


@respx.mock
def test_token_error_raises_api_error(oauth_settings: OAuthSettings) -> None:
    respx.post("https://api-edi.urssaf.fr/api/oauth/v1/token").mock(
        return_value=httpx.Response(401, text="bad creds"),
    )
    with httpx.Client() as http:
        cache = OAuthTokenCache(Environment.SANDBOX.oauth_url, oauth_settings, http)
        with pytest.raises(ApiError) as ei:
            cache.get()
    assert ei.value.status_code == 401


def _api_with_token(token: str = "TOK") -> ApiClient:
    """Build an ApiClient with the token already cached, so no OAuth call is made."""
    http = httpx.Client()
    settings = OAuthSettings(client_id="cid", client_secret="csecret")
    cache = OAuthTokenCache(Environment.SANDBOX.oauth_url, settings, http)
    cache._cached = CachedToken(  # type: ignore[attr-defined]
        access_token=token, token_type="Bearer", expires_at=time.time() + 3600
    )
    return ApiClient(Environment.SANDBOX, cache, http)


@respx.mock
def test_get_decodes_4xx_json_into_apierror() -> None:
    respx.get("https://api-edi.urssaf.fr/mandats").mock(
        return_value=httpx.Response(
            400,
            json={"code": "INVALID", "message": "siret missing"},
            headers={"content-type": "application/json"},
        ),
    )
    with _api_with_token() as api, pytest.raises(ApiError) as ei:
        api.get("/mandats")
    assert ei.value.status_code == 400
    assert ei.value.code == "INVALID"
    assert ei.value.message == "siret missing"


@respx.mock
def test_get_passes_authorization_header() -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    respx.get("https://api-edi.urssaf.fr/whatever").mock(side_effect=_capture)
    with _api_with_token("hello") as api:
        api.get("/whatever")
    assert captured["auth"] == "Bearer hello"


@respx.mock
def test_401_invalidates_token_and_retries_once() -> None:
    refresh_route = respx.post("https://api-edi.urssaf.fr/api/oauth/v1/token").mock(
        return_value=httpx.Response(200, json={"access_token": "NEW", "expires_in": 3600}),
    )
    api_route = respx.get("https://api-edi.urssaf.fr/x").mock(
        side_effect=[
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json={"ok": True}),
        ],
    )
    with _api_with_token("OLD") as api:
        resp = api.get("/x")
    assert resp.status_code == 200
    assert api_route.call_count == 2
    assert refresh_route.call_count == 1
