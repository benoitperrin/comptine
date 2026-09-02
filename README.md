# comptine

*La déclaration Pajemploi, chaque mois, à l'identique.*

Client Python et CLI pour l'API Urssaf **Tierce Déclaration Pajemploi** (TDPAJE) : déclarer
la rémunération mensuelle d'une garde d'enfants à domicile pour le compte d'un particulier
employeur qui vous a donné mandat.

Le nom vient de ce qu'une comptine est une formule courte qu'on répète à l'identique, et qui
sert à compter.

Projet indépendant, sans aucun lien avec l'Urssaf ni avec l'Acoss. La documentation technique
du code est en anglais, ce README et le modèle de mandat sont en français.

## Avant tout : qui peut s'en servir

L'API TDPAJE n'est pas ouverte aux particuliers. Pour obtenir des identifiants, il faut être
une **personne morale référencée** par l'Urssaf, et fournir un dossier : extrait K ou Kbis (ou
carte du répertoire des métiers, ou parution au Journal officiel pour une association, ou le
texte instituant l'établissement public), une attestation de régularité fiscale et une
attestation de compte à jour des cotisations sociales.

La demande se fait sur https://portailapi.urssaf.fr puis par courriel à
`contact.tiercedeclaration@urssaf.fr`, en mettant `[TDPAJE]` dans l'objet. Comptez plusieurs
semaines : dans notre cas, souscription le 19/02/2026, sandbox le 28/05, production le 29/07.

Ce dépôt ne contient évidemment aucun identifiant, et n'en distribue pas : les règles de bon
usage de l'Acoss imposent au partenaire « la non-divulgation à des tiers des codes d'accès ».
Il ne contient pas non plus la spécification OpenAPI, qui est remise sous licence par l'Urssaf
et n'est pas publique. Ce qu'il contient, c'est un client qui marche et la liste des pièges
qu'on s'est pris.

## Ce que ça fait

- Authentification OAuth2 `client_credentials` sur la passerelle Urssaf (sandbox
  `api-edi.urssaf.fr`, production `api.urssaf.fr`), portée `tiersdecl.paje`.
- Mandat de tierce déclaration : enregistrement (PAJE030) et annulation (PAJE031).
- Vérification d'un particulier employeur, d'un salarié, et des enfants ouvrant droit au CMG
  (PAJE022).
- Estimation des cotisations, prédéclaration, déclaration, pour une activité « garde
  d'enfants à domicile » (GED).
- Lecture des entrées mensuelles depuis un Google Sheet de suivi, avec résolution des colonnes
  par en-tête.
- Sortie JSON par défaut et codes de sortie déterministes, pour un pilotage par script ou par
  un agent.

## Les pièges de l'API, qui justifient à eux seuls ce dépôt

1. **`InputDeclarationGed` est imbriqué, `InputEstimationGed` est plat.** Les mêmes champs
   (`dtDebutPeriode`, `nbHeures`, `mntSalaireNetMensuel`…) vivent à la racine pour l'estimation
   et sous `inputDeclCommun` pour la déclaration. Les envoyer à la racine laisse l'objet nul
   côté serveur et rend un **500 `ERREUR_TECHNIQUE`** qui ne dit rien.
2. **`inputDeclEnfant` est obligatoire en pratique**, alors qu'il n'est pas marqué `required` :
   sans lui, 500 ; avec un enfant, 200. Les noms doivent être en majuscules non accentuées.
3. **Les erreurs métier arrivent en tableau JSON**, pas en objet :
   `[{"code": "ER_API_MANDAT_VERIFICATION", "message": "..."}]`. Un client qui ne lit que la
   forme objet affiche un « 400 » nu. L'en-tête de corrélation est `x-correlationid`, sans
   tiret avant `id`.
4. **Tout passe par le mandat.** Tant que PAJE030 n'a pas été appelé, `enfants/verifier`,
   `predeclarer` et `declarer` répondent tous `ER_API_MANDAT_VERIFICATION`. PAJE030 répond
   **200 avec un corps vide**, et il n'existe aucun endpoint de liste : la seule façon de
   savoir qu'un mandat existe est d'appeler quelque chose qui l'exige.
5. **La fenêtre déclarative ouvre le 25 du mois d'emploi.** Avant, `ER_API_DECLA_0000`. Il n'y
   a pas de date butoir : les déclarations tardives passent.
6. **Un POST sans corps n'a pas de `Content-Type`**, et Spring le rejette en 415 ou 400 avant
   toute règle métier. Envoyez `{}`.
7. **Les heures déclarées sont un choix de convention**, pas une donnée. Pour un CDI
   mensualisé, le bulletin porte la mensualisation (35 h/semaine × 52/12 → 152), pas les heures
   réelles du mois, qui varient de 147 à 161 selon le calendrier. L'écart se voit dans les
   cotisations.

Le détail est dans `docs/api/business-rules.md`, la reconnaissance initiale dans
`docs/api/discovery-notes.md`.

## Installation

```bash
uv pip install -e ".[dev]"
```

## Configuration

`~/.config/comptine/config.json`, en mode `600` :

```json
{
  "env": "sandbox",
  "oauth": { "client_id": "...", "client_secret": "...", "scope": "tiersdecl.paje" },
  "tiers_declarant": { "siret": "...", "raison_sociale": "..." },
  "particuliers_employeurs": {
    "client1": {
      "n_employeur_paje": "Y1234567890123",
      "nom": "DUPONT", "prenom": "CLAIRE",
      "date_naissance": "1985-03-10",
      "code_postal": "35700",
      "enfants": [{ "nom": "DUPONT", "prenom": "LEA", "date_naissance": "2021-02-18" }]
    }
  },
  "salaries": {
    "nounou": { "employeur": "client1", "nom": "MARTIN", "prenom": "SOPHIE", "nir": "..." }
  }
}
```

Un fichier `config.prod.json` séparé sert la production, sélectionnée par `--env prod`.

## Usage

```bash
comptine auth check
comptine employeur verify --employeur client1
comptine mandat register --employeur client1          # PAJE030, écriture réelle
comptine enfants verify --employeur client1           # qui ouvre droit au CMG
comptine estimate --salarie nounou --month 2026-09
comptine declare --salarie nounou --month 2026-09 --preview
comptine declare --salarie nounou --month 2026-09 --predeclare-only
comptine declare --salarie nounou --month 2026-09
```

`--hours-source mensualisation` (défaut) prend les heures du contrat ; `--hours-source sheet`
prend la colonne « Heures effectives » du tableur.

Une déclaration engage des cotisations réelles et ne se modifie en ligne que pendant un mois.
Si vous branchez ce client sur un agent, gardez `declarer` derrière une confirmation humaine.

## Depuis un assistant : MCP, plugin, connecteur

Le dépôt embarque un serveur MCP, `comptine-mcp`, qui expose huit outils : état,
vérifications de l'employeur, du salarié et des enfants ouvrant droit, estimation,
aperçu du corps, prédéclaration et déclaration.

Trois verrous, parce qu'une déclaration engage des cotisations réelles :

- **aucun outil n'accepte d'employeur en paramètre.** Il est résolu côté serveur, à
  partir de la configuration en local ou du compte appelant en mode hébergé. Un test
  d'invariant relit le code source et échoue si un paramètre d'employeur réapparaît ;
- **`declarer` exige le jeton rendu par `predeclarer`**, qui est l'empreinte du corps
  exact. Si quoi que ce soit a changé depuis, la déclaration est refusée ;
- **le mandat n'est pas exposé.** C'est un acte juridique qui suit un mandat signé,
  posé à la main.

Chaque appel est journalisé en JSON-lines (`COMPTINE_AUDIT_LOG` pour choisir où).

### En local

Comme plugin Claude Code ou Cowork, le dépôt est lui-même le plugin :

```bash
claude --plugin-dir /chemin/vers/comptine
```

Pour tout autre client MCP, la commande est `comptine-mcp` en stdio. Chez Codex :

```bash
codex mcp add comptine -- uvx --from "comptine[mcp] @ git+https://github.com/benoitperrin/comptine" comptine-mcp
```

Avec plusieurs employeurs en configuration, `COMPTINE_EMPLOYEUR` désigne celui sur
lequel le serveur agit.

### Hébergé, pour donner un accès à quelqu'un

Le serveur se sert aussi en HTTP. La personne n'installe rien : elle ajoute l'URL comme
connecteur personnalisé dans Claude, sur n'importe quel plan, depuis le web, Desktop,
Cowork ou mobile.

```bash
comptine-mcp --nouveau-compte "Claire Dupont" --employeur claire   # rend un jeton, une fois
comptine-mcp --http --port 8787
```

Le jeton n'est jamais stocké : la configuration ne garde que son empreinte SHA-256, et
un compte est lié à un employeur et un seul. Sans jeton valide, le serveur refuse —
il échoue fermé, il ne retombe jamais sur un employeur par défaut. `peut_declarer`
vaut `false` par défaut : un compte lit et estime, il ne dépose pas.

⚠️ Héberger ce service, c'est déclarer pour autrui : le mandat écrit préalable est
exigé par l'article R133-43 du code de la sécurité sociale, et l'Urssaf renvoie vers le
tiers déclarant toute contestation.

## Vous n'avez pas d'accès et vous voulez quand même passer par l'API

Sépharée SAS est référencée comme tiers déclarant et peut déposer les déclarations d'un
particulier employeur qui lui donne mandat. C'est artisanal et volontairement limité : pas de
formulaire, pas d'inscription automatique, quelques dossiers à la fois, à titre gratuit, et
sans engagement de délai.

Écrivez à `comptine@sepharee.com`. Il faut :

- être particulier employeur relevant de Pajemploi, pour une garde d'enfants à domicile ;
- signer le mandat de `docs/mandat-type.md` et joindre une pièce d'identité. Ce n'est pas une
  formalité de confort : l'article R133-43 du code de la sécurité sociale impose au tiers
  déclarant de détenir les éléments attestant d'une relation contractuelle préalable, et
  l'organisme peut les demander. Poser un mandat ne demande techniquement que votre numéro
  Pajemploi et votre date de naissance, deux informations que votre salariée et vos proches
  connaissent : la signature est ce qui vous protège ;
- savoir que la déclaration reste la vôtre. L'article L133-11 II rappelle que le recours à un
  tiers déclarant s'applique « sans préjudice des règles applicables en matière de contrôle, de
  recouvrement et de sanctions » à votre égard.

Vous pouvez révoquer le mandat à tout moment, par un mot à la même adresse.

## Licence

MIT, voir `LICENSE`.
