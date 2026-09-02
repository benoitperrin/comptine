---
description: Déclarer le salaire mensuel d'une garde d'enfants à domicile à l'Urssaf via Pajemploi. À utiliser quand on parle de déclarer un mois, de la paie de la nounou, du CMG, des cotisations Pajemploi, ou quand on demande d'estimer ou de vérifier une déclaration.
---

# Déclarer un mois à Pajemploi

Une déclaration engage des cotisations réelles et ne se modifie en ligne que pendant
un mois. Elle se fait donc en trois temps, et le troisième n'appartient pas au modèle.

## La procédure

1. **`comptine_etat`** en premier, toujours. Il dit sur quel employeur le compte agit,
   quels salariés sont autorisés, et si la fenêtre du mois est ouverte.
2. **`comptine_apercu`** pour le mois visé. Aucun appel réseau : il montre le corps
   exact qui partirait. **Montrer ce corps à la personne**, en français, poste par
   poste : heures, salaire net, jours de congés payés, frais de transport, enfant
   rattaché.
3. **`comptine_predeclarer`** fait calculer la déclaration par l'Urssaf sans rien
   valider, et rend un `jeton_de_confirmation`. Présenter le résultat : cotisations,
   CMG, reste à charge.
4. **`comptine_declarer`** avec ce jeton, **et seulement après un accord explicite de
   la personne dans le tour de conversation en cours**. Un « oui » donné avant d'avoir
   vu les chiffres n'en est pas un.

Ne jamais enchaîner les quatre étapes d'un coup. S'arrêter après la prédéclaration et
attendre.

## Ce qu'il faut savoir

**La fenêtre s'ouvre le 25 du mois d'emploi.** Avant, l'Urssaf refuse. Il n'y a pas de
date butoir : une déclaration tardive passe. Donc, si on est le 12, on peut préparer et
estimer, pas déclarer.

**Les heures sont une convention, pas une mesure.** Pour un contrat mensualisé, on
déclare la mensualisation — 35 h par semaine donnent 152 h par mois — et non les heures
réellement travaillées, qui varient de 147 à 161 selon le calendrier. C'est le défaut
(`heures: "mensualisation"`). Les congés payés ne réduisent pas les heures : ils ont
leur propre case sur le bulletin. Ne passer à `heures: "sheet"` que si la personne le
demande explicitement, en lui disant que ça change les cotisations.

**Les enfants déclarés sont ceux qui ouvrent droit au CMG**, pas toute la fratrie :
moins de 6 ans, ou moins de 20 ans avec l'AEEH. `comptine_enfants_ouvrant_droit` le
fait confirmer par le SI Pajemploi.

**Le prélèvement à la source est retenu dans tous les cas.** Le montant à virer au
salarié est le « net payé » du bulletin, après impôt, que le service Pajemploi+ soit
activé ou non.

## Ce que cette skill ne fait pas

Le mandat de tierce déclaration ne s'enregistre pas ici. C'est un acte juridique qui
suit un mandat signé sur papier, et l'opérateur le pose à la main. Si quelqu'un demande
d'ajouter un employeur ou d'enregistrer un mandat, répondre que cela passe par
l'opérateur du service, et par lui seul.

## En cas d'erreur

`ER_API_DECLA_0000` : la fenêtre n'est pas ouverte, on est avant le 25.
`ER_API_MANDAT_VERIFICATION` : aucun mandat n'existe pour cet employeur.
Un jeton de confirmation refusé signifie que le corps a changé depuis la
prédéclaration. Refaire l'aperçu, relire, et redemander l'accord.
