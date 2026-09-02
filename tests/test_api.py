"""Tests for the API category modules.

The TDPAJE backend is Spring-based: a POST with no body carries no
``Content-Type``, and Spring rejects it before any business rule runs
(415 ``Content type '' not supported`` on ``associer``, 400 ``Required request
body is missing`` on ``salaries/verifier``). Every POST must therefore carry a
JSON body, even an empty one.
"""

from __future__ import annotations

import time
from datetime import date

import httpx
import pytest
import respx

from comptine.api import Associer, Enfants, Salaries
from comptine.client import ApiClient, CachedToken, OAuthTokenCache
from comptine.config import Enfant, Environment, OAuthSettings

BASE = "https://api-edi.urssaf.fr/tiersdecl/v1/paje"


@pytest.fixture
def api() -> ApiClient:
    http = httpx.Client()
    settings = OAuthSettings(client_id="cid", client_secret="csecret")
    cache = OAuthTokenCache(Environment.SANDBOX.oauth_url, settings, http)
    cache._cached = CachedToken(
        access_token="TOK", token_type="Bearer", expires_at=time.time() + 3600
    )
    return ApiClient(Environment.SANDBOX, cache, http)


@respx.mock
def test_associer_sends_json_body_when_no_creation_body(api: ApiClient) -> None:
    route = respx.post(f"{BASE}/employeurs/Y418/salarie/associer").mock(
        return_value=httpx.Response(200, json={}),
    )
    with api:
        Associer(api).link(
            employer_pajemploi_number="Y418",
            employer_date_of_birth=date(1994, 10, 9),
            employee_date_of_birth=date(1992, 3, 1),
            employee_pajemploi_number="00000001429220",
        )

    request = route.calls.last.request
    assert request.headers["content-type"] == "application/json"
    assert request.content == b"{}"


@respx.mock
def test_enfants_verify_reports_which_children_open_the_right(api: ApiClient) -> None:
    """PAJE022 answers 1/2 for an "enfant ouvrant droit", 0 otherwise."""
    respx.post(f"{BASE}/employeurs/Y418/enfants/verifier").mock(
        return_value=httpx.Response(
            200,
            json={
                "verificationEnfantPe": [
                    {
                        "reponseStatutEnfant": 1,
                        "inputEnfantPe": {
                            "nomEnfant": "DUPONT",
                            "prenomEnfant": "LEA",
                            "dtNaissanceEnfant": "2021-02-18",
                        },
                    },
                    {
                        "reponseStatutEnfant": 0,
                        "inputEnfantPe": {
                            "nomEnfant": "DUPONT",
                            "prenomEnfant": "CYPRIEN",
                            "dtNaissanceEnfant": "2018-12-19",
                        },
                    },
                ]
            },
        ),
    )
    enfants = [
        Enfant(nom="Dupont", prenom="Lea", date_naissance=date(2021, 2, 18)),
        Enfant(nom="Dupont", prenom="Cyprien", date_naissance=date(2018, 12, 19)),
    ]
    with api:
        opening = Enfants(api).opening_the_right(
            employer_pajemploi_number="Y418",
            employer_date_of_birth=date(1985, 3, 10),
            enfants=enfants,
        )

    assert [e.prenom for e in opening] == ["Lea"]


@respx.mock
def test_salarie_verify_sends_json_body_when_no_identification(api: ApiClient) -> None:
    route = respx.post(f"{BASE}/salaries/verifier").mock(
        return_value=httpx.Response(200, json={}),
    )
    with api:
        Salaries(api).verify(pajemploi_number="00000001429220", date_of_birth=date(1992, 3, 1))

    request = route.calls.last.request
    assert request.headers["content-type"] == "application/json"
    assert request.content == b"{}"
