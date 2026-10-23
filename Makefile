.PHONY: setup lint test contracts up down logs bootstrap-dashboard eval-validate schema-smoke gen-demo dashboard-test dashboard-demo dashboard-serve ui-smoke gateway-test stack-smoke pages demo-docker eval-pilot

setup:
	uv sync --all-packages

lint:
	uv run ruff check . && uv run ruff format --check .

test:
	uv run pytest -q

contracts:        ## validate OpenAPI + JSON schemas are in sync + migrations exist
	uv run python scripts/validate_contracts.py

schema-smoke:     ## real-Postgres check of the migrations (Docker-free, via pgserver)
	uv run python scripts/smoke_schema.py

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

bootstrap-dashboard:
	bash scripts/bootstrap_dashboard.sh

eval-validate:
	uv run agentlens-eval validate-dataset evals/datasets/golden_v0.jsonl

gen-demo:         ## regenerate SYNTHETIC demo data for the dashboard
	uv run python scripts/gen_demo_data.py

dashboard-test:
	cd dashboard && npm ci && npm run test:ci

dashboard-demo:
	cd dashboard && npm ci && npm run build:demo

dashboard-serve:  ## serve the built demo on http://localhost:4300
	cd dashboard/dist/dashboard/browser && python3 -m http.server 4300

stack-smoke:      ## real-socket e2e: gateway + agent + MCP + Postgres (scripted LLM)
	uv run python scripts/stack_smoke.py

ui-smoke:         ## headless-browser smoke test of the demo build
	python3 scripts/ui_smoke.py   # needs playwright + chromium (pip install playwright && playwright install chromium)

gateway-test:     ## gateway tests against a real Postgres (no Docker needed)
	uv run pytest gateway/tests -q

pages:            ## demo build for GitHub Pages (BASE_HREF=/<repo>/)
	BASE_HREF=$${BASE_HREF:-/} bash scripts/build_pages.sh

demo-docker:      ## dashboard demo in Docker on :4200
	docker compose -f docker-compose.demo.yml up --build

eval-pilot:       ## $2 live pilot budget: 3 cases x 1 repeat (needs .env sourced + config/pricing.yaml)
	uv run agentlens-eval run --dataset evals/datasets/golden_v0.jsonl --config config/agents/baseline.yaml \
	  --repeats 1 --limit 3 --budget-usd 2 --label pilot
