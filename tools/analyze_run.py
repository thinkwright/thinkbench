#!/usr/bin/env python3
"""Analyze a raw thinkbench runner directory.

Usage: python3 tools/analyze_run.py <run_dir> [run_name]
e.g.   python3 tools/analyze_run.py results/runs/1781795685 minimax-m3-vs-glm-5.2

The input directory must contain the raw runner `results.jsonl`. Aggregate
folders under `results/<run_name>/` are output, not input.
"""
import json, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NAME = "thinkbench"
OUTPUT_ROOT = Path(os.environ.get("THINKBENCH_OUTPUT_ROOT", REPO / "results"))
DEFAULT_MODELS = REPO / "runner" / "models.json"
_BUGFIX = "ttlcache ledgerfix csvparse pctstats tokenbucket lrucache semvercmp luhn intervalmerge backoff base62 graphpath textwidth movavg deepget".split()
_FEATURE = "searchprefix schemaoneof csvgroupby routerwild kvtxn eventbus querygroup middleware cachetags condschema hsm tmploop cursorpage tierlimit serialhook".split()
_REPAIR = "repaircalc repairspans repairpager repairmoney rrulelite calceval jsonquery datespan unitconv cronmatch romanio textflow permgen graphbip decimalfmt".split()
_AMBIG = "jobflow mergeconf cachelayer ratelimit retryflow notifyhub workflow formvalidate docsearch scheduler auditlog featureflags".split()
DIVERSE = {**{s: "bug-fix" for s in _BUGFIX}, **{s: "feature-add" for s in _FEATURE},
           **{s: "repair-to-green" for s in _REPAIR}, **{s: "ambiguous-spec" for s in _AMBIG}}
GRADED_TYPES = ["implement", "bug-fix", "feature-add", "repair-to-green"]
ttype = lambda t: DIVERSE.get(t, "implement")


def agg(rs):
    n = len(rs)
    if n == 0:
        return {
            "n": 0,
            "solved": 0,
            "solve_rate": 0.0,
            "mean_score": 0.0,
            "avg_secs": 0.0,
            "avg_tokens": 0,
            "cached_frac": 0.0,
            "total_cost_usd": 0.0,
        }
    prompt = sum(r["prompt_tokens"] for r in rs)
    return {
        "n": n,
        "solved": sum(1 for r in rs if r["solved"]),
        "solve_rate": round(sum(1 for r in rs if r["solved"]) / n, 4),
        "mean_score": round(sum(r["score"] for r in rs) / n, 4),
        "avg_secs": round(sum(r["secs"] for r in rs) / n, 1),
        "avg_tokens": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in rs) / n),
        "cached_frac": round(sum(r["cached_tokens"] for r in rs) / max(1, prompt), 4),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rs), 4),
    }


def load_config(models, thinking_mode, trials):
    """Provider/serving/pricing metadata from the runner's models.json
    (path via env THINKBENCH_MODELS). Returns (config_dict, markdown_lines)."""
    p = Path(os.environ.get("THINKBENCH_MODELS", DEFAULT_MODELS))
    if not p.exists():
        return None, []
    cj = json.loads(p.read_text())
    specs = cj.get("models", cj) if isinstance(cj, dict) else cj
    bym = {s["name"]: s for s in specs if s.get("name") in models}
    if not bym:
        return None, []
    endpoint = next((s.get("base_url") for s in specs), None)
    config = {
        "provider": "Fireworks AI", "endpoint": endpoint, "serving_tier": "priority",
        "thinking_mode": thinking_mode, "trials": trials,
        "models": {n: {"model_id": s.get("model"), "service_tier": s.get("service_tier"),
                       "input_usd_per_mtok": s.get("input_usd_per_mtok"),
                       "cached_input_usd_per_mtok": s.get("cached_input_usd_per_mtok"),
                       "output_usd_per_mtok": s.get("output_usd_per_mtok")}
                   for n, s in bym.items()},
    }
    md = ["\n## Configuration\n",
          f"Provider: **{config['provider']}** · endpoint `{endpoint}` · serving tier **priority** "
          f"(both models) · thinking mode **{thinking_mode}** · {trials} trials/task · cache-aware cost.\n",
          "| model | Fireworks model id | tier | input $/Mtok | cached input $/Mtok | output $/Mtok |",
          "|---|---|---|--:|--:|--:|"]
    for n in models:
        mc = config["models"].get(n, {})
        md.append(f"| {n} | `{mc.get('model_id','')}` | {mc.get('service_tier','')} | "
                  f"{mc.get('input_usd_per_mtok','')} | {mc.get('cached_input_usd_per_mtok','')} | "
                  f"{mc.get('output_usd_per_mtok','')} |")
    return config, md


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 tools/analyze_run.py <run_dir> [run_name]")
    run = Path(sys.argv[1])
    jsonl = run / "results.jsonl"
    if not jsonl.is_file():
        raise SystemExit(f"{jsonl} not found; pass a raw runner directory, not an aggregate results folder")
    allrows = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]
    if not allrows:
        raise SystemExit(f"{jsonl} is empty")
    models = sorted({r["worker"] for r in allrows})
    trials = max(r["trial"] for r in allrows)
    # results live in a per-run folder (thinkbench is multi-run), not at the root.
    run_name = sys.argv[2] if len(sys.argv) > 2 else "-vs-".join(models)
    out = OUTPUT_ROOT / run_name
    out.mkdir(parents=True, exist_ok=True)
    config, cfg_md = load_config(models, allrows[0].get("effort_mode"), trials)
    rows = [r for r in allrows if not r.get("observed")]   # graded
    obs = [r for r in allrows if r.get("observed")]         # ambiguous-spec (observed)
    tasks = sorted({r["task"] for r in rows})
    obs_tasks = sorted({r["task"] for r in obs})

    overall = {m: agg([r for r in rows if r["worker"] == m]) for m in models}
    by_type = {typ: {m: agg([r for r in rows if r["worker"] == m and ttype(r["task"]) == typ]) for m in models}
               for typ in GRADED_TYPES}
    by_task = {t: {"type": ttype(t),
                   "models": {m: agg([r for r in rows if r["worker"] == m and r["task"] == t]) for m in models}}
               for t in tasks}
    observed = {t: {m: agg([r for r in obs if r["worker"] == m and r["task"] == t]) for m in models}
                for t in obs_tasks}

    results = {
        "run_ids": sorted({r["run_id"] for r in allrows}), "models": models, "trials": trials,
        "num_graded_tasks": len(tasks), "num_observed_tasks": len(obs_tasks), "num_runs": len(allrows),
        "effort_mode": allrows[0].get("effort_mode"), "config": config,
        "overall_graded": overall, "by_type": by_type, "by_task": by_task, "observed": observed,
    }
    (out / "results.json").write_text(json.dumps(results, indent=2))

    # Human-readable RESULTS.md
    L = [f"# {NAME} — results\n",
         f"{len(allrows)} runs · {len(tasks)} graded tasks + {len(obs_tasks)} observed · {trials} trials/model · "
         f"models: {', '.join(models)} · thinking mode: {results['effort_mode']}\n"] + cfg_md + [
         "\n## Overall (graded tasks)\n",
         "| model | full-pass | mean score | avg latency | avg tokens | cached | total cost |",
         "|---|--:|--:|--:|--:|--:|--:|"]
    for m in models:
        o = overall[m]
        L.append(f"| {m} | {o['solved']}/{o['n']} ({o['solve_rate']:.0%}) | {o['mean_score']:.3f} | "
                 f"{o['avg_secs']:.0f}s | {o['avg_tokens']:,} | {o['cached_frac']:.0%} | ${o['total_cost_usd']:.2f} |")
    L.append("\n## By task type (mean score / full-pass rate)\n")
    L.append("| type | tasks | " + " | ".join(models) + " |")
    L.append("|---|--:|" + "--:|" * len(models))
    for typ in GRADED_TYPES:
        nt = sum(1 for t in tasks if ttype(t) == typ)
        cells = " | ".join(f"{by_type[typ][m]['mean_score']:.3f} ({by_type[typ][m]['solve_rate']:.0%})" for m in models)
        L.append(f"| {typ} | {nt} | {cells} |")
    L.append("\n## Per-task (graded — mean score over trials)\n")
    L.append("| task | type | " + " | ".join(models) + " |")
    L.append("|---|---|" + "--:|" * len(models))
    for t in tasks:
        cells = " | ".join(f"{by_task[t]['models'][m]['mean_score']:.2f}" for m in models)
        L.append(f"| {t} | {by_task[t]['type']} | {cells} |")
    L.append("\n## Ambiguous-spec (observed — NOT scored)\n")
    L.append("_These probe how each model interprets an underspecified brief; there is no right "
             "answer, so no score. The model's solution + transcript are persisted per run for the "
             "qualitative read. Latency / tokens / cost below are descriptive only._\n")
    L.append("| task | " + " | ".join(f"{m} (lat / tok / $)" for m in models) + " |")
    L.append("|---|" + "--:|" * len(models))
    for t in obs_tasks:
        cells = " | ".join(f"{observed[t][m]['avg_secs']:.0f}s / {observed[t][m]['avg_tokens']:,} / ${observed[t][m]['total_cost_usd']:.3f}" for m in models)
        L.append(f"| {t} | {cells} |")
    (out / "RESULTS.md").write_text("\n".join(L) + "\n")

    print(f"wrote {out/'results.json'} and {out/'RESULTS.md'}")
    print(f"  graded: {len(tasks)} tasks | observed: {len(obs_tasks)} tasks | {len(allrows)} runs")
    for m in models:
        o = overall[m]
        print(f"  {m:>12}: {o['solve_rate']:.0%} full-pass | mean {o['mean_score']:.3f} | ${o['total_cost_usd']:.2f} | {o['avg_secs']:.0f}s avg")


if __name__ == "__main__":
    main()
