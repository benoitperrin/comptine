"""Configuration loading for pajemploi.

Configuration lives in ``~/.config/comptine/config.json`` (mode 600), with a fallback to
``~/.config/pajemploi/`` for installations that predate the rename. Environments
``sandbox`` and ``prod`` map to different OAuth and API hostnames; the active environment
is selected by the ``env`` field, with override via ``COMPTINE_ENV`` or ``--env``.
"""

from __future__ import annotations

import json
import os
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Self

from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, Field


class Environment(StrEnum):
    SANDBOX = "sandbox"
    PROD = "prod"

    @property
    def oauth_url(self) -> str:
        return f"https://{self.api_host}/api/oauth/v1/token"

    @property
    def api_host(self) -> str:
        return {
            Environment.SANDBOX: "api-edi.urssaf.fr",
            Environment.PROD: "api.urssaf.fr",
        }[self]

    @property
    def api_base(self) -> str:
        return f"https://{self.api_host}"


class OAuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str
    client_secret: str
    scope: str = "tiersdecl.paje"


class TiersDeclarant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    siret: str
    raison_sociale: str | None = None


class Enfant(BaseModel):
    """A child looked after, declared as ``inputDeclEnfant`` on every GED declaration."""

    model_config = ConfigDict(extra="forbid")
    nom: str
    prenom: str
    date_naissance: date


class ParticulierEmployeur(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n_employeur_paje: str | None = None
    nom: str
    prenom: str
    date_naissance: date | None = Field(
        default=None,
        description="Required by every employer-scoped API call as ``dtNaissanceEmployeur``.",
    )
    adresse: str | None = None
    code_postal: str | None = None
    ville: str | None = None
    enfants: list[Enfant] = Field(
        default_factory=list,
        description="Children looked after; a GED declaration needs at least one.",
    )


class Salarie(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n_salarie_paje: str | None = None
    employeur: str
    nom: str | None = None
    prenom: str | None = None
    nir: str | None = None
    date_naissance: date | None = None
    activity_regime: str = Field(
        default="ged",
        description='"ged" (garde d\'enfants à domicile) or "ama" (assistante maternelle agréée).',
    )
    sheet_id: str | None = None
    sheet_tab: str = "Suivi"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    env: Environment = Environment.SANDBOX
    oauth: OAuthSettings
    tiers_declarant: TiersDeclarant
    particuliers_employeurs: dict[str, ParticulierEmployeur] = Field(default_factory=dict)
    salaries: dict[str, Salarie] = Field(default_factory=dict)
    google_credentials_path: str | None = None
    http_timeout_seconds: float = 30.0

    @classmethod
    def from_file(cls, path: Path) -> Self:
        with path.open(encoding="utf-8") as f:
            return cls.model_validate(json.load(f))


def default_config_path(env: Environment | None = None) -> Path:
    """Return the default config path.

    If ``env`` is set, ``config.<env>.json`` is tried first, falling back to ``config.json``.
    """
    bases = [Path(user_config_dir("comptine")), Path(user_config_dir("pajemploi"))]
    if env is not None:
        for base in bases:
            candidate = base / f"config.{env.value}.json"
            if candidate.exists():
                return candidate
    for base in bases:
        candidate = base / "config.json"
        if candidate.exists():
            return candidate
    return bases[0] / "config.json"


def load_config(path: Path | None = None, env_override: Environment | None = None) -> Config:
    """Load and validate the config from disk.

    Order of resolution for the environment:
    1. ``env_override`` argument.
    2. ``COMPTINE_ENV`` env var.
    3. Whatever the file declares.
    """
    env_from_env = os.environ.get("COMPTINE_ENV")
    target_env: Environment | None = env_override
    if target_env is None and env_from_env:
        target_env = Environment(env_from_env)

    config_path = path or default_config_path(target_env)
    if not config_path.exists():
        raise FileNotFoundError(
            f"No pajemploi config at {config_path}. Create one (mode 600) — see README."
        )
    cfg = Config.from_file(config_path)
    if target_env is not None:
        cfg = cfg.model_copy(update={"env": target_env})
    return cfg
