"""The hosted MCP server's security model, under test.

One set of Acoss credentials reaches every employer who granted a mandate, so the
only thing standing between two households is the scoping code. These tests pin it
down: which employer a call acts on, who may act on which salarié, and the fact that
no tool anywhere accepts an employer as an argument.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date

import pytest

from comptine.config import (
    Compte,
    Config,
    Enfant,
    OAuthSettings,
    ParticulierEmployeur,
    Salarie,
    TiersDeclarant,
)
from comptine.mcp_server import (
    _PRINCIPAL,
    AccessDenied,
    allowed_salaries,
    confirmation_token,
    current_employer,
    current_salarie,
    resolve_account,
    token_digest,
)

TOKEN_A = "jeton-de-claire"
TOKEN_B = "jeton-de-paul"


def _config() -> Config:
    """Two households on the same server — the situation that must never leak."""
    return Config(
        oauth=OAuthSettings(client_id="id", client_secret="secret"),
        tiers_declarant=TiersDeclarant(siret="12345678900012"),
        particuliers_employeurs={
            "claire": ParticulierEmployeur(
                n_employeur_paje="Y1111111111111",
                nom="DUPONT",
                prenom="CLAIRE",
                date_naissance=date(1985, 3, 10),
                enfants=[Enfant(nom="DUPONT", prenom="LEA", date_naissance=date(2021, 2, 18))],
            ),
            "paul": ParticulierEmployeur(
                n_employeur_paje="Y2222222222222",
                nom="MARTIN",
                prenom="PAUL",
                date_naissance=date(1980, 7, 1),
            ),
        },
        salaries={
            "nounou_claire": Salarie(employeur="claire", nom="BERNARD", prenom="SOPHIE"),
            "nounou_paul": Salarie(employeur="paul", nom="ROUX", prenom="AMINA"),
            "seconde_de_claire": Salarie(employeur="claire", nom="LOPEZ", prenom="ANA"),
        },
        comptes={
            "claire": Compte(
                token_sha256=token_digest(TOKEN_A), employeur="claire", libelle="Claire"
            ),
            "paul": Compte(
                token_sha256=token_digest(TOKEN_B),
                employeur="paul",
                salaries=["nounou_paul"],
                libelle="Paul",
            ),
        },
    )


@pytest.fixture
def cfg() -> Config:
    return _config()


@pytest.fixture(autouse=True)
def _clear_principal():
    token = _PRINCIPAL.set(None)
    yield
    _PRINCIPAL.reset(token)


# --- identity ---------------------------------------------------------------------


def test_a_token_resolves_to_its_own_account(cfg: Config) -> None:
    assert resolve_account(cfg, TOKEN_A) is not None
    assert resolve_account(cfg, TOKEN_A).employeur == "claire"  # type: ignore[union-attr]
    assert resolve_account(cfg, TOKEN_B).employeur == "paul"  # type: ignore[union-attr]


@pytest.mark.parametrize("token", ["", None, "inconnu", TOKEN_A + "x", TOKEN_A.upper()])
def test_anything_but_the_exact_token_resolves_to_nobody(cfg: Config, token: str | None) -> None:
    assert resolve_account(cfg, token) is None


def test_the_token_itself_is_never_stored(cfg: Config) -> None:
    stored = {c.token_sha256 for c in cfg.comptes.values()}
    assert TOKEN_A not in stored
    assert all(len(digest) == 64 for digest in stored)


# --- employer scoping -------------------------------------------------------------


def test_the_employer_comes_from_the_account(cfg: Config) -> None:
    _PRINCIPAL.set(cfg.comptes["paul"])
    handle, employer = current_employer(cfg, hosted=True)
    assert handle == "paul"
    assert employer.n_employeur_paje == "Y2222222222222"


def test_hosted_mode_refuses_rather_than_defaulting(cfg: Config) -> None:
    """No account behind the call must deny it, never fall back on an employer."""
    with pytest.raises(AccessDenied, match="No account"):
        current_employer(cfg, hosted=True)


def test_local_mode_refuses_to_guess_between_several_employers(cfg: Config) -> None:
    with pytest.raises(AccessDenied, match="COMPTINE_EMPLOYEUR"):
        current_employer(cfg, hosted=False)


def test_local_mode_takes_the_only_employer_there_is(cfg: Config) -> None:
    cfg.particuliers_employeurs.pop("paul")
    handle, _ = current_employer(cfg, hosted=False)
    assert handle == "claire"


def test_local_mode_honours_the_environment_variable(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPTINE_EMPLOYEUR", "paul")
    handle, _ = current_employer(cfg, hosted=False)
    assert handle == "paul"


def test_an_account_pointing_at_an_unknown_employer_is_denied(cfg: Config) -> None:
    _PRINCIPAL.set(Compte(token_sha256="x" * 64, employeur="fantome"))
    with pytest.raises(AccessDenied, match="unknown employer"):
        current_employer(cfg, hosted=True)


# --- salarié scoping --------------------------------------------------------------


def test_a_salarie_of_another_household_is_refused(cfg: Config) -> None:
    _PRINCIPAL.set(cfg.comptes["claire"])
    with pytest.raises(AccessDenied, match="does not belong to your employer"):
        current_salarie(cfg, "nounou_paul", hosted=True)


def test_an_account_restricted_to_one_salarie_cannot_reach_the_others(cfg: Config) -> None:
    cfg.salaries["autre_de_paul"] = Salarie(employeur="paul", nom="NOIR", prenom="IVA")
    _PRINCIPAL.set(cfg.comptes["paul"])
    assert current_salarie(cfg, "nounou_paul", hosted=True).nom == "ROUX"
    with pytest.raises(AccessDenied, match="may not act on"):
        current_salarie(cfg, "autre_de_paul", hosted=True)


def test_an_empty_salarie_list_means_every_salarie_of_that_employer(cfg: Config) -> None:
    _PRINCIPAL.set(cfg.comptes["claire"])
    assert sorted(allowed_salaries(cfg, hosted=True)) == ["nounou_claire", "seconde_de_claire"]


def test_an_unknown_salarie_is_refused(cfg: Config) -> None:
    _PRINCIPAL.set(cfg.comptes["claire"])
    with pytest.raises(AccessDenied, match="Unknown salarié"):
        current_salarie(cfg, "personne", hosted=True)


# --- the invariant ----------------------------------------------------------------


def _tool_functions() -> list[ast.FunctionDef]:
    source = pathlib.Path(__file__).parent.parent / "src" / "comptine" / "mcp_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                tools.append(node)
    return tools


def test_the_tools_are_the_ones_we_meant_to_expose() -> None:
    """Neither mandate operation may ever appear here: a mandate follows a signed
    paper, so it stays a manual act."""
    names = {f.name for f in _tool_functions()}
    assert names == {
        "comptine_etat",
        "comptine_verifier_employeur",
        "comptine_verifier_salarie",
        "comptine_enfants_ouvrant_droit",
        "comptine_estimer",
        "comptine_apercu",
        "comptine_predeclarer",
        "comptine_declarer",
    }
    assert not any("mandat" in name for name in names)


def test_no_tool_takes_an_employer_argument() -> None:
    """The invariant the whole model rests on. If a tool ever accepts an employer,
    a caller can declare in someone else's household."""
    offenders = []
    for func in _tool_functions():
        args = func.args
        names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
        for name in names:
            if "employeur" in name.lower() or "employer" in name.lower() or "paje" in name.lower():
                offenders.append(f"{func.name}({name})")
    assert offenders == []


# --- confirmation token -----------------------------------------------------------


def test_the_confirmation_token_follows_the_body() -> None:
    body = {"inputDeclCommun": {"nbHeures": 152, "mntSalaireNetMensuel": 2000.0}}
    other = {"inputDeclCommun": {"nbHeures": 148, "mntSalaireNetMensuel": 2000.0}}
    assert confirmation_token(body) == confirmation_token(dict(body))
    assert confirmation_token(body) != confirmation_token(other)


def test_the_confirmation_token_ignores_key_order() -> None:
    a = {"cdModeCalcul": "R", "inputSp": {"nirSalarie": "1"}}
    b = {"inputSp": {"nirSalarie": "1"}, "cdModeCalcul": "R"}
    assert confirmation_token(a) == confirmation_token(b)
