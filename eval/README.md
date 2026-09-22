# Évaluation expérimentale de HCode

Harnais reproductible mesurant HCode sur 24 tâches × 3 configurations contre un
**démon réel** (JSON-RPC brut sur stdio) — jamais le mock, conformément à la
règle de vérification du projet (`docs/verification.md`).

**Aucun chiffre de ce dossier n'est estimé.** Chaque valeur provient d'un flux
d'événements réel, d'une horloge réelle, ou du code de sortie réel d'un
vérificateur.

## Contenu

| Chemin | Rôle |
|---|---|
| `sandbox/` | Projet Python figé servant de terrain d'essai (modèle *pristine*, jamais modifié par l'agent) |
| `tasks.yaml` | Les 24 tâches : id, type, prompt, `setup` (injection), `success_check` |
| `checks/` | Tests cachés, copiés dans le répertoire de travail **après** l'exécution de l'agent |
| `run_eval.py` | Harnais : reset → injection → démon réel → vérification → `results.jsonl` |
| `validate_tasks.py` | Validation hors ligne du jeu de tâches (0 appel modèle) |
| `analyze.py` | Agrégation → `REPORT.md` + tableaux LaTeX `booktabs` |
| `.work/` | Copies de travail par exécution (ignorées par git) |

## Configurations (ablation)

| Clé | Nom | `mode` | `HCODE_POST_EDIT_LSP` |
|---|---|---|---|
| A | Agent sans Verify | `fast` | `0` |
| B | PEV sans la porte LSP post-édition (**LSP de la phase Verify conservé**) | `planning` | `0` |
| C | PEV + LSP (système complet) | `planning` | `1` |

> **Pourquoi B porte ce nom.** Vérifié dans le code : `factory.py` transmet
> `diagnostics_provider=verify_diagnostics_addendum` à `PEVMiddleware`
> **inconditionnellement** dès que PEV est actif ; aucune variable
> d'environnement ne le désactive. `HCODE_POST_EDIT_LSP` ne contrôle **que**
> `PostEditLspMiddleware`. B retire donc la porte post-édition mais conserve
> l'addendum LSP de Verify. L'appeler « PEV sans LSP » surestimerait l'ablation.

## Isolation

Chaque exécution recopie `sandbox/` vers `.work/<tâche>__<config>/`. L'agent ne
touche jamais le modèle de référence, donc deux exécutions partent d'un état
identique octet par octet. L'injection (`setup`) est appliquée après la copie et
**échoue bruyamment** si son ancre a disparu — une injection silencieusement
ignorée invaliderait la tâche.

## Mesures

`n_tool_calls` et `n_corrections` sont **exacts** (un événement chacun).
`n_llm_calls` est dérivé des frontières de tours du modèle, car le pont ne
mappe pas `on_chat_model_start/end` (`bridge.py` : *"not mapped in C2"*). Cet
extracteur est **validé** contre un modèle scripté dont le nombre de tours est
connu :

```bash
uv run python eval/run_eval.py --validate-extractor   # sans clé, sans quota
```

## Échecs d'infrastructure

429 / 413 / délais dépassés / erreurs fournisseur sont enregistrés avec
`outcome="infra"`, **exclus** des taux de réussite et **rejoués** plus tard :
une limite de débit n'est pas un échec de l'agent. Après plusieurs échecs infra
consécutifs le harnais s'arrête proprement ; relancer la même commande reprend
là où elle s'était arrêtée.

## Utilisation

```bash
# 1. Valider le jeu de tâches (hors ligne, gratuit)
uv run python eval/validate_tasks.py

# 2. Précision du classificateur (hors ligne, aucun appel modèle)
uv run python eval/run_eval.py --classifier-only

# 3. Smoke test (9 exécutions)
uv run python eval/run_eval.py --tasks S1,B1,E1 --configs A,B,C

# 4. Campagne complète (reprenable — relancer après épuisement du quota)
uv run python eval/run_eval.py

# 5. Sous-ensembles
uv run python eval/run_eval.py --types bug,semantic --configs C --limit 8

# 6. Analyse
uv run python eval/analyze.py            # ou --smoke pour marquer l'échantillon
```

La reprise est automatique : les paires (tâche, config) déjà **réglées**
(succès/échec) sont sautées ; les paires `infra` sont rejouées.

## Modèle

`openai/gpt-oss-120b` via Groq (palier gratuit), `HCODE_SLIM_PROMPT=max`,
configuré dans le `.env` à la racine du dépôt (chargé par le démon).
