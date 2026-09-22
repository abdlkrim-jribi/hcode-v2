# Évaluation expérimentale de HCode — rapport de résultats

*Généré le 2026-09-22 15:52 UTC à partir de `results.jsonl` (9 exécutions enregistrées).*

> **SMOKE TEST — échantillon partiel.** Ces chiffres proviennent d'un sous-ensemble de tâches destiné à valider le harnais, **pas** de la campagne complète 24×3. Ils ne doivent pas être cités comme résultats définitifs.

## 1. Protocole

- **Modèle** : `openai/gpt-oss-120b` (Groq, palier gratuit), `HCODE_SLIM_PROMPT=max`.
- **Exécution** : chaque tâche est lancée contre un **démon réel** via JSON-RPC brut sur stdio — jamais le mock (règle de vérification du projet).
- **Isolation** : le bac à sable est restauré à un état identique octet par octet avant chaque exécution ; l'injection de bug / d'erreur de type est appliquée ensuite.
- **Succès** : critère automatique et objectif (pytest caché, pyright, ou contenu de la réponse). Aucun jugement humain.
- **Échecs d'infrastructure** (429 / 413 / délai dépassé / erreur fournisseur) : comptés séparément et **exclus** des taux de réussite.

## 2. Définition honnête de la configuration B

`factory.py` transmet `diagnostics_provider=verify_diagnostics_addendum` à `PEVMiddleware` **inconditionnellement** dès que PEV est actif ; aucune variable d'environnement ne le désactive. `HCODE_POST_EDIT_LSP` ne contrôle **que** `PostEditLspMiddleware`. La configuration B retire donc la porte LSP post-édition mais **conserve** l'addendum LSP de la phase Verify. L'appeler « PEV sans LSP » surestimerait l'ablation ; elle est donc nommée *« PEV sans la porte LSP post-édition (LSP de la phase Verify conservé) »*.

## 3. Ablation A / B / C

| Configuration | Réussite | Durée moy. (s) | Appels LLM | Outils | Corrections | Infra |
|---|---|---|---|---|---|---|
| A — Agent sans Verify | 0/1 (0.0 %) | 115.5 | 3.0 | 3.0 | 0.00 | 2 |
| B — PEV sans la porte LSP post-édition (LSP de la phase Verify conservé) | 0/2 (0.0 %) | 5.5 | 0.0 | 0.0 | 0.00 | 1 |
| C — PEV + LSP (système complet) | 0/2 (0.0 %) | 5.8 | 0.0 | 0.0 | 0.00 | 1 |

## 4. Surcoût temporel de PEV

- **B (PEV)** vs A : 5.5 s vs 115.5 s → **×0.05** (-95 %)
- **C (PEV+LSP)** vs A : 5.8 s vs 115.5 s → **×0.05** (-95 %)

## 5. Réussite par type de tâche

| Type | A (Fast) | B (PEV) | C (PEV+LSP) |
|---|---|---|---|
| Trivial | -- | -- | -- |
| Simple | 0/1 | 0/1 | 0/1 |
| Modéré | -- | -- | -- |
| Complexe | -- | -- | -- |
| Correction de bug | -- | 0/1 | 0/1 |
| Erreur sémantique | -- | -- | -- |

## 6. Précision du classificateur (hors ligne, sans appel modèle)

**Accord global : 6/24 = 25.0 %**

Distribution des phases choisies : `fast` ×22, `plan` ×1, `trivial` ×1

> **Constat central.** 22/24 tâches (92 %) sont routées vers la phase `fast`, qui ne transitionne jamais : elles **n'atteignent donc jamais la phase Verify** d'elles-mêmes. Cela inclut la totalité des tâches de correction de bug et d'erreur sémantique — précisément celles où la vérification importe le plus. Ce résultat est mesuré sans aucun appel modèle et confirme la limite du classificateur par mots-clés.

| Tâche | Type | Phase choisie | Attendue | Accord |
|---|---|---|---|---|
| T1 | Trivial | `fast` | `trivial` | **non** |
| T2 | Trivial | `trivial` | `trivial` | oui |
| T3 | Trivial | `fast` | `trivial` | **non** |
| T4 | Trivial | `fast` | `trivial` | **non** |
| S1 | Simple | `fast` | `fast` | oui |
| S2 | Simple | `fast` | `fast` | oui |
| S3 | Simple | `fast` | `fast` | oui |
| S4 | Simple | `fast` | `fast` | oui |
| M1 | Modéré | `fast` | `plan` | **non** |
| M2 | Modéré | `fast` | `plan` | **non** |
| M3 | Modéré | `fast` | `plan` | **non** |
| M4 | Modéré | `fast` | `plan` | **non** |
| C1 | Complexe | `fast` | `plan` | **non** |
| C2 | Complexe | `plan` | `plan` | oui |
| C3 | Complexe | `fast` | `plan` | **non** |
| C4 | Complexe | `fast` | `plan` | **non** |
| B1 | Correction de bug | `fast` | `plan` | **non** |
| B2 | Correction de bug | `fast` | `plan` | **non** |
| B3 | Correction de bug | `fast` | `plan` | **non** |
| B4 | Correction de bug | `fast` | `plan` | **non** |
| E1 | Erreur sémantique | `fast` | `plan` | **non** |
| E2 | Erreur sémantique | `fast` | `plan` | **non** |
| E3 | Erreur sémantique | `fast` | `plan` | **non** |
| E4 | Erreur sémantique | `fast` | `plan` | **non** |

## 7. Échecs d'infrastructure (exclus des taux)

Total : **4** exécution(s).

- `infra:provider` : 2
- `infra:timeout` : 2

## 7bis. Signatures d'erreur systématiques

Un échec qui se répète à l'identique sur plusieurs tâches indique un **défaut systémique**, pas une limite de capacité de l'agent : le taux de réussite correspondant ne mesure alors pas ce qu'il prétend mesurer.

| Occurrences | Exécutions | Message |
|---|---|---|
| 4 | S1×B, S1×C, B1×B, B1×C | `Tool choice is none, but model called a tool` |
| 2 | B1×A, E1×A | `harness timeout after 240.0s` |
| 2 | E1×B, E1×C | `Connection error.` |
| 1 | S1×A | `Tool call validation failed: tool call validation failed: parameters for tool edit did not match schema: errors: [missing properties: 'old_string', 'new_string'` |

## 8. Journal des exécutions

| Tâche | Type | Conf. | Issue | Durée (s) | LLM | Outils | Corr. | Phases |
|---|---|---|---|---|---|---|---|---|
| B1 | Correction de bug | A | ⚠️ infra | 242.6 | 2 | 2 | 0 | plan |
| B1 | Correction de bug | B | ❌ failure | 5.7 | 0 | 0 | 0 | plan |
| B1 | Correction de bug | C | ❌ failure | 6.7 | 0 | 0 | 0 | plan |
| E1 | Erreur sémantique | A | ⚠️ infra | 241.0 | 1 | 1 | 0 | plan |
| E1 | Erreur sémantique | B | ⚠️ infra | 16.9 | 0 | 0 | 0 | plan |
| E1 | Erreur sémantique | C | ⚠️ infra | 5.8 | 0 | 0 | 0 | plan |
| S1 | Simple | A | ❌ failure | 115.5 | 3 | 3 | 0 | plan |
| S1 | Simple | B | ❌ failure | 5.3 | 0 | 0 | 0 | plan |
| S1 | Simple | C | ❌ failure | 5.0 | 0 | 0 | 0 | plan |
