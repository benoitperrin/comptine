# TDPAJE business rules

Source: Urssaf "Support Métier et API TDPAJE" FAQ
(<https://portailapi.urssaf.fr/fr/centre-assistance/article/api-tdpaje-faq>), read 2026-06-02.

## Declaration window

- The declarative period for a given employment month **opens on the 25th of that
  month** and nominally runs **to the 5th of the following month**.
- **Declaring before the 25th of the employment month is rejected** with:
  - `ER_API_DECLA_0000` — "Pour la période d'emploi indiquée, votre espace déclaratif
    sera ouvert à partir du 25 du mois d'emploi".
- **There is no hard deadline.** Declaring after the 5th (e.g. the 6th) is *not* blocked;
  late declarations go through.

## Hours rounding

- `nbHeures` (heures normales) follows standard half-up rounding:
  - decimal `< 0.5` → round **down**;
  - decimal `>= 0.5` → round **up**.
- If a declaration contains only normal hours, `mntSalaireHoraireNet`
  (`Salaire horaire net`) is **not** mandatory.
- Our `mapper._hours_int` uses `ROUND_HALF_UP`, which matches this exactly
  (151.4 → 151, 151.5 → 152).

## Disability / AEEH handling (resolved)

Per Urssaf (Gilles, JIRA ASE-6924, 2026-06-02): AEEH / disability information is
**managed entirely inside the Pajemploi back-office (SI PAJE)** and is **not**
transmitted through the TD API. The declaration payload therefore carries no
disability field — `indcEnfantPlusJeuneAgarder` (age band 1/2/3) is the only
child-related input, and the CMG-handicap computation is applied server-side from
the data Pajemploi already holds.

Consequence for this connector: **nothing to add** to `InputDeclarationGed`.
We deliberately do not test an AEEH case in sandbox (it would only exercise the
Urssaf-side calculation, not our code); the real disability case will be
validated directly in production.

## Sandbox employer activation (resolved → pending re-test)

The FAQ does **not** document the sandbox "Coordonnées bancaires invalides" state.
Our two assigned sandbox employers (`Y4189828980000`, `Y4189890980003`) initially
returned `employeurAutoriseOk=false` / `etatCompteEnLigne="Coordonnées bancaires
invalides"`, which blocked `mandat register` (`ER_API_030_0000`) and therefore
`predeclarer`/`declarer` (`ER_API_MANDAT_VERIFICATION`).

Per the OpenAPI spec, `employeurAutoriseOk` requires *valid bank details*, so the
behaviour is internally consistent. Urssaf (Gilles, 2026-06-02) confirmed a
**sandbox-side update of the two employer accounts effective 2026-06-03** to clear
the blocker; the full flow (`mandat → associer → predeclarer → declarer`) is to be
re-tested then and the result confirmed back on ASE-6924.

**Resolved (observed 2026-07-13).** Both employers now answer
`employeurAutoriseOk=true` / `etatCompteEnLigne="Actif"`. The Urssaf never posted
the fix on ASE-6924 (last comment there is still Gilles' "en cours d'analyse" of
2026-06-16), so it landed silently somewhere between 2026-06-10 and 2026-07-13.
`mandat register` and `salarie/associer` both succeed.

## `InputDeclarationGed` is nested, `InputEstimationGed` is flat

The single biggest trap in this API, and the reason `predeclarer`/`declarer`
returned **500 `ERREUR_TECHNIQUE`** on every attempt once the mandate cleared:

| | shape |
|---|---|
| `InputEstimationGed` (`/ged/estimer`) | **flat** — `nbHeures`, `mntSalaireNetMensuel`… at the root |
| `InputDeclarationGed` (`/ged/predeclarer`, `/ged/declarer`) | **nested** — the same fields live under `inputDeclCommun` |

Sending the declaration fields at the root leaves `inputDeclCommun` null
server-side and the backend throws. Only `cdModeCalcul` is `required` at the root.

## A GED declaration needs at least one `inputDeclEnfant`

Not marked `required` in the spec, but **mandatory in practice**: without it,
`/ged/predeclarer` answers 500 `ERREUR_TECHNIQUE`. With one child it returns 200.
This is coherent — *garde d'enfants à domicile* always concerns a child, and the
CMG is computed per child. The connector guards against a child-less declaration
client-side rather than tripping the server error.

`InputEnfant` names must be **unaccented uppercase** (`pattern: [A-Z'\-\s]*`), so
"Grégoire" must be sent as "GREGOIRE" (see `children_to_wire`).

## End-to-end declaration proven in sandbox (2026-07-13)

`auth → employeur verify → mandat register → salarie associer → ged/predeclarer
→ ged/declarer` runs clean against `sandbox1` + `durand_marie` + 1 child, and
`declarer` returns a `referenceDocumentaire` (`2026194V05430`). The worker's
`etatCompteEnLigne` is still `"Inactif"` and that turns out **not** to block the
declaration — no further Urssaf action is needed to finish sandbox validation.

## Production: mandate first, then everything else (2026-09-02)

`mandat register` (PAJE030) is the gate to every write and to PAJE022. Until it is
called, `enfants/verifier`, `predeclarer` and `declarer` all answer:

```json
[{"code":"ER_API_MANDAT_VERIFICATION","message":"Mandat de tierce déclaration absent"}]
```

Two things to note:

- **Business errors come back as a JSON *array*.** Reading only the object shape
  turned the message above into a bare `"400"` with no code — fixed in
  `client._make_api_error`. The gateway's correlation header is `x-correlationid`
  (no dash before `id`).
- `POST /mandats` answers **200 with an empty body**. There is no list endpoint,
  so the only way to confirm a mandate exists is to call something that needs it.

Registered for `Y1234567890123` on 2026-09-02; PAJE022 then answered immediately.

## PAJE022 in production settles the "which children" question

Submitting LEA DUPONT (born 2021-02-18) alone returns
`reponseStatutEnfant: 1` — found, opening the right. This confirms the reading held
since 2026-07-13 on sandbox evidence: `inputDeclEnfant` carries the children who
**open the right** (CMG: under 6, or under 20 with AEEH), not the whole household.
Cyprien (7, no AEEH) is correctly absent.

## Declared hours are the mensualisation, not the month's real hours

The pay slips filed for April to August 2026 all carry **152 h** — the contract's
35 h/week × 52/12 = 151,67, rounded. The tracking sheet's "Heures effectives"
column computes the *real* hours of each month (147 to 161 depending on the
calendar), which is a different figure and a different convention.

It is not cosmetic: on August 2026, `/ged/estimer` answers
`mntCotiEmplAcharge` **1 172,48 €** / `mntExoEmployeur` **274,00 €** at 152 h,
against **1 178,48 €** / **268,00 €** at 148 h.

The connector therefore defaults to `--hours-source mensualisation`, reading
"Heures par mois" from the sheet's reference block. `--hours-source sheet` keeps
the old behaviour.
