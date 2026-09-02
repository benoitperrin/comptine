# Sandbox API discovery notes

State as of **2026-06-01**.

## What works

- OAuth2 `client_credentials` against `https://api-edi.urssaf.fr/api/oauth/v1/token` returns a usable bearer token in <300 ms. Sample response:

  ```json
  {
    "access_token": "kU1rMUWW27CceuVu_uAZOcn2ufQv1D0Dl_LhZlqW6Lg",
    "token_type": "Bearer",
    "expires_in": 3600
  }
  ```

  Tokens are opaque (not JWTs).

- `GET /api/oauth/v1/.well-known/openid-configuration` confirms:
  - Issuer: `https://login.urssaf.fr`
  - Authorize / token / user_info / introspection / revocation / JWKS endpoints all under `/api/oauth/v1/…`.
  - Grant types supported: `client_credentials`, `authorization_code`, `implicit`, `refresh_token`, `password`, plus Urssaf-specific `trust_fc` and `trust_ne`.
  - Token endpoint auth method: `client_secret_post`.

## What we don't know yet

The Urssaf API gateway (Gravitee) routes each API by **context-path**. The TDPAJE context-path is **not derivable** from credentials, the partner emails, or the openid-configuration. Without the OpenAPI specification we cannot guess it.

### Patterns tested and rejected (HTTP 404 on `GET`):

```
/td-paje              /tdpaje              /tierce-declaration-paje
/tierce-declaration-pajemploi              /paje                /paje/v1
/api/td-paje          /api/tdpaje          /api/tierce-declaration-paje
/api/tierce-declaration-pajemploi          /api/paje            /api/paje/v1
/sandbox/tdpaje       /tdpaje/sandbox      /tdpaje/sb
/edi-paje             /api/edi-paje        /api/echange-edi-paje
/paje-tierce-declaration                   /api/paje-tierce-declaration
/declarant-paje       /tiers-declarant-paje
... and ~250 more variants (see scripts/probe_endpoints.py)
```

Every path returned HTML 404 from the gateway — no `Allow` header, no JSON body. The same gateway *does* serve the OAuth endpoints under `/api/oauth/v1/…`, so the host is reachable; it's the API-specific routing that's hidden.

The portal at `https://portailapi.urssaf.fr/fr/catalogue-api/prd/td-paje` is JavaScript-rendered behind a TSPD (T-Systems) WAF that rejects non-JS clients with HTTP/1.1 `Connection reset`. Loading it with Playwright is the next step (was blocked tonight because the Rosalie host that hosts our Playwright service was unreachable).

## Categories known from data.gouv.fr and Cobham-Solutions

These are the functional categories the API exposes — endpoints are not listed publicly.

| Category | Likely operations |
|---|---|
| Mandats | CRUD on the mandate between tiers déclarant and particulier-employeur |
| Employeurs | Verify a private employer (NIR + names → Pajemploi account) |
| Salariés | Verify a domestic worker |
| Associer | Link a worker to an employer |
| Estimer | Simulate contributions for a candidate declaration (no side effects) |
| Prédéclarer | Submit a declaration in draft state |
| Déclarer | Confirm a declaration; this triggers cotisations billing |
| Bulletin | Download the official pay-slip PDF |
| Enfants éligibles | CMG (Complément de libre choix du Mode de Garde) child eligibility |

## Reference: real shape of a declaration's inputs and outputs

Captured from a real pay slip for the period **2025-12-01 → 2025-12-31** (volet social `2026001X00000`, employer `Y1234567890123`, salarié `00000000000000`).

### Inputs ("Éléments pris en compte")

- Nombre d'heures effectives: 80
- Nombre d'heures normales: 80
- Nombre d'heures supplémentaires 25 %: 0
- Nombre d'heures supplémentaires 50 %: 0
- Nombre de jours de congés payés: 3,0
- Salaire brut: 1046,06 €
- Acompte: 0
- Indemnités kilométriques: 0
- Frais de transport: 50,75 €
- Date de paiement du salaire: 31/12/2025
- Option de calcul des cotisations: Salaire réel

### Computed contributions (per line)

For each line: base, employee rate %, employee amount, employer rate %, employer amount.

```
CSG + RDS non déductible    1027,75   2,900%  29,81    —
CSG déductible              1027,75   6,800%  69,89    —
Vieillesse plafonnée        1046,06   0,400%   4,18    —
Maladie                     1046,06     —      —      13,000%  135,99
Vieillesse déplafonnée      1046,06   6,900%  72,18    8,550%   89,44
(ligne sans libellé)        1046,06     —      —       2,020%   21,13
Allocs familiales           1046,06     —      —       5,250%   54,92
Accident du travail         1046,06     —      —       2,180%   22,80
FNAL                        1046,06     —      —       0,100%    1,05
CSA                         1046,06     —      —       0,300%    3,14
Formation pro               1046,06     —      —       0,850%    8,89
Dialogue social             1046,06     —      —       0,016%    0,17
Santé travail               1046,06     —      —       2,700%    5,00 (plafonné)
Retraite compl.             1046,06   4,010%  41,95    6,010%   62,86
Prévoyance                  1046,06   1,040%  10,88    2,450%   25,63
Assurance chômage           1046,06     —      —       4,000%   41,84
                                            -------          -------
TOTAL                                       228,89          472,86
```

### Outputs

- Salaire net déclaré: 817,18 €
- Indemnités: 50,75 €
- Salaire net déclaré (y compris indemnités) = Net à payer avant l'impôt: 867,93 €
- Salaire net imposable (avec exo fiscale): 846,98 €
- PAS: taux 0,00 %, montant 0,00 €
- Net payé: 867,93 €
- Montant net social: 817,18 €
- Cumul imposable année fiscale 2025 au 28/05/2026: 12 687,25 €

### Identifier patterns

- **Volet social** (`declaration_id`): `YYYY` + `DDD` (day-of-year) + 1 letter + 5 digits.
  Examples: `2026001X00000` (Jan 1 2026), `2025332X65823` (Nov 28 2025), `2025069X98212` (Mar 10 2025).
  → The `declarer` API likely returns this verbatim.
- **N° Employeur**: `Y` + 12 digits. Stable across months for a given particulier-employeur.
- **N° Salarié**: 14 digits, left-padded with zeros.

## Next steps to unblock the endpoint map

1. **Log into the portal** at `https://portailapi.urssaf.fr/fr/catalogue-api/prd/td-paje` with a browser, open the `Tests` tab, intercept the OpenAPI fetch in DevTools (Network → XHR), save the JSON.
2. Or: file a Urssaf JIRA ticket asking for the OpenAPI/Swagger document (`docs/jira-ticket-draft.md`).
3. Update `PAYLOAD_FIELD_MAP` in `src/pajemploi/mapper.py` and the `PATH_*` constants in `src/pajemploi/api/*.py` accordingly. Tests should keep passing — only field names change.
4. Re-run `pajemploi inventory` to validate against the sandbox.
