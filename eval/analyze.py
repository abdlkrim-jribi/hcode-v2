"""Analyse eval/results.jsonl -> eval/REPORT.md + LaTeX (booktabs) tables.

Every number printed here is computed from measured rows in results.jsonl.
Nothing is estimated. Where a cell has no data, it prints "--" rather than 0,
so an unrun cell can never be mistaken for a measured zero.

Success rates are computed over SETTLED runs only (outcome success|failure).
Infrastructure failures (429 / 413 / timeout / provider) are excluded from the
denominator and reported separately -- a rate limit is not an agent failure.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS = EVAL_DIR / "results.jsonl"
TABLES = EVAL_DIR / "tables"

CONFIG_ORDER = ["A", "B", "C"]
CONFIG_LABEL = {
    "A": "A — Agent sans Verify",
    "B": "B — PEV sans la porte LSP post-édition (LSP de la phase Verify conservé)",
    "C": "C — PEV + LSP (système complet)",
}
CONFIG_SHORT = {"A": "A (Fast)", "B": "B (PEV)", "C": "C (PEV+LSP)"}
TYPE_ORDER = ["trivial", "simple", "moderate", "complex", "bug", "semantic"]
TYPE_FR = {
    "trivial": "Trivial", "simple": "Simple", "moderate": "Modéré",
    "complex": "Complexe", "bug": "Correction de bug", "semantic": "Erreur sémantique",
}


def load(path: Path = RESULTS) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def dedup(rows: list[dict]) -> list[dict]:
    """Keep the LAST row per (task, config) -- a retried infra pair supersedes."""
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        latest[(r.get("task"), r.get("config"))] = r
    return list(latest.values())


def fmt(x, nd=1, dash="--"):
    if x is None:
        return dash
    return f"{x:.{nd}f}"


def mean_or_none(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def agg(rows: list[dict]) -> dict:
    """Per-config aggregate over settled rows."""
    out = {}
    for c in CONFIG_ORDER:
        sub = [r for r in rows if r.get("config") == c and r.get("outcome") in ("success", "failure")]
        infra = [r for r in rows if r.get("config") == c and r.get("outcome") == "infra"]
        n = len(sub)
        ok = sum(1 for r in sub if r.get("success"))
        out[c] = {
            "n": n,
            "ok": ok,
            "rate": (ok / n * 100) if n else None,
            "duration": mean_or_none([r.get("duration_s") for r in sub]),
            "llm": mean_or_none([r.get("n_llm_calls") for r in sub]),
            "tools": mean_or_none([r.get("n_tool_calls") for r in sub]),
            "corr": mean_or_none([r.get("n_corrections") for r in sub]),
            "infra": len(infra),
        }
    return out


def by_type(rows: list[dict]) -> dict:
    grid = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("outcome") in ("success", "failure"):
            grid[r.get("type")][r.get("config")].append(bool(r.get("success")))
    return grid


# ── LaTeX emitters (booktabs) ─────────────────────────────────────────────────

def tex_ablation(a: dict) -> str:
    rows = []
    for c in CONFIG_ORDER:
        d = a[c]
        rate = f"{d['ok']}/{d['n']} ({fmt(d['rate'])}\\%)" if d["n"] else "--"
        rows.append(
            f"{CONFIG_SHORT[c]} & {rate} & {fmt(d['duration'])} & {fmt(d['llm'])} & "
            f"{fmt(d['tools'])} & {fmt(d['corr'], 2)} & {d['infra']} \\\\"
        )
    return (
        "\\begin{table}[htbp]\n\\centering\n"
        "\\caption{Ablation des configurations : taux de réussite et coût par exécution.}\n"
        "\\label{tab:ablation}\n"
        "\\begin{tabular}{lcccccc}\n\\toprule\n"
        "Configuration & Réussite & Durée (s) & Appels LLM & Outils & Corrections & Infra \\\\\n"
        "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def tex_by_type(grid: dict) -> str:
    rows = []
    for t in TYPE_ORDER:
        cells = []
        for c in CONFIG_ORDER:
            vals = grid.get(t, {}).get(c, [])
            cells.append(f"{sum(vals)}/{len(vals)}" if vals else "--")
        rows.append(f"{TYPE_FR[t]} & " + " & ".join(cells) + " \\\\")
    return (
        "\\begin{table}[htbp]\n\\centering\n"
        "\\caption{Réussite par type de tâche et par configuration.}\n"
        "\\label{tab:bytype}\n"
        "\\begin{tabular}{lccc}\n\\toprule\n"
        "Type de tâche & A (Fast) & B (PEV) & C (PEV+LSP) \\\\\n\\midrule\n"
        + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def tex_classifier(cl: list[dict]) -> str:
    dist = defaultdict(int)
    for r in cl:
        dist[r["classifier_phase"]] += 1
    agree = sum(r["agree"] for r in cl)
    rows = []
    for t in TYPE_ORDER:
        sub = [r for r in cl if r["type"] == t]
        if not sub:
            continue
        chosen = defaultdict(int)
        for r in sub:
            chosen[r["classifier_phase"]] += 1
        chosen_s = ", ".join(f"{k} ($\\times${v})" for k, v in sorted(chosen.items()))
        rows.append(
            f"{TYPE_FR[t]} & {sub[0]['expected_phase']} & {chosen_s} & "
            f"{sum(r['agree'] for r in sub)}/{len(sub)} \\\\"
        )
    return (
        "\\begin{table}[htbp]\n\\centering\n"
        "\\caption{Phase initiale choisie par le TaskClassifier (hors ligne, sans appel modèle) "
        "comparée à la phase attendue par type.}\n"
        "\\label{tab:classifier}\n"
        "\\begin{tabular}{llcc}\n\\toprule\n"
        "Type & Phase attendue & Phase choisie & Accord \\\\\n\\midrule\n"
        + "\n".join(rows) + "\n\\midrule\n"
        + f"\\textbf{{Total}} & -- & -- & \\textbf{{{agree}/{len(cl)} "
          f"({agree/len(cl)*100:.1f}\\%)}} \\\\\n"
        + "\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


# ── Report ────────────────────────────────────────────────────────────────────

def build_report(rows: list[dict], cl: list[dict], smoke: bool) -> str:
    a = agg(rows)
    grid = by_type(rows)
    settled = [r for r in rows if r.get("outcome") in ("success", "failure")]
    infra = [r for r in rows if r.get("outcome") == "infra"]

    base = a["A"]["duration"]
    overhead = []
    for c in ("B", "C"):
        d = a[c]["duration"]
        overhead.append(
            f"- **{CONFIG_SHORT[c]}** vs A : "
            + (f"{fmt(d)} s vs {fmt(base)} s → "
               f"**×{d/base:.2f}** ({(d/base-1)*100:+.0f} %)"
               if (d and base) else "-- (données insuffisantes)")
        )

    L = []
    L.append("# Évaluation expérimentale de HCode — rapport de résultats\n")
    L.append(f"*Généré le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
             f"à partir de `results.jsonl` ({len(rows)} exécutions enregistrées).*\n")
    if smoke:
        L.append("> **SMOKE TEST — échantillon partiel.** Ces chiffres proviennent d'un "
                 "sous-ensemble de tâches destiné à valider le harnais, **pas** de la "
                 "campagne complète 24×3. Ils ne doivent pas être cités comme résultats "
                 "définitifs.\n")

    L.append("## 1. Protocole\n")
    L.append("- **Modèle** : `openai/gpt-oss-120b` (Groq, palier gratuit), "
             "`HCODE_SLIM_PROMPT=max`.\n"
             "- **Exécution** : chaque tâche est lancée contre un **démon réel** via JSON-RPC "
             "brut sur stdio — jamais le mock (règle de vérification du projet).\n"
             "- **Isolation** : le bac à sable est restauré à un état identique octet par octet "
             "avant chaque exécution ; l'injection de bug / d'erreur de type est appliquée "
             "ensuite.\n"
             "- **Succès** : critère automatique et objectif (pytest caché, pyright, ou "
             "contenu de la réponse). Aucun jugement humain.\n"
             "- **Échecs d'infrastructure** (429 / 413 / délai dépassé / erreur fournisseur) : "
             "comptés séparément et **exclus** des taux de réussite.\n")

    L.append("## 2. Définition honnête de la configuration B\n")
    L.append("`factory.py` transmet `diagnostics_provider=verify_diagnostics_addendum` à "
             "`PEVMiddleware` **inconditionnellement** dès que PEV est actif ; aucune variable "
             "d'environnement ne le désactive. `HCODE_POST_EDIT_LSP` ne contrôle **que** "
             "`PostEditLspMiddleware`. La configuration B retire donc la porte LSP "
             "post-édition mais **conserve** l'addendum LSP de la phase Verify. "
             "L'appeler « PEV sans LSP » surestimerait l'ablation ; elle est donc nommée "
             "*« PEV sans la porte LSP post-édition (LSP de la phase Verify conservé) »*.\n")

    L.append("## 3. Ablation A / B / C\n")
    L.append("| Configuration | Réussite | Durée moy. (s) | Appels LLM | Outils | Corrections | Infra |")
    L.append("|---|---|---|---|---|---|---|")
    for c in CONFIG_ORDER:
        d = a[c]
        rate = f"{d['ok']}/{d['n']} ({fmt(d['rate'])} %)" if d["n"] else "--"
        L.append(f"| {CONFIG_LABEL[c]} | {rate} | {fmt(d['duration'])} | {fmt(d['llm'])} "
                 f"| {fmt(d['tools'])} | {fmt(d['corr'], 2)} | {d['infra']} |")
    L.append("")

    L.append("## 4. Surcoût temporel de PEV\n")
    L += overhead
    L.append("")

    L.append("## 5. Réussite par type de tâche\n")
    L.append("| Type | A (Fast) | B (PEV) | C (PEV+LSP) |")
    L.append("|---|---|---|---|")
    for t in TYPE_ORDER:
        cells = []
        for c in CONFIG_ORDER:
            vals = grid.get(t, {}).get(c, [])
            cells.append(f"{sum(vals)}/{len(vals)}" if vals else "--")
        L.append(f"| {TYPE_FR[t]} | " + " | ".join(cells) + " |")
    L.append("")

    L.append("## 6. Précision du classificateur (hors ligne, sans appel modèle)\n")
    if cl:
        agree = sum(r["agree"] for r in cl)
        dist = defaultdict(int)
        for r in cl:
            dist[r["classifier_phase"]] += 1
        L.append(f"**Accord global : {agree}/{len(cl)} = {agree/len(cl)*100:.1f} %**\n")
        L.append("Distribution des phases choisies : "
                 + ", ".join(f"`{k}` ×{v}" for k, v in sorted(dist.items())) + "\n")
        n_fast = dist.get("fast", 0)
        L.append(f"> **Constat central.** {n_fast}/{len(cl)} tâches "
                 f"({n_fast/len(cl)*100:.0f} %) sont routées vers la phase `fast`, qui ne "
                 f"transitionne jamais : elles **n'atteignent donc jamais la phase Verify** "
                 f"d'elles-mêmes. Cela inclut la totalité des tâches de correction de bug et "
                 f"d'erreur sémantique — précisément celles où la vérification importe le plus. "
                 f"Ce résultat est mesuré sans aucun appel modèle et confirme la limite du "
                 f"classificateur par mots-clés.\n")
        L.append("| Tâche | Type | Phase choisie | Attendue | Accord |")
        L.append("|---|---|---|---|---|")
        for r in cl:
            L.append(f"| {r['task']} | {TYPE_FR.get(r['type'], r['type'])} | "
                     f"`{r['classifier_phase']}` | `{r['expected_phase']}` | "
                     f"{'oui' if r['agree'] else '**non**'} |")
        L.append("")
    else:
        L.append("_Non calculé — lancer `run_eval.py --classifier-only`._\n")

    L.append("## 7. Échecs d'infrastructure (exclus des taux)\n")
    if infra:
        kinds = defaultdict(int)
        for r in infra:
            kinds[r.get("error_type") or "infra:unknown"] += 1
        L.append(f"Total : **{len(infra)}** exécution(s).\n")
        for k, v in sorted(kinds.items()):
            L.append(f"- `{k}` : {v}")
    else:
        L.append("Aucun échec d'infrastructure enregistré.")
    L.append("")

    L.append("## 7bis. Signatures d'erreur systématiques\n")
    sig = defaultdict(list)
    for r in rows:
        txt = r.get("error_text") or ""
        if txt:
            try:
                msg = json.loads(txt).get("message", txt)
            except Exception:
                msg = txt
            sig[str(msg)[:160]].append(f"{r.get('task')}×{r.get('config')}")
    if sig:
        L.append("Un échec qui se répète à l'identique sur plusieurs tâches indique un "
                 "**défaut systémique**, pas une limite de capacité de l'agent : le taux "
                 "de réussite correspondant ne mesure alors pas ce qu'il prétend mesurer.\n")
        L.append("| Occurrences | Exécutions | Message |")
        L.append("|---|---|---|")
        for msg, who in sorted(sig.items(), key=lambda kv: -len(kv[1])):
            L.append(f"| {len(who)} | {', '.join(who)} | `{msg}` |")
    else:
        L.append("Aucune erreur enregistrée.")
    L.append("")

    L.append("## 8. Journal des exécutions\n")
    L.append("| Tâche | Type | Conf. | Issue | Durée (s) | LLM | Outils | Corr. | Phases |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r.get("task", ""), r.get("config", ""))):
        ph = ",".join(r.get("phases_reached") or []) or "--"
        mark = {"success": "✅", "failure": "❌", "infra": "⚠️"}.get(r.get("outcome"), "?")
        L.append(f"| {r.get('task')} | {TYPE_FR.get(r.get('type'), r.get('type'))} | "
                 f"{r.get('config')} | {mark} {r.get('outcome')} | "
                 f"{fmt(r.get('duration_s'))} | {r.get('n_llm_calls', '--')} | "
                 f"{r.get('n_tool_calls', '--')} | {r.get('n_corrections', '--')} | {ph} |")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="mark the report as a partial smoke sample")
    args = ap.parse_args()

    rows = dedup(load())
    cl_path = EVAL_DIR / "classifier.json"
    cl = json.loads(cl_path.read_text(encoding="utf-8")) if cl_path.exists() else []

    report = build_report(rows, cl, args.smoke)
    (EVAL_DIR / "REPORT.md").write_text(report, encoding="utf-8")

    TABLES.mkdir(exist_ok=True)
    a, grid = agg(rows), by_type(rows)
    (TABLES / "ablation.tex").write_text(tex_ablation(a), encoding="utf-8")
    (TABLES / "by_type.tex").write_text(tex_by_type(grid), encoding="utf-8")
    if cl:
        (TABLES / "classifier.tex").write_text(tex_classifier(cl), encoding="utf-8")

    print(f"wrote {EVAL_DIR / 'REPORT.md'}")
    print(f"wrote {TABLES}/*.tex  ({len(list(TABLES.glob('*.tex')))} tables)")
    print(f"rows: {len(rows)}  settled: {sum(1 for r in rows if r.get('outcome') in ('success','failure'))}  "
          f"infra: {sum(1 for r in rows if r.get('outcome') == 'infra')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
