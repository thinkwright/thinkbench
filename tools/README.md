# tools

- `build_dataset.py` — regenerates `manifest.json` + `TASKS.md` (and a copy of the bundle)
  from `tasks/`, extracting each grader's behaviors by running it against its `reference/`.
- `analyze_run.py` — aggregates a benchmark run's `results.jsonl` into `results.json` +
  `RESULTS.md` (graded scores by type/task, plus the observed ambiguous-spec section).

Common commands:

```sh
python3 tools/build_dataset.py
python3 tools/analyze_run.py results/runs/<run_id> <run-name>
```

`build_dataset.py` writes an ignored downloadable bundle under `dataset/thinkbench/`
and refreshes the root `manifest.json` and `TASKS.md`. `analyze_run.py` expects a
raw runner directory containing `results.jsonl`; the aggregate folders under
`results/<run-name>/` are output, not input.
