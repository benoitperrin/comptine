"""Command-line interface.

JSON output is the default, so an LLM or shell pipeline can consume it directly.
Pass ``--text`` for a humanised display. Exit codes:

  * ``0``  on success.
  * ``2``  on usage / configuration error.
  * ``3``  on API error.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from comptine import __version__
from comptine.api import (
    Declarer,
    Employeurs,
    Enfants,
    Estimer,
    Mandats,
    Predeclarer,
    Salaries,
)
from comptine.client import ApiClient, ApiError
from comptine.config import Config, Environment, ParticulierEmployeur, Salarie, load_config
from comptine.mapper import (
    DeclarationWindowError,
    check_declaration_window,
    children_to_wire,
    to_declaration_ged,
    to_estimation_ged,
)
from comptine.models import Employee
from comptine.sheet import HoursSource, MonthlySheetReader, WriteBackUnavailable

logger = logging.getLogger("comptine")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"Cannot JSON-serialise {type(obj).__name__}")


def _emit(out: Any, *, text: bool) -> None:
    if text and not isinstance(out, str):
        print(json.dumps(out, indent=2, ensure_ascii=False, default=_json_default))
    elif isinstance(out, str):
        print(out)
    else:
        print(json.dumps(out, ensure_ascii=False, default=_json_default))


@dataclass
class CommandContext:
    args: argparse.Namespace
    text: bool


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comptine", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", type=Path, help="Path to config.json (defaults to user config dir)."
    )
    parser.add_argument(
        "--env",
        choices=[e.value for e in Environment],
        help="Override the environment (sandbox or prod).",
    )
    parser.add_argument(
        "--text", action="store_true", help="Human-readable output instead of JSON."
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase log verbosity."
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    # auth check
    p_auth = sub.add_parser("auth", help="Authentication helpers")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("check", help="Fetch a token and print metadata.")

    # employeur verify
    p_emp = sub.add_parser("employeur", help="Employer operations")
    emp_sub = p_emp.add_subparsers(dest="employeur_cmd", required=True)
    p_ev = emp_sub.add_parser("verify", help="Verify a private employer's account.")
    p_ev.add_argument("--employeur", required=True, help="Handle from config.")

    # salarie verify
    p_sal = sub.add_parser("salarie", help="Worker operations")
    sal_sub = p_sal.add_subparsers(dest="salarie_cmd", required=True)
    p_sv = sal_sub.add_parser("verify", help="Verify a worker's Pajemploi account.")
    p_sv.add_argument("--salarie", required=True, help="Handle from config.salaries")

    # enfants verify
    p_enf = sub.add_parser("enfants", help="Children operations")
    enf_sub = p_enf.add_subparsers(dest="enfants_cmd", required=True)
    p_env = enf_sub.add_parser(
        "verify", help="Check which children open the right to the CMG (PAJE022)."
    )
    p_env.add_argument("--employeur", required=True, help="Handle from config.")

    # mandat register / cancel
    p_man = sub.add_parser("mandat", help="Mandate operations")
    man_sub = p_man.add_subparsers(dest="mandat_cmd", required=True)
    p_mr = man_sub.add_parser("register", help="Register a mandate (PAJE030).")
    p_mr.add_argument("--employeur", required=True)
    p_mc = man_sub.add_parser("cancel", help="Cancel a mandate (PAJE031).")
    p_mc.add_argument("--employeur", required=True)

    # estimate / declare
    p_est = sub.add_parser("estimate", help="Estimate cotisations (no side effects).")
    p_est.add_argument("--salarie", required=True)
    p_est.add_argument("--month", required=True, help="YYYY-MM")
    p_est.add_argument(
        "--hours-source",
        default=HoursSource.MENSUALISATION.value,
        choices=[s.value for s in HoursSource],
        help=(
            "Where nbHeures comes from: 'mensualisation' (contract hours/month from the "
            "sheet's reference block, what every pay slip so far carries) or 'sheet' "
            "(the 'Heures effectives' column, i.e. the real hours of the month)."
        ),
    )
    p_est.add_argument(
        "--age-band",
        default="2",
        choices=["1", "2", "3"],
        help='Youngest child age: "1" (0-3 yrs), "2" (3-6 yrs), "3" (>6 yrs). Default: "2".',
    )

    p_decl = sub.add_parser("declare", help="Submit a monthly declaration.")
    p_decl.add_argument("--salarie", required=True)
    p_decl.add_argument("--month", required=True, help="YYYY-MM")
    p_decl.add_argument(
        "--hours-source",
        default=HoursSource.MENSUALISATION.value,
        choices=[s.value for s in HoursSource],
        help=(
            "Where nbHeures comes from: 'mensualisation' (contract hours/month from the "
            "sheet's reference block, what every pay slip so far carries) or 'sheet' "
            "(the 'Heures effectives' column, i.e. the real hours of the month)."
        ),
    )
    p_decl.add_argument(
        "--preview",
        action="store_true",
        help="Print the exact body that would be POSTed (no network call).",
    )
    p_decl.add_argument("--dry-run", action="store_true", help="Run estimer only (no persistence).")
    p_decl.add_argument(
        "--predeclare-only",
        action="store_true",
        help="Stop after predeclarer; do not call declarer.",
    )
    p_decl.add_argument(
        "--no-write-back",
        action="store_true",
        help="Do not write the declaration id back to the sheet.",
    )
    p_decl.add_argument(
        "--skip-window-check",
        action="store_true",
        help="Bypass the local 'declarative window opens on the 25th' guard.",
    )

    sub.add_parser("discover", help="Run the sandbox endpoint probe (legacy).")

    return parser


def _resolve_employer(handle: str, cfg: Config) -> ParticulierEmployeur:
    return cfg.particuliers_employeurs[handle]


def _resolve_salarie(handle: str, cfg: Config) -> Salarie:
    return cfg.salaries[handle]


def _need(value: Any, label: str) -> Any:
    if value is None:
        raise SystemExit(f"Configuration error: missing {label} in config.json")
    return value


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def cmd_auth_check(ctx: CommandContext, cfg: Config) -> int:
    with ApiClient.from_config(cfg) as api:
        token = api._token_cache.get()
        _emit(
            {
                "env": cfg.env.value,
                "base_url": cfg.env.api_base,
                "scope": cfg.oauth.scope,
                "token_prefix": token[:12],
                "ok": True,
            },
            text=ctx.text,
        )
    return 0


def cmd_employeur_verify(ctx: CommandContext, cfg: Config) -> int:
    e = _resolve_employer(ctx.args.employeur, cfg)
    pn = _need(e.n_employeur_paje, "particuliers_employeurs[].n_employeur_paje")
    dob = _need(e.date_naissance, "particuliers_employeurs[].date_naissance")
    with ApiClient.from_config(cfg) as api:
        out = Employeurs(api).verify(pajemploi_number=pn, date_of_birth=dob)
    _emit(out, text=ctx.text)
    return 0


def cmd_salarie_verify(ctx: CommandContext, cfg: Config) -> int:
    s = _resolve_salarie(ctx.args.salarie, cfg)
    with ApiClient.from_config(cfg) as api:
        out = Salaries(api).verify(
            pajemploi_number=s.n_salarie_paje,
            nir=s.nir,
            date_of_birth=s.date_naissance,
        )
    _emit(out, text=ctx.text)
    return 0


def cmd_enfants_verify(ctx: CommandContext, cfg: Config) -> int:
    e = _resolve_employer(ctx.args.employeur, cfg)
    pn = _need(e.n_employeur_paje, "particuliers_employeurs[].n_employeur_paje")
    dob = _need(e.date_naissance, "particuliers_employeurs[].date_naissance")
    if not e.enfants:
        raise SystemExit(
            f"Configuration error: no children under particuliers_employeurs.{ctx.args.employeur}"
        )
    with ApiClient.from_config(cfg) as api:
        enfants = Enfants(api)
        raw = enfants.verify(
            employer_pajemploi_number=pn, employer_date_of_birth=dob, enfants=e.enfants
        )
        opening = enfants.opening_the_right(
            employer_pajemploi_number=pn, employer_date_of_birth=dob, enfants=e.enfants
        )
    _emit(
        {
            "raw": raw,
            "ouvrent_droit": [f"{c.prenom} {c.nom}" for c in opening],
            "hint": "Only these belong in inputDeclEnfant "
            "(config.particuliers_employeurs[].enfants).",
        },
        text=ctx.text,
    )
    return 0


def cmd_mandat_register(ctx: CommandContext, cfg: Config) -> int:
    e = _resolve_employer(ctx.args.employeur, cfg)
    pn = _need(e.n_employeur_paje, "particuliers_employeurs[].n_employeur_paje")
    dob = _need(e.date_naissance, "particuliers_employeurs[].date_naissance")
    with ApiClient.from_config(cfg) as api:
        out = Mandats(api).register(employer_pajemploi_number=pn, employer_date_of_birth=dob)
    _emit(out, text=ctx.text)
    return 0


def cmd_mandat_cancel(ctx: CommandContext, cfg: Config) -> int:
    e = _resolve_employer(ctx.args.employeur, cfg)
    pn = _need(e.n_employeur_paje, "particuliers_employeurs[].n_employeur_paje")
    dob = _need(e.date_naissance, "particuliers_employeurs[].date_naissance")
    with ApiClient.from_config(cfg) as api:
        out = Mandats(api).cancel(employer_pajemploi_number=pn, employer_date_of_birth=dob)
    _emit(out, text=ctx.text)
    return 0


def _read_month_and_employee(
    ctx: CommandContext, cfg: Config
) -> tuple[Any, ParticulierEmployeur, Employee, Salarie]:
    s = _resolve_salarie(ctx.args.salarie, cfg)
    e = _resolve_employer(s.employeur, cfg)
    sheet = MonthlySheetReader.from_config(cfg, ctx.args.salarie)
    hours_source = HoursSource(getattr(ctx.args, "hours_source", HoursSource.MENSUALISATION))
    month = sheet.read_month(ctx.args.month, hours_source=hours_source)
    if month.net_salary is None:
        raise SystemExit(
            f"Sheet row for {ctx.args.month} has no 'Salaire net' value — fill that column first."
        )
    employee = Employee(
        pajemploi_number=s.n_salarie_paje,
        last_name=s.nom or "",
        first_name=s.prenom or "",
        nir=s.nir,
    )
    return month, e, employee, s


def cmd_estimate(ctx: CommandContext, cfg: Config) -> int:
    month, employer, _employee, _s = _read_month_and_employee(ctx, cfg)
    body = to_estimation_ged(
        month,
        employer_postal_code=employer.code_postal,
        youngest_child_age_band=ctx.args.age_band,
    )
    with ApiClient.from_config(cfg) as api:
        out = Estimer(api).ged(body)
    _emit({"payload": body, "estimation": out}, text=ctx.text)
    return 0


def cmd_declare(ctx: CommandContext, cfg: Config) -> int:
    month, employer, employee, _s = _read_month_and_employee(ctx, cfg)
    pn = _need(employer.n_employeur_paje, "particuliers_employeurs[].n_employeur_paje")
    dob = _need(employer.date_naissance, "particuliers_employeurs[].date_naissance")

    if ctx.args.preview:
        warnings: list[str] = []
        if employee.pajemploi_number is None and not employee.nir:
            employee = employee.model_copy(update={"nir": "________________"[:15]})
            warnings.append("Employee NIR / Pajemploi number missing — placeholder used.")
        body = to_declaration_ged(
            month, employee=employee, children=children_to_wire(employer.enfants)
        )
        _emit(
            {
                "preview": True,
                "warnings": warnings,
                "endpoint": (
                    f"POST {cfg.env.api_base}/tiersdecl/v1/paje/employeurs/"
                    f"{pn}/ged/declarer?dtNaissanceEmployeur={dob.isoformat()}"
                ),
                "body": body,
                # Requested, not guaranteed: write-back needs the "N° volet"
                # header in the sheet and degrades to a warning without it.
                "write_back_requested": not ctx.args.no_write_back,
            },
            text=ctx.text,
        )
        return 0

    with ApiClient.from_config(cfg) as api:
        if ctx.args.dry_run:
            body = to_estimation_ged(month, employer_postal_code=employer.code_postal)
            out = Estimer(api).ged(body)
            _emit({"dry_run": True, "payload": body, "estimation": out}, text=ctx.text)
            return 0

        # Fail fast before the window opens (the 25th of the employment month);
        # the server would otherwise reject with ER_API_DECLA_0000.
        if not ctx.args.skip_window_check:
            try:
                check_declaration_window(month.period_start, today=date.today())
            except DeclarationWindowError as e:
                print(f"Declaration window not open: {e}", file=sys.stderr)
                return 2

        body = to_declaration_ged(
            month, employee=employee, children=children_to_wire(employer.enfants)
        )
        pre = Predeclarer(api).ged(
            employer_pajemploi_number=pn, employer_date_of_birth=dob, body=body
        )
        if ctx.args.predeclare_only:
            _emit({"predeclared": pre, "payload": body}, text=ctx.text)
            return 0

        final = Declarer(api).ged(
            employer_pajemploi_number=pn, employer_date_of_birth=dob, body=body
        )

    decl_id = final.get("referenceDocumentaire") if isinstance(final, dict) else None
    write_back_warning: str | None = None
    if not ctx.args.no_write_back and decl_id:
        sheet = MonthlySheetReader.from_config(cfg, ctx.args.salarie)
        try:
            sheet.write_back(
                ctx.args.month,
                declaration_id=str(decl_id),
                declared_at=date.today(),
                net_paid=month.net_salary,
            )
        except (WriteBackUnavailable, KeyError, RuntimeError) as e:
            # The declaration is filed; a sheet problem must not look like a failure.
            write_back_warning = str(e)
            logger.warning("Write-back skipped: %s", e)
    result: dict[str, Any] = {"payload": body, "declaration": final}
    if write_back_warning:
        result["write_back_warning"] = write_back_warning
    _emit(result, text=ctx.text)
    return 0


def cmd_discover(ctx: CommandContext, cfg: Config) -> int:
    script = Path(__file__).parent.parent.parent / "scripts" / "probe_endpoints.py"
    return subprocess.call([sys.executable, str(script)])


COMMANDS: dict[tuple[str, ...], Callable[[CommandContext, Config], int]] = {
    ("auth", "check"): cmd_auth_check,
    ("employeur", "verify"): cmd_employeur_verify,
    ("salarie", "verify"): cmd_salarie_verify,
    ("enfants", "verify"): cmd_enfants_verify,
    ("mandat", "register"): cmd_mandat_register,
    ("mandat", "cancel"): cmd_mandat_cancel,
    ("estimate",): cmd_estimate,
    ("declare",): cmd_declare,
    ("discover",): cmd_discover,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING - 10 * min(args.verbose, 2),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    key: tuple[str, ...]
    match args.cmd:
        case "auth":
            key = ("auth", args.auth_cmd)
        case "employeur":
            key = ("employeur", args.employeur_cmd)
        case "salarie":
            key = ("salarie", args.salarie_cmd)
        case "enfants":
            key = ("enfants", args.enfants_cmd)
        case "mandat":
            key = ("mandat", args.mandat_cmd)
        case _:
            key = (args.cmd,)

    cmd = COMMANDS.get(key)
    if cmd is None:
        print(f"Unknown command: {key}", file=sys.stderr)
        return 2

    ctx = CommandContext(args=args, text=args.text)
    try:
        cfg = load_config(
            path=args.config,
            env_override=Environment(args.env) if args.env else None,
        )
    except FileNotFoundError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2

    try:
        return cmd(ctx, cfg)
    except ValueError as e:
        print(f"Invalid declaration input: {e}", file=sys.stderr)
        return 2
    except ApiError as e:
        print(json.dumps({"error": e.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
