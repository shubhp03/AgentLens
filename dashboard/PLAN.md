> **Status: built** (Angular 21, standalone + signals). Verified: production build, 22 vitest tests, headless-Chromium smoke of all 10 routes on SYNTHETIC demo data. Never run against a live gateway.

# dashboard/ — Angular app plan

Generate with `make bootstrap-dashboard` (standalone components, signals, SCSS, no SSR). Chart.js for charts.
Needs Node >= 20.19 / 22.12 / 24 (Angular 21).
Auth: a login screen takes the API key, keeps it in sessionStorage only, adds `X-API-Key` via an HTTP interceptor.
(Portfolio simplification; OIDC would be the production answer — say so in the README.)
Generate a typed client from `../contracts/openapi.yaml` (e.g. `openapi-typescript`) rather than hand-writing models.

## Routes → views
| Route | View | Key elements |
|---|---|---|
| `/` | Overview | runs today, spend vs daily cap, latest baseline pass rate (with CI), recent runs |
| `/runs` | Runs list | filters (status/source/config), keyset pagination, status chips, cost, latency |
| `/runs/:id` | **Run detail (hero screen)** | question/answer card (value, unit, period, citations), **trace waterfall** (span bars by time; llm vs tool colours; error highlighting), span inspector (input/output JSON, tokens, cost), live updates via SSE while running |
| `/ask` | Playground | submit a question with a chosen config, watch the trace stream live |
| `/evals` | Eval runs | table with pass rate ± CI, cost/success, p95 latency, config, git sha, baseline badge |
| `/evals/:id` | Eval run detail | failure-category bar chart, per-category pass rates, results table (filter: failed only), click-through to the run's trace |
| `/evals/compare?base=&candidate=` | **Compare (second hero screen)** | side-by-side metrics with CIs, paired McNemar p-value, regressed/improved case list, cost-vs-quality scatter |
| `/configs` | Configs | immutable snapshots, prompt sha, tool allowlist |

## Component/service structure (`src/app`)
```
core/        api-client (generated), auth.interceptor, auth.service, sse.service (EventSource → signal), error handling
shared/      status-chip, cost-badge, json-viewer, duration-pipe, empty-state, chart-wrapper (Chart.js)
features/    overview/ runs/ run-detail/ (trace-waterfall, span-inspector) playground/ evals/ eval-detail/ compare/ configs/
```

## Non-negotiables for the demo
- Trace waterfall renders a 30-span run smoothly and is readable on a laptop screen (this is the LinkedIn screenshot).
- Empty/loading/error states on every view. Keyboard-accessible tables. Dark mode optional (P2).
- A **read-only demo mode** (recorded runs served statically) so visitors can explore without live LLM spend.
