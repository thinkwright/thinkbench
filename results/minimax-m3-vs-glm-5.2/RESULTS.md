# thinkbench — results

432 runs · 60 graded tasks + 12 observed · 3 trials/model · models: glm-5.2, minimax-m3 · thinking mode: none


## Configuration

Provider: **Fireworks AI** · endpoint `https://api.fireworks.ai/inference/v1` · serving tier **priority** (both models) · thinking mode **none** · 3 trials/task · cache-aware cost.

| model | Fireworks model id | tier | input $/Mtok | cached input $/Mtok | output $/Mtok |
|---|---|---|--:|--:|--:|
| glm-5.2 | `accounts/fireworks/models/glm-5p2` | priority | 2.1 | 0.39 | 6.6 |
| minimax-m3 | `accounts/fireworks/models/minimax-m3` | priority | 0.45 | 0.09 | 1.8 |

## Overall (graded tasks)

| model | full-pass | mean score | avg latency | avg tokens | cached | total cost |
|---|--:|--:|--:|--:|--:|--:|
| glm-5.2 | 165/180 (92%) | 0.976 | 80s | 82,443 | 73% | $18.47 |
| minimax-m3 | 152/180 (84%) | 0.961 | 45s | 135,060 | 73% | $6.67 |

## By task type (mean score / full-pass rate)

| type | tasks | glm-5.2 | minimax-m3 |
|---|--:|--:|--:|
| implement | 15 | 0.902 (67%) | 0.844 (40%) |
| bug-fix | 15 | 1.000 (100%) | 0.999 (98%) |
| feature-add | 15 | 1.000 (100%) | 1.000 (100%) |
| repair-to-green | 15 | 1.000 (100%) | 1.000 (100%) |

## Per-task (graded — mean score over trials)

| task | type | glm-5.2 | minimax-m3 |
|---|---|--:|--:|
| backoff | bug-fix | 1.00 | 1.00 |
| base62 | bug-fix | 1.00 | 1.00 |
| budgetrules | implement | 1.00 | 0.96 |
| cachelab | implement | 0.97 | 0.90 |
| cachetags | feature-add | 1.00 | 1.00 |
| calceval | repair-to-green | 1.00 | 1.00 |
| cellsim | implement | 0.97 | 0.97 |
| condschema | feature-add | 1.00 | 1.00 |
| confstack | implement | 1.00 | 0.97 |
| cronmatch | repair-to-green | 1.00 | 1.00 |
| cronsim | implement | 0.86 | 0.90 |
| csvgroupby | feature-add | 1.00 | 1.00 |
| csvparse | bug-fix | 1.00 | 1.00 |
| csvql | implement | 1.00 | 0.95 |
| cursorpage | feature-add | 1.00 | 1.00 |
| datespan | repair-to-green | 1.00 | 1.00 |
| decimalfmt | repair-to-green | 1.00 | 1.00 |
| deepget | bug-fix | 1.00 | 1.00 |
| eventbus | feature-add | 1.00 | 1.00 |
| flagwise | implement | 1.00 | 0.95 |
| graphbip | repair-to-green | 1.00 | 1.00 |
| graphpath | bug-fix | 1.00 | 1.00 |
| hsm | feature-add | 1.00 | 1.00 |
| intervalmerge | bug-fix | 1.00 | 1.00 |
| jsonquery | repair-to-green | 1.00 | 1.00 |
| kvtxn | feature-add | 1.00 | 1.00 |
| ledgercore | implement | 0.64 | 0.67 |
| ledgerfix | bug-fix | 1.00 | 1.00 |
| lrucache | bug-fix | 1.00 | 1.00 |
| luhn | bug-fix | 1.00 | 1.00 |
| microapi | implement | 0.98 | 0.69 |
| middleware | feature-add | 1.00 | 1.00 |
| migrato | implement | 0.81 | 1.00 |
| movavg | bug-fix | 1.00 | 1.00 |
| orderplane | implement | 0.69 | 0.58 |
| patchwise | implement | 0.62 | 1.00 |
| pctstats | bug-fix | 1.00 | 1.00 |
| permgen | repair-to-green | 1.00 | 1.00 |
| querygroup | feature-add | 1.00 | 1.00 |
| repaircalc | repair-to-green | 1.00 | 1.00 |
| repairmoney | repair-to-green | 1.00 | 1.00 |
| repairpager | repair-to-green | 1.00 | 1.00 |
| repairspans | repair-to-green | 1.00 | 1.00 |
| romanio | repair-to-green | 1.00 | 1.00 |
| routerwild | feature-add | 1.00 | 1.00 |
| rrulelite | repair-to-green | 1.00 | 1.00 |
| schemaoneof | feature-add | 1.00 | 1.00 |
| searchprefix | feature-add | 1.00 | 1.00 |
| semvercmp | bug-fix | 1.00 | 1.00 |
| serialhook | feature-add | 1.00 | 1.00 |
| slotfinder | implement | 1.00 | 0.86 |
| statichisel | implement | 1.00 | 0.93 |
| textflow | repair-to-green | 1.00 | 1.00 |
| textwidth | bug-fix | 1.00 | 1.00 |
| ticketflow | implement | 1.00 | 0.33 |
| tierlimit | feature-add | 1.00 | 1.00 |
| tmploop | feature-add | 1.00 | 1.00 |
| tokenbucket | bug-fix | 1.00 | 0.98 |
| ttlcache | bug-fix | 1.00 | 1.00 |
| unitconv | repair-to-green | 1.00 | 1.00 |

## Ambiguous-spec (observed — NOT scored)

_These probe how each model interprets an underspecified brief; there is no right answer, so no score. The model's solution + transcript are persisted per run for the qualitative read. Latency / tokens / cost below are descriptive only._

| task | glm-5.2 (lat / tok / $) | minimax-m3 (lat / tok / $) |
|---|--:|--:|
| auditlog | 102s / 38,842 / $0.277 | 57s / 91,489 / $0.112 |
| cachelayer | 118s / 118,234 / $0.528 | 60s / 93,760 / $0.118 |
| docsearch | 126s / 102,124 / $0.500 | 66s / 180,961 / $0.176 |
| featureflags | 102s / 57,717 / $0.344 | 53s / 124,305 / $0.138 |
| formvalidate | 146s / 134,671 / $0.647 | 97s / 294,980 / $0.270 |
| jobflow | 152s / 165,844 / $0.693 | 54s / 73,671 / $0.107 |
| mergeconf | 106s / 69,844 / $0.368 | 55s / 133,118 / $0.131 |
| notifyhub | 132s / 65,113 / $0.407 | 54s / 107,123 / $0.131 |
| ratelimit | 99s / 71,039 / $0.355 | 56s / 114,184 / $0.117 |
| retryflow | 115s / 68,844 / $0.395 | 65s / 144,071 / $0.142 |
| scheduler | 125s / 86,485 / $0.428 | 35s / 52,430 / $0.083 |
| workflow | 107s / 66,101 / $0.372 | 56s / 123,851 / $0.136 |
