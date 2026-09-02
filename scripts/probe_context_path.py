"""Find the Gravitee context-path of the TDPAJE API.

Discriminator: when the gateway has no API registered at a path, Gravitee returns
HTTP 404 with body ``{"message": "No context-path matches the request URI."}``.
Any other response (different message, 401/403, 405, 2xx) means the path
matches an API.

Strategy: try a wide set of slug variants at the root and emit anything that
deviates from the "No context-path matches" baseline. Runs fast because the
gateway answers in <50 ms once cached.
"""

from __future__ import annotations

import json
import logging
import sys
from itertools import product
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from comptine import ApiClient, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("ctx-probe")

NO_CTX_MARKER = "No context-path matches the request URI"

# Slug components combined into context-path candidates.
ROOTS = ["", "/api", "/v1", "/api/v1", "/sandbox", "/sandbox/api"]
PRIMARY = [
    "td-paje",
    "tdpaje",
    "td_paje",
    "tdpajemploi",
    "td-pajemploi",
    "tierce-declaration-paje",
    "tierce-declaration-pajemploi",
    "tierce-declaration-paje-api",
    "tierce-declaration",
    "paje",
    "pajemploi",
    "paje-tierce-declaration",
    "pajemploi-tierce-declaration",
    "particulier-employeur-paje",
    "tiers-declarant-paje",
    "tiers-declaration-paje",
    "tiers-declarant-pajemploi",
    "declarations-paje",
    "declarations-pajemploi",
    "edi-paje",
    "edi-pajemploi",
    "echange-edi-paje",
    "echange-edi-pajemploi",
    "edi/paje",
    "edi/pajemploi",
    "tiercedeclaration-paje",
    "tiercedeclaration",
    "tiercedeclarationpaje",
    "tdpaje-api",
    "tdpaje-rest",
    "tdpaje-v1",
    "td-paje-v1",
    "tdp-paje",
    "tdpajemploi-rest",
    "paje-edi",
    "pajemploi-edi",
    "tiercedeclarant",
    "tiercedeclarant-paje",
    "tiers-declarant",
    "declarant-paje",
    "declarant-pajemploi",
    "td-pjm",
    "tdpjm",
    "ged",
    "ged-paje",
    "assistant-maternel",
    "assmat",
]
VERSIONS = ["", "/v1", "/v2", "/v1.0", "/1.0", "/1", "/sandbox", "/sandbox/v1"]


def main() -> None:
    cfg = load_config()
    with ApiClient.from_config(cfg) as api:
        token = api._token_cache.get()

    hdr = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "comptine/0.1 (+https://github.com/benoitperrin/comptine)",
    }
    findings: list[dict[str, Any]] = []
    base = cfg.env.api_base
    with httpx.Client(base_url=base, headers=hdr, timeout=10.0) as c:
        for root, slug, ver in product(ROOTS, PRIMARY, VERSIONS):
            path = f"{root}/{slug}{ver}"
            try:
                resp = c.get(path)
            except httpx.TransportError as e:
                logger.warning("transport err %s: %s", path, e)
                continue
            body = resp.text[:300]
            if NO_CTX_MARKER not in body and resp.status_code != 404:
                logger.info("HIT %s → %d %s", path, resp.status_code, body[:80])
                findings.append({"path": path, "status": resp.status_code, "body": body})
            elif NO_CTX_MARKER not in body and resp.status_code == 404:
                # Different 404 reason → still a hit
                logger.info("HIT(404-different) %s → %s", path, body[:80])
                findings.append({"path": path, "status": resp.status_code, "body": body})

    out = Path(__file__).parent.parent / "docs" / "api" / "context_path_hits.json"
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("%d candidate context-paths captured in %s", len(findings), out)


if __name__ == "__main__":
    main()
