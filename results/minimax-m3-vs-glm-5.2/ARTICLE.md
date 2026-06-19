# MiniMax M3 vs GLM 5.2: a rigorous, diverse coding-agent benchmark

*A Thinkbench head-to-head. Both models run as autonomous coding agents across 72 real-shaped
tasks in five dimensions — including a new "ambiguous-spec" dimension that measures something
most benchmarks don't: how a model handles the underspecified specs people actually write.*

---

## Why we built this

Our [first MiniMax M3 vs Qwen3-Max benchmark](https://thinkwright.ai/minimax-m3-vs-qwen3-max-agent-bench)
ran a handful of coding tasks and found M3 surprisingly strong for its price. This time we
wanted something we'd stake a stronger claim on: a **larger, more diverse, more carefully
graded** suite, and a fair, apples-to-apples head-to-head between **MiniMax M3** and the
newly-released **GLM 5.2** (both served on Fireworks' priority tier).

The result is **Thinkbench** — 72 tasks across five dimensions, open-sourced under Apache-2.0.
A first version was lopsided (mostly greenfield "implement" tasks) and hid a grading flaw; we
caught it, rebalanced to **15 tasks per graded dimension**, hardened the graders, and added a
fifth dimension that turned out to be the most interesting.

## The five dimensions

| dimension | n | what it tests |
|---|--:|---|
| **implement** | 15 | build a project from scratch from a spec (greenfield) |
| **bug-fix** | 15 | find and fix a planted bug in working code |
| **feature-add** | 15 | add a capability to existing code without breaking it |
| **repair-to-green** | 15 | a library ships with several interacting bugs + a failing test suite; make it green |
| **ambiguous-spec** | 12 | *observed, not scored* — a deliberately vague brief; we watch how each model interprets it |

The first four are graded. The fifth is deliberately **not** graded — more on that below.

## How it works

Each model runs each task in a fresh, isolated workspace through an autonomous agent loop
(`read_file` / `write_file` / `run_command` tools). It sees only the brief (and starter code,
for non-greenfield tasks). When it stops, a **held-out grader** is dropped in and run against
hidden behavioral checks.

A few choices that make the numbers trustworthy:

- **Fixed-denominator scoring.** An empty or broken solution scores 0.0 — never a misleading
  partial. (An early version of one grader let an unimportable solution skip its checks and
  score high; we fixed every grader so import failure forces 0.0.)
- **Graders are held out.** The model never sees the grader; a reference solution exists only
  to self-test it (every grader scores its reference exactly 1.0).
- **A genuinely hard bar.** Graded tasks are calibrated so a *naive* solution lands ~0.4–0.8
  and only a careful one reaches 1.0 — subtle, often interacting, bugs and edge-case graders.
- **Parity.** Both models run with thinking disabled, 3 trials per task, cache-aware cost.

## Setup

Both models are served by **Fireworks AI** on the **priority** serving tier (the faster QoS
path), via the OpenAI-compatible endpoint `https://api.fireworks.ai/inference/v1`, both with
thinking disabled (`reasoning_effort=none`) for parity. Cost is computed **cache-aware**: an
agentic loop resends the whole conversation each call, so most input is cache-served, and we
price the cached/uncached split per Fireworks' published rates rather than flat-rating input.

| model | Fireworks model id | serving | input $/Mtok | cached input $/Mtok | output $/Mtok |
|---|---|---|--:|--:|--:|
| **MiniMax M3** | `accounts/fireworks/models/minimax-m3` | priority | 0.45 | 0.09 | 1.80 |
| **GLM 5.2** | `accounts/fireworks/models/glm-5p2` | priority | 2.10 | 0.39 | 6.60 |

## Results

**Overall (60 graded tasks, 3 trials each):**

| model | full-pass | mean score | avg latency | total cost |
|---|--:|--:|--:|--:|
| **GLM 5.2** | **92%** | 0.976 | 80s | $18.47 |
| **MiniMax M3** | 84% | 0.961 | **45s** | **$6.67** |

GLM 5.2 is the stronger solver overall. MiniMax M3 is **~2.8× cheaper and ~1.8× faster** at a
mean score within two points.

**By dimension — this is the real story:**

| dimension | M3 | GLM |
|---|--:|--:|
| implement (greenfield) | 0.844 (40% solve) | 0.902 (67% solve) |
| bug-fix | 0.999 (98%) | 1.000 (100%) |
| feature-add | 1.000 | 1.000 |
| repair-to-green | 1.000 (100%) | 1.000 (100%) |

**Modifying existing code is solved by both models** — even our deliberately-hard bug-fix,
feature-add, and repair tasks land at 0.999–1.000 for *both*. The separation is **entirely in
greenfield, build-from-scratch work**, where GLM's edge is real (67% vs 40% full-solve). This
is exactly the differentiation a balanced, hard suite is supposed to surface — and exactly what
an easy or lopsided suite would miss.

Per task, **54 of 60 graded tasks land within 0.1 of each other**, and every one of the six
larger gaps is a greenfield task. GLM's clear wins: ticketflow, microapi, slotfinder,
orderplane. **M3's wins:** patchwise (1.00 vs 0.62) and migrato (1.00 vs 0.81). It is not
one-sided.

### Why they diverge

We read the actual solutions, not just the scores. Where the two split on a graded task, the
differences are narrow and instructive:

- **ticketflow** (GLM 1.00, M3 0.33) is a *packaging* slip, not a reasoning one. Two of M3's
  trials shipped a `src/` layout with an editable install; the grader imports the package from
  the workspace root, so it wasn't found and all 18 checks were skipped. M3's passing trial
  wrote files flat — exactly as GLM did all three times. The logic was never even reached.
- **patchwise** (M3 1.00, GLM 0.62): GLM's losses were bugs in code it *did* deliver — one trial
  had a name typo (`op_to_old` vs `op_to_old_new`, a NameError on 11 of 15 checks); two trials
  mishandled the "no newline at end of file" diff marker on roundtrip. M3 handled both cleanly.
- **microapi** (M3 0.69, GLM 0.98): M3 normalized handler return values but not a middleware's
  short-circuit return, so a short-circuit crashed; and one trial built a route regex without the
  leading slash and 404-ed every request. GLM was correct throughout.

The pattern: GLM's losses are bugs inside otherwise-complete code; M3's are narrower — a
packaging-discipline slip and the occasional buggy design variant on a single trial. M3 runs a
touch more *per-trial inconsistent*; GLM is steadier.

### A note on rigor

An earlier cut of these numbers showed M3 with several "package-delivery failures" and a
reliability gap. Reading the workspaces revealed three of them were **empty** — a bug in our
*parallel* harness (six processes sharing one temp directory clobbered each other's workspaces),
not M3's doing. We fixed the harness, re-ran those three cleanly (M3 scored 1.000 on all three),
and the tables above are corrected. We flag it because that's exactly the kind of artifact that
quietly inflates a benchmark — and catching it is the whole reason we read the code, not just
the scorecards.

## The ambiguous-spec dimension

Real specs are vague. People hand a coding agent two sentences and expect something sensible.
So we wrote 12 deliberately underspecified briefs — "a caching layer," "rate limiting for an
API," "an audit log" — pinning nothing about the API, the algorithm, the storage, or the
edge cases. There's no single right answer, so we don't grade them. We **observe**: run both
models, keep what they built, and compare how they filled the gaps.

They **converge** hard on the canonical algorithm whenever there is one — token bucket for rate
limiting, TF-IDF + cosine for search, hash-chaining for the audit log — and **diverge** on
everything around it: the API shape, the defaults, and the failure semantics. Three patterns:

1. **M3 is the eager one; GLM is the literal one.** M3 reflexively reached for real-systems
   concerns the briefs never named — thread-safety locks, priority-ordered fallback channels,
   JSON persistence, file-permission hardening, a conditional-validation combinator, a recursive
   feature-flag strategy hierarchy — building toward the system it figured you'd eventually need.
   GLM more often did exactly what was asked and stopped, *twice explicitly declining* to add
   scope ("No knobs for conditional logic"). The eagerness cuts both ways: it's initiative and
   anticipation, but it's also why M3 honored the briefs' repeated "keep it lean, no pile of
   knobs" *less* than GLM did — both were told not to gold-plate, and GLM held the line.
2. **M3 designs an extensible system; GLM writes a direct solution.** The most consistent contrast
   was API paradigm. M3 leaned functional/declarative — composable factory combinators, policy
   objects, data-driven strategies that nest. GLM leaned imperative/explicit — concrete named
   classes, monolithic methods, tuple/dict returns that are flatter and more legible at a glance.
3. **They split on the policy choices the textbook doesn't fix.** Retry: M3 retries *nothing*
   unless you opt in; GLM retries *anything* by default. Notifications: M3 raises on total
   failure; GLM collects per-channel results and returns a report. Workflow: GLM rejects illegal
   moves with reason codes; M3 raises typed exceptions.

The one-line version: **given a vague brief, GLM 5.2 builds the thing you asked for; MiniMax M3
builds the system you'd eventually need.** Neither is "right" — and that's the point. A pass/fail
grader would have flattened the single most useful signal in the whole benchmark.

Every one of these interpretations is **logged as evidence**, not just asserted: the per-task
divergences live in `results/minimax-m3-vs-glm-5.2/OBSERVATIONS.md`, and the actual code each
model wrote is committed under `observed/<task>/<model>/`. Read M3's `auditlog` next to GLM's and
the eager-vs-literal split is right there in the source.

## The verdict

**GLM 5.2** is the more reliable solver: higher full-pass rate, zero non-deliveries, perfect
on modify-existing-code, and a clear edge on hard greenfield builds. It earns its premium when
correctness on from-scratch work matters most.

**MiniMax M3** is the value pick: ~2.8× cheaper, ~1.8× faster, and statistically tied with GLM
on the modify-code work that dominates real engineering (it matches GLM at 0.999–1.000 across
bug-fix, feature-add, and repair). Its one soft spot is hard greenfield builds — a lower full-
solve rate and a touch more per-trial inconsistency — worth knowing if you point it primarily at
from-scratch work.

## Reproduce it

The whole benchmark — all 72 tasks, the held-out graders, reference solutions, the catalog,
and these results — is open source: **[github.com/thinkwright/thinkbench](https://github.com/thinkwright/thinkbench)**
(Apache-2.0). Run any agent against a task's brief and grade it with `python3 grade.py`.

*Producing this benchmark and its results cost **$48.88** across 605 metered runs — two full
runs plus the iteration to catch and fix a grading flaw, parallelize the re-run, and re-run
three runs hit by a harness artifact.*
