# Limites et résultats attendus — effet présumé d'un modèle propriétaire

> **STATUT DE CE DOCUMENT — À LIRE AVANT TOUTE CITATION.**
> Ce document ne contient **aucune mesure**. Il énonce des **hypothèses
> raisonnées** sur le comportement attendu de HCode avec un modèle propriétaire
> de premier plan (GPT-4o, Claude, …), à partir des défaillances **réellement
> observées** avec `openai/gpt-oss-120b` (Groq, palier gratuit).
>
> Les chiffres mesurés se trouvent exclusivement dans `results.jsonl` et
> `REPORT.md`. **Aucune valeur numérique n'est avancée ici**, car aucune
> exécution avec un modèle payant n'a été réalisée. Toute formulation de ce
> document est délibérément qualitative (« diminuerait », « resterait
> inchangé »), et chaque hypothèse est accompagnée de son **critère de
> réfutation**.

## 1. Point de départ : ce qui a effectivement été mesuré

Le test de fumée (9 exécutions, 3 tâches × 3 configurations) n'a produit
**aucune réussite**. Quatre classes de défaillance distinctes ont été observées.
La question traitée ici est : **lesquelles disparaîtraient avec un meilleur
modèle, et lesquelles subsisteraient ?**

La distinction est décisive, car elle sépare les limites du *modèle* des
défauts du *système* — et seules les secondes relèvent du travail d'ingénierie
présenté dans ce mémoire.

## 2. Classification des défaillances observées

| # | Défaillance observée | Nature | Origine |
|---|---|---|---|
| D1 | `Tool choice is none, but model called a tool` | Interaction modèle ↔ système | Phase de planification liant zéro outil ; le fournisseur en déduit `tool_choice=none` |
| D2 | `edit` appelé sans `old_string` / `new_string` | Capacité du modèle | Respect du schéma d'outil |
| D3 | Appel d'outil sans retour (délai de 240 s atteint) | **Système (HCode)** | Un outil ne rend jamais la main malgré un délai par défaut de 30 s |
| D4 | `Connection error.` | Infrastructure | Cause non établie |

## 3. Hypothèses

### H1 — Un modèle propriétaire **masquerait** D1 sans le corriger

**Fondement.** D1 n'est pas un défaut de logique de PEV : la sonde sans clé
`planning-full-arc` réussit avec un modèle scripté. Elle survient lorsqu'un
modèle émet un appel d'outil alors qu'aucun outil n'est lié. Les modèles
propriétaires de premier plan respectent généralement l'absence d'outils.

**Effet attendu.** Les configurations B et C cesseraient de rencontrer cette
erreur ; le taux d'échec dû à D1 tendrait vers zéro.

**Mais — et c'est le point central de ce mémoire —** le défaut resterait
**latent dans le code**. Il est déclenché par la famille `gpt-oss`, qui est
précisément la **cible d'entreprise auto-hébergée** du projet. Évaluer avec un
modèle propriétaire aurait donc **dissimulé un défaut qui se serait manifesté
sur l'infrastructure du client**.

**Réfutation.** H1 est réfutée si un modèle propriétaire reproduit D1. À noter :
D1 s'est révélée **intermittente** — une exécution de contrôle ultérieure sur
le même modèle n'a pas reproduit l'erreur. Une validation exigerait donc un
échantillon suffisant, non une exécution unique.

### H2 — Un modèle propriétaire **corrigerait** D2

**Fondement.** L'omission d'arguments requis dans un appel d'outil est une
défaillance caractéristique des modèles de capacité intermédiaire. D2 s'est
manifestée de façon récurrente sur `gpt-oss-120b`.

**Effet attendu.** Réduction marquée, voire disparition, des erreurs de
validation de schéma ; en conséquence, une part des exécutions actuellement
mortes atteindrait la phase de vérification.

**Réfutation.** H2 est réfutée si les erreurs de validation persistent à taux
comparable.

**Remarque d'ingénierie.** D2 est signalée `"recoverable": false` : le démon
**tue l'exécution**. Réinjecter l'erreur de validation vers le modèle comme
résultat d'outil — le motif d'auto-correction déjà employé par la porte LSP
post-édition — transformerait vraisemblablement un échec fatal en
auto-correction. Cette amélioration est **indépendante du modèle** et bénéficie
donc aussi à la cible `gpt-oss`.

### H3 — Un modèle propriétaire **ne corrigerait pas** D3

**Fondement.** D3 est un appel d'outil qui ne rend jamais la main, alors que
l'outil `bash` porte un délai par défaut de 30 s (`terminal.py:44`). Le blocage
se situe donc côté HCode, en aval de la décision du modèle. Ni la latence ni la
qualité du modèle n'en sont la cause.

**Effet attendu.** Aucun changement. Les expirations subsisteraient.

**Réfutation.** H3 est réfutée si les expirations disparaissent avec le seul
changement de modèle — ce qui indiquerait alors une cause liée au modèle
qu'il faudrait réexaminer.

### H4 — Le palier payant du **même** fournisseur ne lèverait pas le blocage

**Fondement.** Le palier payant de Groq supprime les limites de débit mais
conserve `gpt-oss-120b`. D1 et D2 dépendent du modèle, non du quota.

**Effet attendu.** D4 disparaîtrait probablement ; D1, D2 et D3 subsisteraient.

**Conséquence pratique.** Payer le même fournisseur **ne suffirait pas** à
rendre l'évaluation complète exploitable.

## 4. Synthèse

| Défaillance | Modèle propriétaire | Palier payant, même modèle |
|---|---|---|
| D1 `tool_choice=none` | Masquée, non corrigée | Subsiste |
| D2 schéma d'outil | Probablement corrigée | Subsiste |
| D3 outil bloqué | **Subsiste** | Subsiste |
| D4 erreurs de connexion | Probablement corrigée | Probablement corrigée |

Autrement dit, un modèle propriétaire lèverait au plus **une** des quatre
classes de défaillance de manière franche, en masquerait une deuxième, et
laisserait la troisième — d'origine purement logicielle — intacte.

## 5. Limites de l'étude

1. **Aucune exécution avec un modèle propriétaire n'a été réalisée** : les
   hypothèses ci-dessus ne sont pas validées empiriquement.
2. **Aucune réussite n'a été enregistrée** dans le test de fumée ; le coût par
   exécution *réussie* est donc inconnu, et toute projection de durée ou de
   volume de requêtes pour la campagne complète serait spéculative.
3. **D1 est intermittente**, ce qui interdit une démonstration causale
   avant/après sur une exécution unique et impose un raisonnement par
   construction (le correctif rend l'état fautif impossible) plutôt que par
   observation.
4. **La cause de D4 n'est pas établie** (limitation de débit se manifestant en
   rupture de connexion, ou incident réseau).
5. Le classificateur est évalué contre une **correspondance type → phase
   déclarée par l'évaluateur**, explicitée dans `run_eval.py` ; ce n'est pas
   une vérité de terrain issue du système.

## 6. Protocole de validation

Les hypothèses H1–H4 sont testables sans modifier le harnais : le modèle est
lu depuis le `.env`. Il suffit de renseigner une clé propriétaire et de
relancer le même test de fumée, puis de comparer classe de défaillance par
classe de défaillance :

```bash
uv run python eval/run_eval.py --tasks S1,B1,E1 --configs A,B,C
uv run python eval/analyze.py --smoke
```

Une telle comparaison constituerait une **étude comparative par palier de
modèle**, contribution plus forte qu'une campagne mono-modèle — mais elle exige
des exécutions réelles, non des projections.
