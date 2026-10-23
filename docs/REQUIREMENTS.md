# AgentLens — Requirements and Feature Plan

How to use this file
- Each row is one handoff-sized unit of work for you or an AI coding assistant. **One requirement ID per change.**
- **P0** = needed for v1.0 · **P1** = should have, cut second · **P2** = stretch, cut first.
- **Owner:** **You** = write it yourself (interview-defensible core) · **AI** = delegate, then review and run it yourself · **Both** = AI drafts, you own the decisions.
- **Status:** ☑ done in scaffold (offline-tested) · ◐ scaffolded, needs live/integration verification · ☐ to do.
- "AC" = acceptance criteria. A requirement is done only when its AC are met by a test or a documented manual check.

Releases
- **Release A (v0.1, results-first):** agent + tools + eval CLI in *direct mode* (no gateway), producing a real results table. Already LinkedIn-worthy.
- **Release B (v1.0):** gateway + dashboard + CI nightly gate + replay demo.

## A. Tool layer (`tools/`)
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| TL-01 | P0 | `calculate` tool: safe arithmetic incl. `pct_change`, `cagr` | Rejects non-arithmetic AST, huge exponents, div-by-zero; thousands separators work; argument commas unaffected (`tools/tests/test_calc.py`) | AI | ☑ |
| TL-02 | P0 | EDGAR parsing: CIK normalize, company match, filings list, concept search, fact selection by period dates with de-dup | Unit tests on fixtures incl. `fy`-of-filing gotcha, restatement de-dup, quarterly exclusion | AI | ☑ |
| TL-03 | P0 | EDGAR HTTP client: User-Agent required, throttle, disk cache, retry | MockTransport tests: UA sent, cache hit avoids request, 503 retried | AI | ☑ |
| TL-04 | P0 | **Live verification** of all five EDGAR tools against real SEC data (Apple, Microsoft, Alphabet) | Manual script/transcript saved in `docs/reports/tl-live-check.md`; any parse failures turned into fixture tests | You | ☐ |
| TL-05 | P0 | `get_filing_section` extraction quality on ≥5 real 10-Ks across companies | Items 1, 1A, 7, 8 extract non-empty and start at the real heading; failures documented | Both | ◐ |
| TL-06 | P1 | Quarterly handling: derive Q4 = FY − (Q1+Q2+Q3) tool-side or document as unsupported | Behaviour documented in tool docstring + 1 eval case | Both | ☐ |
| TL-07 | P1 | MCP servers run over streamable HTTP in compose | `docker compose up edgar-mcp calc-mcp`; a client lists tools; resolve any host-allow-list (403/421) issue | AI | ◐ |
| TL-08 | P2 | Section chunk search (`find_in_filing(query)`) for long items | Returns top-k passages with offsets; tested | AI | ☐ |

## B. Agent runtime (`agent/`)
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| AG-01 | P0 | **Agent loop** `AgentRunner.run` per `loop.py` contract | All tests in `agent/tests/test_loop.py` pass and xfail markers removed; invariants hold (one `run.finished`, strictly increasing `seq`, unique span ids) — implemented + tested (agent suite) | **You** | ☑ |
| AG-02 | P0 | Provider retry: ≤3 attempts on retryable `LLMError`, exp. backoff + jitter, one span per attempt | Test with `ScriptedLLM` raising retryable then success; non-retryable → `failed/provider_error`; sleep injectable for fast tests — implemented + tested (agent suite) | You | ☑ |
| AG-03 | P0 | Budget enforcement: `max_steps`, `max_cost_usd` | Tests for `step_limit` and `budget_exceeded`; cost per llm_call span from pricing table; missing price is a loud error — implemented + tested (agent suite) | You | ☑ |
| AG-04 | P1 | OpenAI adapter implementing `LLMClient` | Translation unit tests like `test_anthropic_adapter.py`; wired in `wiring.build_llm` | AI | ☐ |
| AG-05 | P0 | **MCP-backed `ToolRegistry`** (`mcp_registry.py`) on MCP SDK **v2** | Connects to both servers, lists tools, `specs(allowlist)`, `call()` maps errors/timeouts to `ToolOutput(is_error=True)`, reconnects lazily; integration test spawns calc-mcp over stdio — implemented + tested (agent suite) | **You** | ☑ |
| AG-06 | P0 | Tool output truncation (12k chars) with visible marker + span attributes | Oversized output truncated, `attributes.truncated=true`, `original_size` set — implemented + tested (agent suite) | You | ☑ |
| AG-07 | P0 | `submit_answer` validation + one repair attempt | Invalid args → `is_error` tool result and one more LLM turn; second failure → completed/answered with `format_error` marker — implemented + tested (agent suite) | You | ☑ |
| AG-08 | P0 | Concurrent tool execution | Two tool calls in one turn run concurrently (sleeping fake tools); spans each recorded — implemented + tested (agent suite) | You | ☑ |
| AG-09 | P0 | `/v1/agent/run` streams NDJSON; client disconnect cancels the run | Manual curl transcript + test that closing the stream stops LLM calls | Both | ◐ |
| AG-10 | P0 | Anthropic adapter live check | One real call with a tool round-trip succeeds; usage fields populated; pricing yields non-zero cost | You | ◐ |
| AG-11 | P0 | Prompt versioning | `system_prompt_sha` recorded per config; changing the prompt file changes the sha (tested) | AI | ☑ |
| AG-12 | P1 | `system_v2` prompt variant for ablation | Distinct file; ablation config added; documented hypothesis | You | ☐ |
| AG-13 | P1 | Root-span attributes: `no_submit`, retries, truncations summarized | Visible in dashboard span inspector — root attrs: steps, retries, truncations, invalid_answer, no_submit | AI | ☑ |

## C. Gateway (`gateway/`)
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| GW-01 | P0 | Bootstrap gateway (FastAPI + psycopg 3); forward-only migration runner applies `V1`/`V2` (raw SQL, no ORM) | App starts against Postgres 16; migrations idempotent, checksum-verified, atomic — `test_units.py` migration tests + every API test run on real PostgreSQL 16 (pgserver) | AI | ☑ |
| GW-02 | P0 | API-key auth filter (`X-API-Key` → SHA-256 lookup); admin key seeded from env; refuses to start if unset | 401 without/invalid/revoked key; `/healthz` open; key never logged | AI | ☑ |
| GW-03 | P1 | Per-key rate limiting on `POST /runs` | 429 + `Retry-After` after N/min — hand-rolled token bucket (Bucket4j dropped with Java), unit + API tested | AI | ☑ |
| GW-04 | P0 | Daily cost cap guard | New runs rejected 429 + `Retry-After` (to UTC midnight) when spend + reserved in-flight budgets ≥ cap — API tested | AI | ☑ |
| GW-05 | P0 | `POST /configs` idempotent via canonical-JSON `config_hash` | Same content → 200 + same id; different → 201; hash stable under key order — tested | Both | ☑ |
| GW-06 | P0 | `POST /runs` → persist `queued`, dispatch via `RunExecutor` (asyncio task per run + semaphore), 202 | Concurrency never exceeds `MAX_CONCURRENT_RUNS`; validation (length, config xor config_id) → 400 problem+json — tested | You | ☑ |
| GW-07 | P0 | Consume agent NDJSON; idempotent span upserts; update run totals/status | Replaying the same span twice leaves identical DB state (`test_span_upsert_is_idempotent...`); real NDJSON parsed over a socket | **You** | ☑ |
| GW-08 | P0 | SSE `/runs/{id}/events` with replay-then-live | Late subscriber receives earlier spans then live ones without gaps or duplicates; closes after `run.finished` — tested over a real socket | Both | ☑ |
| GW-09 | P1 | `DELETE /runs/{id}` cancels | Running run ends `cancelled`; terminal run → 409 | AI | ☑ |
| GW-10 | P0 | Run wall-clock timeout | Hung agent → `failed/timeout` within configured time (slow stub) | AI | ☑ |
| GW-11 | P1 | Startup crash recovery | Rows left `running` → `failed/gateway_restart` on boot (integration test) | AI | ☑ |
| GW-12 | P0 | `GET /runs` (keyset pagination, filters) and `GET /runs/{id}?include=spans` | Stable ordering under inserts; cursor round-trips; spans ordered by `seq` — tested | AI | ☑ |
| GW-13 | P0 | Dataset upsert with `content_hash` conflict detection | Same name+version+hash → 200; changed content under same version → 409 | AI | ☑ |
| GW-14 | P0 | Eval run create/get/list/complete/patch | Metrics via `eval_run_metrics` view + Wilson CI; failure breakdown present | AI | ☑ |
| GW-15 | P0 | `POST /eval-runs/{id}/results` batch, idempotent | Duplicate `(case_key, repeat_idx)` upserts; failed result without category → 400 (mirrors DB CHECK) | AI | ☑ |
| GW-16 | P0 | `GET /eval-runs/compare` | Uses the same `agentlens_evals` statistics code as the CLI (no second implementation) — tested | Both | ☑ |
| GW-17 | P1 | `GET /stats/overview` | Correct against seeded data | AI | ☑ |
| GW-18 | P0 | RFC 9457 `problem+json` error handling | Every 4xx/5xx uses it; no stack traces leak | AI | ☑ |
| GW-19 | P1 | CORS allow-list from env | Disallowed origin blocked in test | AI | ☑ |
| GW-20 | P1 | Contract test: responses conform to `contracts/openapi.yaml` | Route coverage both ways + response schema validation (`test_contract.py`); CI fails on drift | AI | ☑ |

## D. Evaluation (`evals/`)
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| EV-01 | P0 | Dataset schema + loader (JSONL), draft/verified states | `agentlens-eval validate-dataset` works; verified cases need provenance (`test_case_dataset.py`) | AI | ☑ |
| EV-02 | P0 | **Author the golden set**: ≥60 verified cases (target 80) per distribution in EVAL_DESIGN §2 | Every verified case has provenance (accession/concept, date read, verified_by); periods pinned; unanswerables included | **You** | ☐ |
| EV-03 | P1 | Gold-candidate generator: from ticker+concept+period emit a draft case from XBRL | Output validates against schema; always `status: draft` | AI | ☐ |
| EV-04 | P0 | Deterministic scorers: numeric (unit/period), text, abstain, citation validity, numeric grounding, tool-use metrics | Unit tests incl. unit-scale and period edge cases | AI | ☑ |
| EV-05 | P0 | Runner **direct mode**: dataset × config × repeats against the agent service; writes `results.jsonl` + traces | Bounded concurrency; `--budget-usd` aborts cleanly; resumable (skips completed case/repeat pairs) — implemented + tested; run with a real key to produce results | Both | ☑ |
| EV-06 | P0 | **Failure classifier**: trace + answer + expected → `failure_category` per EVAL_DESIGN §5 | Rule table implemented; ≥1 unit test per category using synthetic traces; every failed result gets exactly one category — implemented + tested; run with a real key to produce results | **You** | ☑ |
| EV-07 | P0 | Repeats and aggregation: case-level pass = strict majority; also report consistency (all repeats agree) | Matches `compare` conventions; tested — implemented + tested; run with a real key to produce results | Both | ☑ |
| EV-08 | P0 | Report generator: markdown results table (pass rate ± CI, cost/success, p95, top failures) | Output pasted into README without hand-editing — implemented + tested; run with a real key to produce results | AI | ☑ |
| EV-09 | P0 | Control arm: `no_tools` config in every report | Included by default in ablation runs | You | ◐ |
| EV-10 | P1 | Gateway mode: runner posts datasets/configs/eval-runs/results via REST | Same results as direct mode on a smoke set — NOT built: runner is direct-mode only | AI | ☐ |
| EV-11 | P1 | Regression gate (`--gate`): baseline paired test + absolute floors; exit code 1 on failure | Tested with synthetic baseline/candidate (`compare.gate`) | AI | ◐ |
| EV-12 | P1 | Replay fixtures for CI: record LLM+tool I/O once, replay deterministically | CI eval-smoke runs offline, zero cost, deterministic | Both | ☐ |
| EV-13 | P2 | LLM judge for `text_extract` with rubric; calibrated against ≥20 human labels (report agreement) | Agreement reported; judge never overrides deterministic scorers | Both | ☐ |
| EV-14 | P2 | Prompt-injection mini-suite (≥5 cases with malicious text in a fixture filing) | Measured and reported even if the result is bad | Both | ☐ |
| EV-15 | P1 | Ablation matrix runner: models × prompt versions × {tools on/off} | One command produces the comparison table | AI | ☐ |

## E. Dashboard (`dashboard/`)
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| UI-01 | P0 | Bootstrap Angular app + typed client generated from `contracts/openapi.yaml` | Builds; no hand-written API models — built; build + 22 vitest + 10-route browser smoke pass (demo data) | AI | ☑ |
| UI-02 | P0 | API-key login + interceptor | Wrong key → clear error; key not persisted beyond session — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-03 | P0 | Runs list (filters, pagination, status/cost/latency) | Matches API; empty/loading/error states — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-04 | P0 | **Run detail with trace waterfall** + span inspector | 30-span run readable; llm vs tool colours; errors highlighted; JSON viewer; answer card shows value/unit/period/citations — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | **Both** | ◐ |
| UI-05 | P1 | Live run updates via SSE | Spans appear as they stream; reconnect-safe — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-06 | P1 | Playground: ask a question with a chosen config | Navigates to the live run view — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-07 | P0 | Eval runs list (pass rate ± CI, cost/success, p95, baseline badge) | Sort/filter; CI shown — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-08 | P0 | Eval run detail: failure-category chart, per-category pass rates, failed-only results table with link to trace | Click-through works — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-09 | P0 | **Compare view**: metrics + CIs, McNemar p, regressed/improved cases, cost-vs-quality scatter | Numbers match API/Python reference — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | **Both** | ◐ |
| UI-10 | P1 | Configs view | Shows prompt sha, tools, hash — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-11 | P1 | Overview: spend vs cap, runs today, latest baseline | Correct vs seeded data — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-12 | P0 | **Replay/demo mode** (static recorded runs; no live LLM) | Public site works with zero spend; live mode needs key — built; build + 22 vitest + 10-route browser smoke pass (demo data) | Both | ☑ |
| UI-13 | P1 | Accessibility basics (keyboard nav, contrast, labels) | Axe/Lighthouse pass on main views — built and smoke-tested on SYNTHETIC demo data; never run against a live gateway | AI | ◐ |
| UI-14 | P1 | Component tests for waterfall layout math and compare view | Run in CI — built; build + 22 vitest + 10-route browser smoke pass (demo data) | AI | ☑ |

## F. Platform / DevOps
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| OPS-01 | P0 | `docker compose up` runs the whole stack | Fresh clone + `.env` → healthy stack; documented — compose validated with `docker compose config`; images NEVER built (no daemon in authoring sandbox); `make stack-smoke` proves the topology over real sockets | Both | ◐ |
| OPS-02 | P0 | CI: Python lint + tests + contract validation | Green on PR | AI | ☑ |
| OPS-03 | P0 | CI: gateway tests + stack smoke (Python job), dashboard build | Green on PR — jobs defined; workflow has never run on GitHub | AI | ◐ |
| OPS-04 | P1 | Nightly live eval with budget + regression gate | Artifacts uploaded; fails the run on regression — workflow rewritten for direct mode; needs secrets + a verified golden set; never run | Both | ◐ |
| OPS-05 | P1 | Deploy demo to a small VM behind TLS (Caddy) | Public URL serves replay mode; live mode key-gated — Pages workflow + `scripts/build_pages.sh` (verified under a subpath) and VM+Caddy files (`deploy/`, unrun); see docs/DEPLOY.md | Both | ☐ |
| OPS-06 | P0 | Commit `uv.lock`; pin Docker base/tool versions; enable dependency alerts | Reproducible builds | You | ☐ |
| OPS-07 | P0 | `config/pricing.yaml` filled from provider pages with `as_of` date | Loader accepts it; date recorded in reports | You | ☐ |
| OPS-08 | P0 | Employer-policy check before first public push (outside activity, IP, open source) | Written confirmation/notes kept privately — owner states personal learning project, employer indifferent; keep your own note | **You** | ☑ |

## G. Docs and launch
| ID | P | Requirement | AC | Owner | Status |
|---|---|---|---|---|---|
| DOC-01 | P0 | README results table from real runs | No placeholder `TBD` left; reproduction command included | You | ☐ |
| DOC-02 | P0 | Architecture diagram + trace-waterfall screenshot (LinkedIn assets) | Exported PNG/SVG in `docs/img/` | You | ☐ |
| DOC-03 | P1 | 60–90 s demo video | Shows ask → trace → eval compare | You | ☐ |
| DOC-04 | P0 | Technical write-up: "What broke when I evaluated my agent" (failure taxonomy + fixes) | Publishable; every number traceable to a report | You | ☐ |
| DOC-05 | P0 | Two LinkedIn posts (v0.1 results; v1.0 platform) | Per BUILD_PLAN launch playbook | You | ☐ |
| DOC-06 | P0 | Resume bullets from measured numbers only | Each number traceable to a report in `docs/reports/` | You | ☐ |
| DOC-07 | P0 | Honest Limitations section stays in README | Present at release | You | ☑ |

## H. Non-functional requirements
| ID | P | Requirement | AC |
|---|---|---|---|
| NFR-01 | P0 | No live model calls outside nightly eval | CI config + `AGENTS.md` rule; unit tests use fakes |
| NFR-02 | P0 | Reproducibility | Every eval result links to config hash, prompt sha, dataset content hash, git sha |
| NFR-03 | P0 | Secrets never in repo/logs | Secret scan in CI (e.g. gitleaks) or documented manual check |
| NFR-04 | P1 | Gateway overhead p95 < 50 ms (non-streaming) | Simple load check recorded in `docs/reports/` |
| NFR-05 | P1 | Structured logs carry `run_id`; no API keys or full tool outputs at INFO | Log review test |
| NFR-06 | P0 | Tests for money/maths: cost accounting, statistics, unit scaling | Present and tested (Python ☑); the gateway reuses the same statistics code, so there is no second implementation to drift |

## Milestones and exit criteria
Dates assume ~10 hrs/week on the project alongside work and the MS; slip them rather than cutting quality gates.

| Milestone | Target | Scope | Exit criteria |
|---|---|---|---|
| **M0 Foundations** | Oct 18–21 | Scaffold running, OPS-07, OPS-08, TL-04, AG-10 | Real EDGAR + one real LLM call work from your machine; policy check done |
| **M1 Agent works** | ~Oct 27 | AG-01..03, 05..09, TL-05 | Real question answered end-to-end via the agent service with a valid trace |
| **M2 Release A (v0.1)** | ~Nov 8 | EV-02 (≥40 verified), EV-05..09, 11 | README results table from real runs, repo public, LinkedIn post #1 |
| **M3 Platform** | ~Nov 29 | GW-01..08, 12..16, UI-01..04, 07..09 | Eval + trace visible in the dashboard; compare view correct |
| **M4 Release B (v1.0)** | ~Dec 13 (hard stop) | Remaining P0, chosen P1s, OPS-04/05, docs | Demo URL live in replay mode, write-up + post #2 published |

**Cut line:** if behind, cut in this order: P2 → UI-13/14 → OPS-05 (ship screenshots + video instead of a live URL) → GW-03/09/11/19 → EV-12/15. Never cut: EV-02 quality, EV-06, AG-01, UI-04, UI-09, DOC-04.

## Definition of Done (project)
All P0 ☑ · CI green · README results come from real runs and name the commit · limitations stated · no secrets in history ·
you can whiteboard the agent loop, the event protocol, the eval statistics and one real failure story without notes.

## Out of scope (v1)
Multi-tenancy/RBAC, fine-tuning, real-time market data, write-capable tools, non-US filers, production SLAs.
