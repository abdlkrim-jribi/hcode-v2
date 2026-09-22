# Évaluation expérimentale — section prête pour le rapport de stage

> Rédigé à partir des mesures réelles contenues dans `eval/results_pre_fix.jsonl`,
> `eval/results.jsonl` et `eval/classifier.json`. **Chaque chiffre de cette
> section est mesuré** ; aucune valeur n'est estimée. Les tableaux LaTeX
> correspondants sont dans `eval/tables/` (`booktabs`).

---

## 1. Objectif et positionnement

L'objectif est d'évaluer HCode de manière **reproductible et objective**, sur un
jeu de tâches représentatif, et de mesurer l'apport réel de ses deux mécanismes
distinctifs : la boucle **Plan–Exécution–Vérification** (PEV) et la **porte LSP
post-édition**.

La contribution de ce chapitre est double :

1. **Méthodologique** — un harnais d'évaluation reproductible, à critères de
   réussite automatiques, pilotant le **démon réel** et non le mock ;
2. **Diagnostique** — l'identification, la traçabilité jusqu'au code et la
   quantification de **quatre classes de défauts** qui empêchent aujourd'hui
   l'agent d'aboutir sur le modèle cible.

## 2. Protocole expérimental

### 2.1 Projet bac à sable

Un paquet Python fixe (`inventory` : `models`, `pricing`, `store`, `report`)
accompagné de sa suite de tests. État de référence vérifié avant toute
campagne : **6 tests au vert, 0 erreur Pyright**. Chaque exécution recopie ce
modèle vers un répertoire de travail neuf ; l'agent ne modifie jamais la
référence, ce qui garantit un état de départ identique octet par octet.

### 2.2 Jeu de tâches

**24 tâches, 6 types × 4**, selon la typologie demandée : *trivial* (localiser),
*simple* (modifier une fonction), *modéré* (ajouter une fonctionnalité et ses
tests), *complexe* (modification multi-fichiers), *correction de bug* (défaut
injecté), *erreur sémantique* (erreur de typage détectable par Pyright).

Chaque tâche porte un **critère de réussite automatique** — test `pytest` caché,
vérification Pyright, ou contenu de la réponse — sans aucun jugement humain. Les
tests de vérification sont copiés dans le répertoire de travail **après**
l'exécution de l'agent, afin qu'ils ne puissent être ni lus ni contournés.

### 2.3 Configurations (ablation)

| Clé | Configuration | `mode` | `HCODE_POST_EDIT_LSP` |
|---|---|---|---|
| A | Agent sans Verify | `fast` | `0` |
| B | PEV sans la porte LSP post-édition (**LSP de la phase Verify conservé**) | `planning` | `0` |
| C | PEV + LSP (système complet) | `planning` | `1` |

**Précision méthodologique importante.** La configuration B ne peut pas être
nommée « PEV sans LSP ». La lecture du code établit que `factory.py` transmet
`diagnostics_provider=verify_diagnostics_addendum` à `PEVMiddleware`
**inconditionnellement** dès que PEV est actif, et que `HCODE_POST_EDIT_LSP` ne
contrôle **que** `PostEditLspMiddleware`. B retire donc la porte post-édition
mais conserve l'addendum LSP de la phase Verify ; la nommer autrement
surestimerait l'ablation.

### 2.4 Validation du dispositif *avant* toute mesure

Deux validations préalables, toutes deux **sans appel modèle** :

- **Jeu de tâches** — pour les 24 tâches : ancres d'injection présentes, les
  4 bugs injectés font effectivement **échouer** la suite de référence, et les
  4 erreurs de typage produisent effectivement **1 erreur Pyright** chacune. Une
  tâche déjà satisfaite avant exécution ne mesurerait rien.
- **Extracteur de métriques** — le pont ne mappe pas `on_chat_model_start/end`,
  le nombre d'appels LLM est donc dérivé des frontières de tours. L'extracteur
  est confronté à un modèle scripté dont la vérité terrain est connue : il
  retrouve **4 tours sur 4**, 1 appel d'outil, et l'arc complet
  `plan → execute → verify`.

## 3. Résultats

### 3.1 Précision du classificateur de tâches

*(Résultat obtenu hors ligne, sans aucun appel modèle — tableau
`tables/classifier.tex`.)*

| Mesure | Valeur |
|---|---|
| Accord avec la phase attendue | **6 / 24 = 25,0 %** |
| Tâches routées vers `fast` | **22 / 24** |
| Tâches routées vers `trivial` | 1 / 24 (T2) |
| Tâches routées vers `plan` | **1 / 24** (C2) |

**Constat central.** `fast` et `trivial` ne transitionnent jamais : **23 tâches
sur 24 n'atteignent donc jamais la phase Verify de leur propre initiative**,
y compris **la totalité** des tâches de correction de bug et d'erreur
sémantique — précisément celles où la vérification apporterait le plus.

Le mécanisme est un simple filtre par mots-clés : seule C2 est classée
« complexe », parce que son énoncé contient le verbe *« Create »* ; seule T2 est
« triviale », parce qu'elle commence par *« What is »*. La fonctionnalité phare
du système est donc, par défaut, **structurellement inatteignable** pour des
tâches réalistes — ce qui justifie a posteriori le basculeur de mode introduit
en amont du projet.

> **Figure suggérée.** Diagramme de répartition des 24 tâches entre `fast`,
> `trivial` et `plan`, mettant en évidence la barre `fast` dominante.

### 3.2 Test de fumée et taxonomie des défauts

9 exécutions (3 tâches × 3 configurations), démon réel, JSON-RPC brut :
**0 réussite, 5 échecs, 4 incidents d'infrastructure**. Quatre classes de
défauts ont été isolées et tracées jusqu'au code :

| Classe | Défaut | Mécanisme établi |
|---|---|---|
| **D1** | `Tool choice is none, but model called a tool` | La phase plan de PEV ne lie **aucun** outil ; LangChain n'émet alors pas de champ `tools` ; le fournisseur en déduit `tool_choice=none` ; `gpt-oss` appelle malgré tout un outil et la requête est rejetée |
| **D1b** | `attempted to call tool 'repo_browser.…' which was not in request.tools` | Le modèle **invente** un nom d'outil pendant la planification |
| **D2** | `parameters for tool edit did not match schema` | Le modèle omet `old_string` / `new_string` ; le fournisseur rejette la complétion entière, et le démon traite l'erreur comme fatale (`"recoverable": false`) |
| **D3** | Appel d'outil sans retour, délai dépassé | Un outil ne rend jamais la main, alors que `bash` porte un délai par défaut de 30 s — le blocage est **côté HCode** |

### 3.3 Effet des correctifs

Deux correctifs ont été développés en réponse (attache-outil de la phase plan ;
réparation d'appel d'outil rejeté), puis les mêmes 9 exécutions ont été rejouées
— tableau `tables/comparison.tex`.

| Indicateur | Avant | Après |
|---|---|---|
| Appels LLM (total) | 6 | **15** |
| Appels d'outils (total) | 6 | **14** |
| Réussites | 0 / 9 | 0 / 9 |
| Échecs fatals | 5 | 5 |

**Lecture.** Le travail réellement accompli par l'agent est multiplié par
environ **2,5**, et D2 cesse d'être fatal dans le cas observé (`S1×A` passait de
mort à 115 s à une exécution complète du budget, 5 appels LLM et 5 appels
d'outils). En revanche :

- **D1 n'est pas corrigé, il est déplacé en D1b.** Le modèle appelle un outil
  pendant la planification quelle que soit la liste liée : liste vide → D1,
  liste d'un seul outil inerte → D1b. Lier *un* outil inerte est donc la
  mauvaise forme de correctif.
- **D3 n'est pas touché** et devient le **blocage dominant** : les 3 exécutions
  de la configuration A atteignent le budget temporel.

> **Figure suggérée.** Chronogramme d'une exécution avant/après, montrant la
> séquence `tool:read → error` (avant) contre `tool:read → tool:edit →
> tool_result:edit` (après).

## 4. Discussion

### 4.1 Zéro réussite n'est pas un échec de l'évaluation

Aucune tâche n'a abouti, mais l'évaluation a rempli sa fonction : elle a
**empêché le lancement d'une campagne de 72 exécutions** qui n'aurait rien
mesuré, et a isolé quatre défauts précis, chacun tracé jusqu'à une ligne de
code. Un dispositif expérimental qui révèle que le système sous test n'est pas
encore mesurable est un résultat, à condition de le dire.

### 4.2 La contrainte du palier gratuit comme révélateur

D1 est déclenché par la famille `gpt-oss`, qui est **la cible d'entreprise
auto-hébergée** du projet. Une évaluation menée sur un modèle propriétaire
aurait très probablement **masqué** ce défaut — qui se serait alors manifesté
sur l'infrastructure du client. La contrainte budgétaire n'a donc pas seulement
limité l'étude : elle a **exposé un défaut qu'une étude mieux dotée aurait
manqué**. L'analyse détaillée figure dans `eval/HYPOTHESES.md`.

## 5. Limites

1. **Aucune réussite enregistrée** : les taux de réussite par configuration et
   par type ne sont pas encore mesurables, et le coût d'une exécution *réussie*
   est inconnu. Toute projection de durée ou de volume pour la campagne
   complète serait spéculative.
2. **n = 1 par cellule.** Plusieurs défauts se sont révélés **intermittents**
   (D1 ne s'est pas reproduit lors d'une exécution de contrôle ; `E1×C` est
   allée plus loin que ses homologues). Les issues par exécution doivent donc
   être lues comme des **signaux, non comme des taux** — ce qui plaide pour
   plusieurs répétitions par cellule dans la campagne définitive.
3. La correspondance type → phase attendue, servant de référence au calcul
   d'accord du classificateur, est une **convention explicitée par
   l'évaluateur**, non une vérité issue du système.
4. La cause des incidents de connexion n'est pas établie.

## 6. Conditions de lancement de la campagne complète

Par ordre de priorité, établi par les mesures :

1. **D3** — diagnostiquer l'appel d'outil qui ne rend jamais la main malgré le
   délai de 30 s. Défaut purement logiciel, bloquant pour **toutes** les
   configurations.
2. **D1b** — lier les **véritables** outils en lecture seule
   (`read`, `ls`, `glob`, `grep`) pendant la phase plan plutôt qu'un outil
   inerte — la phase Verify de PEV procède déjà exactement ainsi — et faire
   énumérer les outils réellement disponibles par le message de réparation.
3. **Budget temporel** — le relever au-delà de 300 s, valeur à laquelle les
   exécutions productives sont encore tronquées.
4. **Répétitions** — prévoir plusieurs exécutions par cellule, les défauts
   étant intermittents.

## 7. Tableaux LaTeX disponibles

| Fichier | Contenu |
|---|---|
| `tables/classifier.tex` | Phase choisie par le classificateur vs attendue (§3.1) |
| `tables/comparison.tex` | Avant / après les correctifs (§3.3) |
| `tables/ablation.tex` | Ablation A/B/C : réussite et coût par exécution |
| `tables/by_type.tex` | Réussite par type de tâche et configuration |

Les deux derniers sont structurellement prêts mais leurs colonnes de réussite
resteront à `0` tant que les conditions du §6 ne sont pas remplies.
