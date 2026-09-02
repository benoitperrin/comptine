"""MCP server for comptine, in two modes from a single code path.

**Local (stdio)** — for the third-party declarant's own machine. The employer comes
from the configuration.

**Hosted (streamable HTTP)** — for people the operator has given an account to. They
add one URL as a custom connector and install nothing.

The hosted mode is where the risk lives, so the design starts there:

* The declarant holds **one** set of Acoss credentials, and those credentials reach
  every employer who granted a mandate. So the employer a call acts on is resolved
  from the caller's account and **never** from a tool argument. No tool takes an
  ``employeur`` parameter; there is no code path that reads one from the client.
* Identity comes from a bearer token, matched against the SHA-256 stored in the
  configuration. Resolution **fails closed**: with no identity, hosted mode refuses
  every call rather than falling back on a default employer.
* ``mandat register`` and ``mandat cancel`` are not exposed at all. A mandate is a
  legal act that follows a signed paper, so the operator runs it by hand.
* The real declaration is doubly locked: the account must carry ``peut_declarer``,
  and the caller must hand back the confirmation token minted by ``predeclarer``.
  That token is a digest of the exact body, so a declaration can only go through
  if it is the one that was shown a moment earlier.
* Every call is appended to a JSON-lines audit log, with the account and the body.
  It is the only evidence available the day someone disputes a declaration.

Run it::

    comptine-mcp                      # stdio, local
    comptine-mcp --http --port 8787   # hosted
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import hmac
import json
import logging
import os
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from platformdirs import user_state_dir

from comptine.api import Declarer as DeclarerApi
from comptine.api import Employeurs, Enfants, Estimer, Predeclarer, Salaries
from comptine.client import ApiClient, ApiError
from comptine.config import Compte, Config, Environment, ParticulierEmployeur, Salarie, load_config
from comptine.mapper import (
    DeclarationWindowError,
    check_declaration_window,
    children_to_wire,
    to_declaration_ged,
    to_estimation_ged,
)
from comptine.models import Employee
from comptine.sheet import HoursSource, MonthlySheetReader

logger = logging.getLogger("comptine.mcp")

_PRINCIPAL: contextvars.ContextVar[Compte | None] = contextvars.ContextVar(
    "principal", default=None
)
"""The account behind the call in hosted mode; ``None`` in local mode."""


class AccessDenied(RuntimeError):
    """Raised when a call cannot be tied to an authorised account or salarié."""


# --- audit ------------------------------------------------------------------------


def _audit_path() -> Path:
    override = os.environ.get("COMPTINE_AUDIT_LOG")
    if override:
        return Path(override)
    return Path(user_state_dir("comptine")) / "audit.jsonl"


def _audit(tool: str, **fields: Any) -> None:
    """Append one line to the audit log. Never raises: a failure here must not
    swallow a call that has already reached the Urssaf."""
    principal = _PRINCIPAL.get()
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool": tool,
        "compte": principal.libelle if principal else "local",
        "employeur": principal.employeur if principal else None,
        **fields,
    }
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as e:  # pragma: no cover - best effort
        logger.warning("Audit log write failed: %s", e)


# --- identity and scoping ---------------------------------------------------------


def token_digest(token: str) -> str:
    """Hex SHA-256 of a bearer token — what the configuration stores."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def resolve_account(cfg: Config, token: str | None) -> Compte | None:
    """Match a bearer token against the configured accounts, in constant time."""
    if not token:
        return None
    digest = token_digest(token)
    for compte in cfg.comptes.values():
        if hmac.compare_digest(compte.token_sha256, digest):
            return compte
    return None


def current_employer(cfg: Config, *, hosted: bool = False) -> tuple[str, ParticulierEmployeur]:
    """Resolve the employer this call may act on.

    Hosted mode reads it from the caller's account and refuses without one. Local
    mode reads it from ``COMPTINE_EMPLOYEUR``, or from the configuration when it
    holds exactly one employer.
    """
    principal = _PRINCIPAL.get()
    if principal is not None:
        employer = cfg.particuliers_employeurs.get(principal.employeur)
        if employer is None:
            raise AccessDenied(f"Account points at unknown employer {principal.employeur!r}.")
        return principal.employeur, employer

    if hosted:
        raise AccessDenied(
            "No account behind this call. The hosted server refuses rather than "
            "falling back on a default employer."
        )

    handle = os.environ.get("COMPTINE_EMPLOYEUR")
    if handle:
        employer = cfg.particuliers_employeurs.get(handle)
        if employer is None:
            raise AccessDenied(f"COMPTINE_EMPLOYEUR={handle!r} is not in the configuration.")
        return handle, employer
    if len(cfg.particuliers_employeurs) == 1:
        return next(iter(cfg.particuliers_employeurs.items()))
    raise AccessDenied("Several employers are configured: set COMPTINE_EMPLOYEUR to pick one.")


def current_salarie(cfg: Config, handle: str, *, hosted: bool = False) -> Salarie:
    """Return a salarié the caller may act on, or refuse.

    Two checks, both needed: the salarié must belong to the resolved employer, and
    the account must list it (an empty list means every salarié of that employer).
    """
    salarie = cfg.salaries.get(handle)
    if salarie is None:
        raise AccessDenied(f"Unknown salarié {handle!r}.")
    employer_handle, _ = current_employer(cfg, hosted=hosted)
    if salarie.employeur != employer_handle:
        raise AccessDenied(f"Salarié {handle!r} does not belong to your employer.")
    principal = _PRINCIPAL.get()
    if principal is not None and principal.salaries and handle not in principal.salaries:
        raise AccessDenied(f"Your account may not act on salarié {handle!r}.")
    return salarie


def allowed_salaries(cfg: Config, *, hosted: bool = False) -> list[str]:
    employer_handle, _ = current_employer(cfg, hosted=hosted)
    principal = _PRINCIPAL.get()
    handles = [h for h, s in cfg.salaries.items() if s.employeur == employer_handle]
    if principal is not None and principal.salaries:
        handles = [h for h in handles if h in principal.salaries]
    return handles


# --- declaration bodies -----------------------------------------------------------


def confirmation_token(body: dict[str, Any]) -> str:
    """Digest of the exact body, minted by ``predeclarer`` and required by ``declarer``.

    Any change between the two — a different month, an edited amount — changes the
    digest and the declaration is refused.
    """
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _employee(salarie: Salarie) -> Employee:
    return Employee(
        pajemploi_number=salarie.n_salarie_paje,
        last_name=salarie.nom or "",
        first_name=salarie.prenom or "",
        nir=salarie.nir,
    )


def _build_declaration(
    cfg: Config, salarie_handle: str, mois: str, hours_source: str, *, hosted: bool = False
) -> tuple[dict[str, Any], ParticulierEmployeur, Salarie, date]:
    salarie = current_salarie(cfg, salarie_handle, hosted=hosted)
    _, employer = current_employer(cfg, hosted=hosted)
    reader = MonthlySheetReader.from_config(cfg, salarie_handle)
    month = reader.read_month(mois, hours_source=HoursSource(hours_source))
    if month.net_salary is None:
        raise ValueError(f"The sheet row for {mois} carries no net salary.")
    body = to_declaration_ged(
        month, employee=_employee(salarie), children=children_to_wire(employer.enfants)
    )
    return body, employer, salarie, month.period_start


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1, default=_default)


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"Cannot serialise {type(obj).__name__}")


def _error(e: Exception) -> str:
    if isinstance(e, ApiError):
        return _json({"erreur": e.to_dict()})
    return _json({"erreur": {"type": type(e).__name__, "message": str(e)}})


# --- the server -------------------------------------------------------------------


def build_server(cfg: Config, *, hosted: bool = False) -> Any:  # noqa: PLR0915 — one function per tool, FastMCP style
    """Build the FastMCP server. Imported lazily so the package works without the MCP extra."""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415 — optional extra, imported on demand

    mcp = FastMCP("comptine")

    @mcp.tool()
    def comptine_etat() -> str:
        """État du service : environnement, employeur du compte, salariés autorisés,
        fenêtre déclarative du mois en cours. À appeler en premier."""
        try:
            handle, employer = current_employer(cfg, hosted=hosted)
            principal = _PRINCIPAL.get()
            today = date.today()
            window_open = today.day >= 25
            return _json(
                {
                    "environnement": cfg.env.value,
                    "employeur": {
                        "reference": handle,
                        "nom": f"{employer.prenom} {employer.nom}",
                        "numero_pajemploi": employer.n_employeur_paje,
                        "enfants_declares": [f"{e.prenom} {e.nom}" for e in employer.enfants],
                    },
                    "salaries_autorises": allowed_salaries(cfg, hosted=hosted),
                    "declaration": {
                        "mois_en_cours": today.strftime("%Y-%m"),
                        "fenetre_ouverte": window_open,
                        "note": (
                            "La fenêtre d'un mois d'emploi ouvre le 25 de ce mois. "
                            "Il n'y a pas de date butoir."
                        ),
                    },
                    "ecriture_autorisee": principal.peut_declarer if principal else True,
                }
            )
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_verifier_employeur() -> str:
        """Vérifie le compte Pajemploi de l'employeur : actif, et autorisé à déléguer."""
        try:
            _, employer = current_employer(cfg, hosted=hosted)
            with ApiClient.from_config(cfg) as api:
                out = Employeurs(api).verify(
                    pajemploi_number=_need(employer.n_employeur_paje),
                    date_of_birth=_need(employer.date_naissance),
                )
            _audit("verifier_employeur")
            return _json(out)
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_verifier_salarie(salarie: str) -> str:
        """Vérifie le compte Pajemploi d'un salarié et rend son numéro Pajemploi."""
        try:
            s = current_salarie(cfg, salarie, hosted=hosted)
            with ApiClient.from_config(cfg) as api:
                out = Salaries(api).verify(
                    pajemploi_number=s.n_salarie_paje,
                    nir=s.nir,
                    date_of_birth=s.date_naissance,
                )
            _audit("verifier_salarie", salarie=salarie)
            return _json(out)
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_enfants_ouvrant_droit() -> str:
        """Demande au SI Pajemploi lesquels des enfants configurés ouvrent droit au CMG
        (API PAJE022). Seuls ceux-là entrent dans une déclaration."""
        try:
            _, employer = current_employer(cfg, hosted=hosted)
            with ApiClient.from_config(cfg) as api:
                out = Enfants(api).verify(
                    employer_pajemploi_number=_need(employer.n_employeur_paje),
                    employer_date_of_birth=_need(employer.date_naissance),
                    enfants=employer.enfants,
                )
            _audit("enfants_ouvrant_droit")
            return _json(out)
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_estimer(salarie: str, mois: str, heures: str = "mensualisation") -> str:
        """Estime les cotisations d'un mois, sans aucun effet. ``mois`` au format AAAA-MM.
        ``heures`` vaut « mensualisation » (heures du contrat) ou « sheet » (heures réelles)."""
        try:
            s = current_salarie(cfg, salarie, hosted=hosted)
            _, employer = current_employer(cfg, hosted=hosted)
            reader = MonthlySheetReader.from_config(cfg, salarie)
            month = reader.read_month(mois, hours_source=HoursSource(heures))
            body = to_estimation_ged(month, employer_postal_code=employer.code_postal)
            with ApiClient.from_config(cfg) as api:
                out = Estimer(api).ged(body)
            _audit("estimer", salarie=salarie, mois=mois, corps=body)
            return _json({"envoye": body, "estimation": out, "salarie": s.nom})
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_apercu(salarie: str, mois: str, heures: str = "mensualisation") -> str:
        """Montre le corps exact qui serait déclaré, sans aucun appel réseau.
        À lire avant toute prédéclaration."""
        try:
            body, _, _, _ = _build_declaration(cfg, salarie, mois, heures, hosted=hosted)
            return _json({"apercu": True, "corps": body})
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_predeclarer(salarie: str, mois: str, heures: str = "mensualisation") -> str:
        """Fait calculer la déclaration par l'Urssaf sans rien valider, et rend le jeton
        de confirmation à repasser à ``comptine_declarer``."""
        try:
            body, employer, _, _ = _build_declaration(cfg, salarie, mois, heures, hosted=hosted)
            with ApiClient.from_config(cfg) as api:
                out = Predeclarer(api).ged(
                    employer_pajemploi_number=_need(employer.n_employeur_paje),
                    employer_date_of_birth=_need(employer.date_naissance),
                    body=body,
                )
            token = confirmation_token(body)
            _audit("predeclarer", salarie=salarie, mois=mois, corps=body, jeton=token)
            return _json(
                {
                    "predeclaration": out,
                    "corps": body,
                    "jeton_de_confirmation": token,
                    "note": (
                        "Rien n'est validé. Relisez le corps, puis appelez "
                        "comptine_declarer avec ce jeton pour déclarer réellement."
                    ),
                }
            )
        except Exception as e:
            return _error(e)

    @mcp.tool()
    def comptine_declarer(
        salarie: str, mois: str, jeton_de_confirmation: str, heures: str = "mensualisation"
    ) -> str:
        """Dépose la déclaration réelle. Engage des cotisations et n'est modifiable en ligne
        qu'un mois. Exige le jeton rendu par ``comptine_predeclarer`` pour le même corps."""
        try:
            principal = _PRINCIPAL.get()
            if principal is not None and not principal.peut_declarer:
                raise AccessDenied("Your account is not allowed to file declarations.")

            body, employer, _, period_start = _build_declaration(
                cfg, salarie, mois, heures, hosted=hosted
            )
            expected = confirmation_token(body)
            if jeton_de_confirmation.strip() != expected:
                raise AccessDenied(
                    "Confirmation token does not match this declaration. Run "
                    "comptine_predeclarer again and re-read the body: something changed."
                )
            check_declaration_window(period_start, today=date.today())

            with ApiClient.from_config(cfg) as api:
                out = DeclarerApi(api).ged(
                    employer_pajemploi_number=_need(employer.n_employeur_paje),
                    employer_date_of_birth=_need(employer.date_naissance),
                    body=body,
                )
            _audit("declarer", salarie=salarie, mois=mois, corps=body, reponse=out)
            return _json({"declaration": out, "corps": body})
        except DeclarationWindowError as e:
            return _json({"erreur": {"type": "FenetreFermee", "message": str(e)}})
        except Exception as e:
            return _error(e)

    return mcp


def _need(value: Any) -> Any:
    if value is None:
        raise ValueError("Missing employer identifier or date of birth in the configuration.")
    return value


# --- hosted mode ------------------------------------------------------------------

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class BearerAuth:
    """ASGI middleware that ties every HTTP request to a configured account.

    It sets the principal for the duration of the request. Tools read it through
    :func:`current_employer`, which refuses when it is missing, so a failure to
    propagate the context denies the call instead of leaking one.
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], cfg: Config) -> None:
        self.app = app
        self.cfg = cfg

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        authorization = headers.get("authorization", "")
        token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        account = resolve_account(self.cfg, token)
        if account is None:
            await _unauthorized(send)
            return
        reset = _PRINCIPAL.set(account)
        try:
            await self.app(scope, receive, send)
        finally:
            _PRINCIPAL.reset(reset)


async def _unauthorized(send: Send) -> None:
    body = json.dumps({"error": "unauthorized"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="comptine"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _print_new_account(libelle: str, employeur: str) -> None:
    """Mint a bearer token and print the config block to paste.

    The token is shown once, here, and never stored: the configuration keeps only
    its digest, so a stolen config file does not hand over anyone's access.
    """
    token = secrets.token_urlsafe(32)
    block = {
        libelle.lower().replace(" ", "_") or "compte": {
            "token_sha256": token_digest(token),
            "employeur": employeur,
            "libelle": libelle,
            "salaries": [],
            "peut_declarer": False,
        }
    }
    print("Jeton à transmettre à la personne, une seule fois :\n")
    print(f"    {token}\n")
    print("Bloc à ajouter sous `comptes` dans la configuration :\n")
    print(json.dumps(block, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="comptine-mcp", description=__doc__)
    parser.add_argument("--http", action="store_true", help="Serve over HTTP instead of stdio.")
    parser.add_argument(
        "--nouveau-compte",
        metavar="LIBELLE",
        help="Mint a bearer token for one person and print the config block, then exit.",
    )
    parser.add_argument(
        "--employeur", help="Employer handle the new account is tied to (with --nouveau-compte)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--env", choices=[e.value for e in Environment])
    args = parser.parse_args()

    if args.nouveau_compte:
        if not args.employeur:
            raise SystemExit("--nouveau-compte needs --employeur: an account is tied to one.")
        _print_new_account(args.nouveau_compte, args.employeur)
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(env_override=Environment(args.env) if args.env else None)
    server = build_server(cfg, hosted=args.http)

    if not args.http:
        server.run()
        return

    if not cfg.comptes:
        raise SystemExit(
            "Hosted mode with no account configured: every call would be refused. "
            "Mint one with: comptine-mcp --nouveau-compte 'Claire' --employeur claire"
        )
    import uvicorn  # noqa: PLC0415 — optional extra, only needed to serve over HTTP

    app = BearerAuth(server.streamable_http_app(), cfg)
    logger.info(
        "comptine MCP on http://%s:%d/mcp — %d account(s)", args.host, args.port, len(cfg.comptes)
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
