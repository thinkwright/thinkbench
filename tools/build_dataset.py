#!/usr/bin/env python3
"""Assemble the downloadable benchmark dataset from this repo's tasks/.

Produces a self-contained, reproducible bundle under dataset/<NAME>/ and
refreshes the root manifest/catalog:
  README.md      overview + methodology + how-to-run + license
  TASKS.md       human catalog (grouped by type): brief + graded behaviors per task
  manifest.json  machine-readable: [{slug, type, brief, graded_behaviors[], num_checks, has_setup}]
  tasks/<slug>/  brief.txt + setup/ (diverse only) + grade.py + reference/   (the full kit)

Graded behaviors are extracted canonically by running each grade.py against its
reference solution (offline, no API) and reading the scorecard's check id+desc.
results.json (per-task M3-vs-GLM performance) is added after a run by --with-results.
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASKS = REPO / "tasks"
NAME = "thinkbench"
OUT = REPO / "dataset" / NAME
LICENSE_SRC = REPO / "LICENSE"
COPYRIGHT = "Copyright 2026 Thinkwright"

# Task types by membership (everything not listed is greenfield "implement").
_BUGFIX = "ttlcache ledgerfix csvparse pctstats tokenbucket lrucache semvercmp luhn intervalmerge backoff base62 graphpath textwidth movavg deepget".split()
_FEATURE = "searchprefix schemaoneof csvgroupby routerwild kvtxn eventbus querygroup middleware cachetags condschema hsm tmploop cursorpage tierlimit serialhook".split()
_REPAIR = "repaircalc repairspans repairpager repairmoney rrulelite calceval jsonquery datespan unitconv cronmatch romanio textflow permgen graphbip decimalfmt".split()
_AMBIG = "jobflow mergeconf cachelayer ratelimit retryflow notifyhub workflow formvalidate docsearch scheduler auditlog featureflags".split()
DIVERSE_TYPE = {
    **{s: "bug-fix" for s in _BUGFIX},
    **{s: "feature-add" for s in _FEATURE},
    **{s: "repair-to-green" for s in _REPAIR},
    **{s: "ambiguous-spec" for s in _AMBIG},
}
TYPE_ORDER = ["implement", "bug-fix", "feature-add", "repair-to-green", "ambiguous-spec"]
TYPE_BLURB = {
    "implement": "Greenfield: build a project from scratch from a spec.",
    "bug-fix": "Find and fix a planted bug in a small working library; a hidden test verifies.",
    "feature-add": "Add a missing capability to working code without breaking it.",
    "repair-to-green": "A library ships with planted bugs and a visible failing test suite; make it green (a hidden superset grades).",
    "ambiguous-spec": "Deliberately underspecified — NOT graded. The brief leaves real design decisions open; we observe how each model interprets it.",
}


def grade_reference(slug):
    """Run a task's grader against its reference solution; return its scorecard."""
    tdir = TASKS / slug
    ref = tdir / "reference"
    if not ref.is_dir():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copytree(ref, tmp / slug)
        shutil.copy(tdir / "grade.py", tmp / "grade.py")
        try:
            out = subprocess.run([sys.executable, "grade.py"], cwd=tmp,
                                 capture_output=True, text=True, timeout=60)
            line = [l for l in out.stdout.splitlines() if l.strip()][-1]
            return json.loads(line)
        except Exception as e:
            print(f"  !! {slug}: grader self-test failed: {e}")
            return None


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "tasks").mkdir(parents=True)

    slugs = sorted(d.name for d in TASKS.iterdir() if d.is_dir() and (d / "brief.txt").is_file())
    manifest = []
    sanity = []
    for slug in slugs:
        tdir = TASKS / slug
        typ = DIVERSE_TYPE.get(slug, "implement")
        brief = (tdir / "brief.txt").read_text()
        has_setup = (tdir / "setup").is_dir()
        observed = typ == "ambiguous-spec"
        if observed:
            checks = []
        else:
            sc = grade_reference(slug)
            checks = ([{"id": c["id"], "desc": c.get("desc") or c.get("detail") or c["id"]}
                       for c in sc["checks"]] if sc else [])
            sanity.append((slug, sc["score"] if sc else None))
        manifest.append({
            "slug": slug, "type": typ, "brief": brief, "observed": observed,
            "graded_behaviors": checks, "num_checks": len(checks),
            "has_setup": has_setup,
        })
        # Copy the kit for this task (observed tasks ship only the brief).
        dst = OUT / "tasks" / slug
        dst.mkdir(parents=True)
        shutil.copy(tdir / "brief.txt", dst / "brief.txt")
        if (tdir / "grade.py").is_file():
            shutil.copy(tdir / "grade.py", dst / "grade.py")
        for sub in ("setup", "reference"):
            if (tdir / sub).is_dir():
                shutil.copytree(tdir / sub, dst / sub,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    manifest_text = json.dumps(manifest, indent=2) + "\n"
    (OUT / "manifest.json").write_text(manifest_text)
    (REPO / "manifest.json").write_text(manifest_text)

    # Human catalog, grouped by type.
    by_type = {t: [m for m in manifest if m["type"] == t] for t in TYPE_ORDER}
    graded_n = sum(1 for m in manifest if not m["observed"])
    obs_n = sum(1 for m in manifest if m["observed"])
    lines = [f"# {NAME} — task catalog\n",
             f"{len(manifest)} coding-agent tasks across {len([t for t in TYPE_ORDER if by_type[t]])} types "
             f"({graded_n} graded by a hidden behavioral grader, {obs_n} ambiguous-spec tasks observed without grading).\n"]
    for t in TYPE_ORDER:
        ms = by_type[t]
        if not ms:
            continue
        lines.append(f"\n## {t} ({len(ms)})\n\n_{TYPE_BLURB[t]}_\n")
        for m in ms:
            head = "observed (no grader)" if m["observed"] else f"{m['num_checks']} checks"
            lines.append(f"\n### {m['slug']} · {head}\n")
            # 4-backtick fence: briefs contain their own ```python / ```json blocks.
            lines.append("````\n" + m["brief"].strip() + "\n````\n")
            if not m["observed"]:
                lines.append("**Graded behaviors:**\n")
                for c in m["graded_behaviors"]:
                    lines.append(f"- `{c['id']}` — {c['desc']}")
                lines.append("")
    tasks_text = "\n".join(lines).rstrip() + "\n"
    (OUT / "TASKS.md").write_text(tasks_text)
    (REPO / "TASKS.md").write_text(tasks_text)

    readme = f"""# {NAME}

A reproducible benchmark of autonomous coding agents on **diverse, real-shaped tasks**
(not homogeneous puzzles): {len(manifest)} tasks across five types —
{', '.join(f'{t} ({len(by_type[t])})' for t in TYPE_ORDER if by_type[t])}.

It has two modes. **Graded** tasks (implement, bug-fix, feature-add, repair-to-green)
are scored by a held-out behavioral grader. **Ambiguous-spec** tasks are deliberately
underspecified and **not graded** — they exist to be *observed*: there is no single
right answer, so we look at how each model interprets the same vague brief and report
the divergence qualitatively.

## How a graded task is run

Each model runs each task in a fresh, isolated workspace through an autonomous agent
loop with `read_file` / `write_file` / `run_command` tools. The model sees only
`brief.txt` (and `setup/` starter code for non-greenfield tasks). After the model
stops, the held-out grader `grade.py` is dropped in and run; it prints a JSON
scorecard. Scoring uses a **fixed denominator** — an empty/broken solution scores
0.0, never a misleading partial — so a per-task score is `passed / total` checks.

## How an ambiguous-spec task is run

Same agent loop, but the task ships only `brief.txt` and there is no grader. We keep
the model's solution and run metrics and compare interpretations across models; the
score is not defined.

## Layout

- `manifest.json` — machine-readable task index (slug, type, brief, graded behaviors).
- `TASKS.md` — human-readable catalog grouped by type.
- `tasks/<slug>/`
  - `brief.txt` — the prompt the model saw.
  - `setup/` — starter code copied into the workspace (diverse tasks only).
  - `grade.py` — the held-out behavioral grader (never shown to the model).
  - `reference/` — a reference solution (never shown to the model; used to self-test the grader).
- `results.json` — per-task model results (added after a benchmark run).

## Reproducing

Place the model's solution as a package `<slug>` in a working dir, run `python3 grade.py`
from that dir, and read the JSON scorecard. Each `grade.py` is standalone (stdlib only).

## Download hygiene

This public bundle contains task briefs, starter code, held-out graders, reference
solutions, the task catalog, and aggregate result files. It does not include model
transcripts, saved workspaces, raw runner directories, provider credentials, environment
dumps, private host paths, or local machine metadata. Example names, addresses, emails,
hosts, and domains inside task fixtures are synthetic.

## License

Licensed under the Apache License, Version 2.0 — see `LICENSE`. {COPYRIGHT}.
If you use this benchmark, attribution to Thinkwright is appreciated.
"""
    (OUT / "README.md").write_text(readme)
    shutil.copy(LICENSE_SRC, OUT / "LICENSE")
    (OUT / "NOTICE").write_text(
        f"{NAME}\n{COPYRIGHT}\n\n"
        "This dataset (task briefs, graders, reference solutions, and results) is\n"
        "licensed under the Apache License, Version 2.0 (see the LICENSE file).\n"
    )

    # Report.
    bad = [(s, sc) for s, sc in sanity if sc != 1.0]
    print(f"built {OUT}")
    print(f"  {len(manifest)} tasks: " + ", ".join(f"{t}={len(by_type[t])}" for t in TYPE_ORDER if by_type[t]))
    print(f"  reference self-test: {len(sanity) - len(bad)}/{len(sanity)} score 1.0"
          + (f"  !! NOT 1.0: {bad}" if bad else "  (all clean)"))


if __name__ == "__main__":
    main()
