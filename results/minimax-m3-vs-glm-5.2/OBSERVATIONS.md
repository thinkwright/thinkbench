# Ambiguous-spec observations — MiniMax M3 vs GLM 5.2

The 12 **ambiguous-spec** tasks are deliberately underspecified and **not scored** — there is
no right answer. They exist to be *observed*: we run both models on the same vague brief and
compare how each fills the gaps. This file is the evidence behind the qualitative claims in the
write-up. Each model's actual solution is committed under
[`observed/<task>/<model>/`](observed/) (one representative trial per model), so every claim
below is checkable against real code.

## Headline

**Given a vague brief, GLM 5.2 builds the thing you asked for; MiniMax M3 builds the system you'd
eventually need.** M3 is the *eager* one — it reaches for production concerns the brief never
named. GLM stays literal. (See patterns at the bottom.)

## Per-task divergences

Each row: the key decision the brief left open, and how the two diverged. Solutions in
`observed/<task>/{minimax-m3,glm-5.2}/`.

| task | the open decision | MiniMax M3 | GLM 5.2 |
|---|---|---|---|
| **jobflow** | how jobs reference deps + run | object refs, DFS cycle-detect (WHITE/GRAY/BLACK), passes upstream results into jobs, `RLock`, full argparse CLI | string-name refs, Kahn topo-sort, bare `__main__` |
| **mergeconf** | API altitude | high-level OO `MergeResult` dataclass + `provenance_for(path)`, auto-detects JSON/YAML, **no CLI** | low-level `merge() -> (effective, provenance)` tuples + full `--override`/`--explain` CLI |
| **cachelayer** | hashing + safety | mirrors `functools.lru_cache` (rejects unhashable), wraps store in `RLock`, decorator works bare + parametrized | gracefully freezes unhashable args, single-threaded |
| **ratelimit** | algorithm + extras | token bucket + a variable-weight `cost=` param + `.reset()` (extras beyond the ask) | token bucket, always consumes 1, reversed arg order |
| **retryflow** | retry default | `RetryPolicy` object that retries **nothing** unless opted in | decorator-first, retries **anything** by default, composable predicates |
| **notifyhub** | failure model | notifier-centric: tries channels in **priority order**, raises `NoReachableChannel` on total failure | hub-centric: iterates all channels, **collects** per-channel `SendResult`s, never raises |
| **workflow** | declaration style | imperative builder + stateful `Driver`, raises `AmbiguousTransitionError` on dup transitions | declarative `Process` with inline guards, returns rejection **reason codes** |
| **formvalidate** | schema paradigm | functional combinators; **built a `when(cond, then, otherwise)` conditional** | class-based declarative; **explicitly declined** conditional logic ("No knobs") |
| **docsearch** | indexing maturity | TF-IDF+cosine; scans every doc; **disk-backed JSON CLI** | TF-IDF+cosine; lazy **posting-list cache** (scales); in-memory CLI |
| **scheduler** | tick mechanism | fixed 1s background-thread tick (coarse, simple) | min-heap + sleep-until-next-due (precise, idle-efficient) |
| **auditlog** | trust + query | hash-chain, recompute-on-verify (`IntegrityError`), fluent `Query` builder, **`@action` decorator + `0o600` file perms** | hash-chain, stores hashes, `verify()->bool`, flat `query(**filters)` |
| **featureflags** | targeting architecture | polymorphic `Strategy` hierarchy that **recursively nests** (per-env percentage trees), typed `Context` | all rule types **inline in one `is_enabled()`**, loose dict context |

## Overall patterns

1. **M3 over-reaches toward real-systems plumbing; GLM stays literal.** Across the set M3
   reflexively added concerns the briefs never named — thread-safety locks (jobflow, cachelayer),
   priority/fallback delivery (notifyhub), JSON persistence (docsearch, scheduler), file-permission
   hardening + a logging decorator (auditlog), variable cost + reset (ratelimit), a recursive
   strategy hierarchy (featureflags). GLM more often did exactly what was asked and stopped, and
   **twice explicitly declined** to add scope (formvalidate README: "No knobs for conditional
   logic"). Both briefs said "don't gold-plate"; GLM honored the letter, M3 its own read of "show
   the choices you'd defend." The eagerness cuts both ways — initiative vs. restraint.
2. **M3 designs an extensible system; GLM writes a direct solution.** The most consistent contrast
   is API paradigm: M3 leans functional/declarative (composable combinators, policy objects,
   data-driven strategies that nest); GLM leans imperative/explicit (concrete named classes,
   monolithic methods, tuple/dict returns — flatter and more legible at a glance).
3. **They converge on the canonical algorithm, diverge on defaults + failure semantics.** Where a
   textbook answer exists they agree: token bucket (ratelimit), TF-IDF+cosine (docsearch),
   hash-chaining (auditlog), topo-sort (jobflow). The daylight is in the policy choices the
   textbook doesn't fix — retry-nothing (M3) vs retry-anything (GLM); raise-on-failure (M3) vs
   collect-and-report (GLM); reason-codes (GLM) vs typed-exceptions (M3).

_Read it yourself: `observed/<task>/minimax-m3/` vs `observed/<task>/glm-5.2/`._
