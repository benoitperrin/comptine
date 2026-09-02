"""Probe the sandbox API to discover real endpoints.

The Urssaf API gateway is Gravitee. Each registered API has a context-path that
is impossible to guess without the OpenAPI spec, so we sweep plausible patterns
and classify responses:

  * 404 (HTML or JSON gateway error) → wrong path
  * 401 / 403 → path exists, auth/scope issue
  * 405 with Allow header → wrong method but path exists
  * 200 / 4xx with JSON body → path exists; capture the shape

Run with the venv activated:

    PYTHONPATH=src python scripts/probe_endpoints.py
"""

from __future__ import annotations

import json
import logging
import time
from itertools import product
from pathlib import Path
from typing import Any

import httpx

from comptine import ApiClient, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("probe")

OUT_DIR = Path(__file__).parent.parent / "docs" / "api"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Candidate context paths (Gravitee maps the API root to one of these).
CONTEXT_PATHS = [
    "",
    "/td-paje",
    "/tdpaje",
    "/tierce-declaration-paje",
    "/tierce-declaration/paje",
    "/api/td-paje",
    "/api/tdpaje",
    "/api/tierce-declaration-paje",
    "/paje/tierce-declaration",
    "/paje",
]

# Version segments tried after the context path.
VERSIONS = ["", "/v1", "/v2", "/v1.0"]

# Resource segments tried after the version.
RESOURCES = [
    "",
    "/employeurs",
    "/employeur",
    "/particuliers-employeurs",
    "/particulier-employeur",
    "/salaries",
    "/salarie",
    "/mandats",
    "/mandat",
    "/associations",
    "/association",
    "/associer",
    "/declarations",
    "/declaration",
    "/declarer",
    "/predeclarer",
    "/predeclarations",
    "/estimer",
    "/estimations",
    "/bulletins",
    "/bulletin",
    "/enfants",
    "/enfant",
    "/operations",
]


_CLASSIFICATION_TABLE = {
    404: "404",
    401: "401-auth",
    403: "403-forbidden",
    405: "405-method",
}


def classify(resp: httpx.Response) -> str:
    if resp.status_code in _CLASSIFICATION_TABLE:
        return _CLASSIFICATION_TABLE[resp.status_code]
    if 200 <= resp.status_code < 300:
        return "2xx"
    if 400 <= resp.status_code < 500:
        return "4xx"
    if 500 <= resp.status_code < 600:
        return "5xx"
    return f"other-{resp.status_code}"


def short_body(resp: httpx.Response, limit: int = 240) -> str:
    text = resp.text
    if len(text) <= limit:
        return text.replace("\n", " ")
    return text[:limit].replace("\n", " ") + "…"


def main() -> None:
    cfg = load_config()
    findings: list[dict[str, Any]] = []
    with ApiClient.from_config(cfg) as api:
        token = api._token_cache.get()  # warm up
        logger.info("OAuth token acquired (env=%s)", cfg.env)
        auth_header = {"Authorization": f"Bearer {token}"}

        # Use the raw httpx.Client to bypass ApiClient's error raising; we want the
        # full status spectrum.
        with httpx.Client(
            base_url=cfg.env.api_base,
            headers={
                "User-Agent": "pajemploi-probe/0.1",
                "Accept": "application/json",
                **auth_header,
            },
            timeout=15.0,
        ) as http:
            for ctx, ver, res in product(CONTEXT_PATHS, VERSIONS, RESOURCES):
                path = f"{ctx}{ver}{res}" or "/"
                if path == "/":
                    continue
                try:
                    resp = http.get(path)
                except httpx.TransportError as e:
                    logger.warning("transport error %s: %s", path, e)
                    continue
                klass = classify(resp)
                if klass != "404":
                    logger.info("GET %-60s → %s", path, klass)
                    findings.append(
                        {
                            "path": path,
                            "method": "GET",
                            "status": resp.status_code,
                            "classification": klass,
                            "content_type": resp.headers.get("content-type"),
                            "body_preview": short_body(resp),
                            "allow_header": resp.headers.get("allow"),
                        }
                    )
                # Tiny politeness delay
                time.sleep(0.02)

    out = OUT_DIR / "probe_results.json"
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %d non-404 findings to %s", len(findings), out)


if __name__ == "__main__":
    main()
