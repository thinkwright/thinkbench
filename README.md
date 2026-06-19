# Thinkbench

A benchmark for autonomous **coding agents**, built by [Thinkwright](https://thinkwright.ai).
72 diverse, real-shaped tasks across **five dimensions** — not homogeneous puzzles, and not
all greenfield. Each task is the kind of work people actually hand a coding agent.

## The five dimensions

| dimension | n | what it tests |
|---|--:|---|
| **implement** | 15 | build a project from scratch from a spec (greenfield) |
| **bug-fix** | 15 | find and fix a planted bug in working code |
| **feature-add** | 15 | add a capability to existing code without breaking it |
| **repair-to-green** | 15 | a library ships with multiple interacting bugs + a failing test suite; make it green |
| **ambiguous-spec** | 12 | *observed, not scored* — a deliberately vague brief; we watch how each model interprets it |

The first four are **graded** by a held-out behavioral grader. The fifth is the interesting
one: real specs are underspecified, so instead of forcing a pass/fail we **observe** how a
model fills the gaps — its assumptions, completeness, and where two models diverge.

## Methodology

Each model runs each task in a fresh workspace through an autonomous agent loop with
`read_file` / `write_file` / `run_command` tools. It sees only `brief.txt` (and `setup/`
starter code for non-greenfield tasks). When it stops, the held-out grader `grade.py` is
dropped in and run with a timeout; it prints a JSON scorecard.

- **Fixed-denominator scoring** — an empty or broken solution scores `0.0`, never a
  misleading partial. A per-task score is `passed / total` checks.
- **Graders are held out** — never shown to the model; a `reference/` solution is included
  only to self-test each grader (every grader scores its reference `1.0`).
- **Hard bar** — graded tasks are calibrated so a *naive* solution lands ~0.4–0.8 and only a
  careful one reaches 1.0 (subtle, often interacting, bugs and edge-case graders).

## Results — MiniMax M3 vs GLM 5.2

First head-to-head (both at thinking-disabled parity, 3 trials/task, cache-aware cost). Full
tables — human and machine-readable — in
[`results/minimax-m3-vs-glm-5.2/`](results/minimax-m3-vs-glm-5.2/) (`RESULTS.md` + `results.json`).
Each benchmark run gets its own folder under `results/`.

| model | full-pass (60 graded) | mean score | avg latency | total cost |
|---|--:|--:|--:|--:|
| **GLM 5.2** | **92%** | 0.976 | 80s | $18.47 |
| **MiniMax M3** | 84% | 0.961 | **45s** | **$6.67** |

**By dimension** — modify-existing-code is largely solved by both; the separation is in
greenfield builds:

| dimension | M3 | GLM |
|---|--:|--:|
| implement | 0.844 (40% solve) | 0.902 (67%) |
| bug-fix | 0.999 | 1.000 |
| feature-add | 1.000 | 1.000 |
| repair-to-green | 1.000 | 1.000 |

**Takeaway:** GLM 5.2 is the more *reliable* solver (higher full-pass, zero package-delivery
failures, perfect on modify-code) and earns its premium on hard greenfield work. MiniMax M3
is **~2.8× cheaper and ~1.8× faster**, and statistically tied on modify-code work
(0.999–1.000 across bug-fix/feature/repair) — the value pick for the bulk of real work, with
one soft spot on hard greenfield builds.

_Total spend to produce this benchmark and its results: **$48.88** across 605 metered runs
(two full runs + iteration/correction overhead)._

## Evaluate your own model

Each task is self-contained and standard-library-only.

1. Give your agent `tasks/<slug>/brief.txt` (and copy in `tasks/<slug>/setup/` if present) in
   a fresh working directory.
2. Have it produce a package importable as `<slug>` (the brief says so).
3. From that directory, run the held-out grader: `python3 grade.py` — it prints a JSON
   scorecard with `score`, `passed`, `total`, and per-check detail.

For ambiguous-spec tasks there is no grader — read what the model built and compare.

## Run the included runner

The Rust runner can drive OpenAI-compatible chat-completions models through the same
agent loop used for this run.

```sh
cd runner
cargo run -- --list
FIREWORKS_API_KEY=... THINKBENCH_TRIALS=1 cargo run -- minimax-m3 glm-5.2
```

Raw runner output is written under `results/runs/<run_id>/` and is ignored by git.
Aggregate a raw run with:

```sh
python3 tools/analyze_run.py results/runs/<run_id> <run-name>
```

## Safety note

This benchmark intentionally evaluates autonomous coding agents with shell access.
The runner clears the environment for commands and graders so provider keys are not
passed into child processes, and saved workspaces skip symlinks instead of following
them. That is not a full sandbox. Run untrusted models and task suites on a disposable
machine or container with no credentials, no private source trees, and no sensitive
files in the workspace.

## Layout

```
tasks/<slug>/      brief.txt + grade.py + setup/ (where applicable) + reference/   (graded)
                   brief.txt only                                                  (ambiguous-spec)
manifest.json      machine-readable task index (slug, type, brief, graded behaviors)
TASKS.md           human-readable catalog grouped by dimension
results/<run>/     per-run results, one folder per benchmark run, e.g.
                     results/minimax-m3-vs-glm-5.2/RESULTS.md   (human: per-type + per-task)
                     results/minimax-m3-vs-glm-5.2/results.json (machine-readable)
tools/             build_dataset.py (regenerate the catalog), analyze_run.py (score a run)
LICENSE / NOTICE   Apache-2.0, © Thinkwright
```

To regenerate the root task catalog and an ignored downloadable bundle:

```sh
python3 tools/build_dataset.py
```

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Copyright 2026 Thinkwright. Attribution
appreciated if you use the benchmark.
