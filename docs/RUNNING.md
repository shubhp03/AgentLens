# Running AgentLens locally

Four ways to run it. Each says what has actually been verified.

| Path | What you get | Needs | Status |
|---|---|---|---|
| 1. Demo UI | The dashboard on **synthetic** data | Node 22 (or Docker) | Verified (build + browser smoke) |
| 2. Eval harness, real agent | Real LLM + real SEC data → `report.md` with real numbers | API key, SEC user-agent, pricing | Code verified offline; **never run live** |
| 3. Agent service alone | HTTP `POST /v1/agent/run` streaming NDJSON | same as 2 | Boots + streams under test (scripted LLM); **never run live** |
| 4. Full stack (gateway + DB + UI on live data) | Everything | Postgres 16 + path 2's keys | Gateway + e2e verified with a scripted LLM on real sockets; Docker images and live LLM **not** verified |

## 0. Prerequisites
Python 3.12, [uv](https://docs.astral.sh/uv/), Node ≥ 22.12 (for the UI), Docker (optional; for Postgres or the compose stack).

```bash
make setup          # installs the Python workspace (agent, tools, evals)
make test           # 205 tests (incl. gateway on embedded Postgres), no network, no spend
make stack-smoke    # gateway + agent + MCP + Postgres over real sockets, scripted LLM
```

## 1. See the UI (no keys, no spend)
```bash
make dashboard-demo      # builds the demo bundle into dashboard/dist/dashboard/browser
make dashboard-serve     # http://localhost:4300
# or with Docker:
docker compose -f docker-compose.demo.yml up --build    # http://localhost:4200
```
Everything shown is **synthetic** (fictional companies, fake models). It exists so the UI can be explored and screenshotted
without spending anything. Never present it as results.

## 2. Run the real agent through the eval harness (this is the valuable path)
No gateway, no database, no Docker. The CLI runs the agent in-process and spawns the tool servers itself.

```bash
cp .env.example .env
#  edit .env: ANTHROPIC_API_KEY=...   SEC_USER_AGENT="Your Name you@example.com"
set -a; source .env; set +a                      # the CLI reads the environment; it does not load .env itself

cp config/pricing.example.yaml config/pricing.yaml
#  fill input/output $ per million tokens for each model FROM THE PROVIDER'S PRICING PAGE, set as_of.
#  The loader refuses empty/zero prices on purpose (a silent $0 would poison every cost metric).
#  The keys must match config/agents/*.yaml, e.g. "anthropic:claude-sonnet-5-5".

uv run agentlens-eval validate-dataset evals/datasets/golden_v0.jsonl
```

**Pilot first (up to $2).** Proves the plumbing end to end before you invest in the golden set:
```bash
uv run agentlens-eval run --dataset evals/datasets/golden_v0.jsonl \
  --config config/agents/baseline.yaml --repeats 1 --limit 3 --budget-usd 2 --label pilot
# -> eval-runs/pilot/{results.jsonl, results.meta.json, traces/, report.md}
```
Open a file in `eval-runs/pilot/traces/` to read the actual trace. This is also your **TL-04** live check of the EDGAR tools:
expect to find and fix parsing problems on real filings — that is normal and is what the fixture tests are for.

**Publish a live eval into the dashboard.** Start the full stack first (section 4), then use gateway mode. The gateway stores
each run and trace, and the eval run/results appear in the dashboard. `ADMIN_API_KEY` is read from `.env`; it is never put
on the command line.
```bash
set -a; source .env; set +a
export AGENTLENS_GATEWAY_URL=http://localhost:8080
uv run agentlens-eval run --dataset evals/datasets/golden_v0.jsonl \
  --config config/agents/baseline.yaml --verified-only --repeats 1 --budget-usd 2 --label pilot-live
```
Gateway mode keeps the same local report files and additionally registers the dataset/config/eval run, stores linked traces,
and uploads the per-case scores. Case expectations must be complete before execution; the CLI rejects incomplete numeric or
text gold before making model calls. The seed dataset currently has no verified cases, so `--verified-only` will report
`no cases to run` until you verify cases. Do not substitute draft gold values or use draft scores as published results.

**Real evaluation** (only after you have ≥ 60 hand-verified cases, EV-02):
```bash
for cfg in no_tools baseline small_model; do
  uv run agentlens-eval run --dataset evals/datasets/golden_v0.jsonl --verified-only \
    --config config/agents/$cfg.yaml --repeats 3 --budget-usd 15 --label $cfg --out eval-runs/$cfg
done
uv run agentlens-eval compare eval-runs/baseline/results.jsonl eval-runs/small_model/results.jsonl
```
- `--budget-usd` is a hard cap and is **required**. Exit code 3 means the budget stopped the run; re-run the same command with
  a larger budget to resume (finished cases are skipped).
- Keep a final `test`-split run for the end; don't tune against it (`--split test`).
- Only numbers from these `report.md` files may go in the README, a LinkedIn post, or your resume.

## 3. Run the agent service on its own
```bash
set -a; source .env; set +a
export MCP_SERVERS='{"edgar":"stdio:edgar-mcp","calc":"stdio:calc-mcp"}'
uv run uvicorn agentlens_agent.main:app --port 8200
curl localhost:8200/healthz
curl -N -X POST localhost:8200/v1/agent/run -H 'content-type: application/json' -d '{
  "run_id":"11111111-1111-4111-8111-111111111111",
  "question":"What was Apple total net sales in fiscal 2023?",
  "config":{"name":"baseline","provider":"anthropic","model":"claude-sonnet-5-5",
            "tool_allowlist":["resolve_company","list_filings","search_xbrl_concepts","get_financial_facts","get_filing_section","calculate"],
            "max_cost_usd":0.5}}'
```
You get one JSON event per line: `run.started`, `span.finished`…, root `agent` span, `run.finished`.
Configuration errors are clean HTTP errors: missing pricing file → 503, model without a price or unsupported provider → 422.

## 4. Full stack (gateway + Postgres + UI on live data)
The gateway is Python (FastAPI). Two ways to run it:

**4a. Docker (one command; images not yet built by the author, see status above)**
```bash
cp .env.example .env     # set SEC_USER_AGENT, ANTHROPIC_API_KEY, ADMIN_API_KEY (openssl rand -hex 32)
cp config/pricing.example.yaml config/pricing.yaml   # fill from the provider's pricing page
docker compose up --build
# dashboard http://localhost:4200  ·  gateway http://localhost:8080/healthz  ·  sign in with ADMIN_API_KEY
```

**4b. Without Docker (what the author's tests actually exercise)**
```bash
# 1) Postgres 16 (any): e.g.  docker run -d --name al-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=agentlens -p 5432:5432 postgres:16
export SEC_USER_AGENT="Your Name you@example.com" ANTHROPIC_API_KEY=... PRICING_FILE=config/pricing.yaml
export MCP_SERVERS='{"edgar":"stdio:edgar-mcp","calc":"stdio:calc-mcp"}'
uv run uvicorn agentlens_agent.main:app --port 8200 &                        # agent service
export DATABASE_URL=postgresql://postgres:pw@localhost:5432/agentlens ADMIN_API_KEY=$(openssl rand -hex 32)
export AGENT_BASE_URL=http://localhost:8200
uv run uvicorn agentlens_gateway.main:app --port 8080 &                      # gateway (migrates the DB on start)
cd dashboard && npm ci && npm start                                          # http://localhost:4200, sign in with ADMIN_API_KEY
```
Verified: `make gateway-test` (84 tests, real PostgreSQL) and `make stack-smoke` (gateway + agent + real MCP calculator + Postgres over real
sockets, **scripted LLM**). **Not verified:** the Docker images, the dashboard against the live gateway, any real LLM/SEC call.
Try the API without the UI:
```bash
curl -s localhost:8080/api/v1/runs -H "X-API-Key: $ADMIN_API_KEY" -H 'content-type: application/json' -d @run.json   # see contracts/openapi.yaml
```

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `SEC_USER_AGENT is required` | Set it (name + email). It is forwarded to the stdio tool process automatically. |
| `no price configured for anthropic:…` | Add the model to `config/pricing.yaml`; the key must match `provider:model` exactly. |
| Every EDGAR call returns an error | SEC rate-limits/blocks bad or missing user-agents (HTTP 403). Fix the user-agent; the cache is in `.cache/edgar`. |
| `no cases to run` | `--verified-only` with no verified cases, or an over-narrow `--split`. |
| HTTP MCP servers return 403/421 in Docker | Host allow-list (DNS-rebinding protection) in the MCP SDK; see the note in `tools/src/agentlens_tools/_run.py`. |
| `playwright` / UI smoke fails | `pip install playwright && playwright install chromium`, or set `CHROMIUM_PATH`. |
