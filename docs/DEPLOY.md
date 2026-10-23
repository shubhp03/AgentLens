# Deploying AgentLens

Pick by what you want people to see. Cost safety first: **never expose a live LLM endpoint to the public.**

| Option | Shows | Cost | Backend? | Status |
|---|---|---|---|---|
| A. GitHub Pages | Dashboard on synthetic demo data (+ link to your real report) | $0 | none | **Build + deep links verified in a browser**; the workflow itself has not run |
| B. One VM + Docker + Caddy (HTTPS) | Same demo site on your own domain | a few $/month | none | Compose files validated (`docker compose config`); never run |
| C. Full stack on a VM | Live runs through the gateway | VM + LLM spend | gateway | Gateway tested; overlay validated with `docker compose config`; images never built or run |
| D. Nightly eval in CI | Regression gate on real LLM runs | LLM spend (capped) | none | Workflow written; never run |

For a portfolio, **A + a screenshot/video + the real `report.md`** is the best effort-to-impact. A public live-LLM demo invites abuse
and bills; if you ever do C, keep live mode behind an API key with the daily cost cap on.

## A. GitHub Pages (recommended first deploy)
1. Push the repo to GitHub (public). **Before the first public push**, check no secrets are committed: `.env` and
   `config/pricing.yaml` are git-ignored; run `git status` and `git log -p | grep -i api_key` as a sanity check.
2. Repo → Settings → Pages → Source: **GitHub Actions**.
3. Push to `main` (touching `dashboard/**`) or run the `pages` workflow manually. URL: `https://<user>.github.io/<repo>/`.
4. Local rehearsal: `BASE_HREF=/agentlens/ bash scripts/build_pages.sh` (the output keeps deep links working via `404.html`).
   Other static hosts (Netlify, Cloudflare Pages, S3): `BASE_HREF=/ bash scripts/build_pages.sh` and upload `dashboard/dist/dashboard/browser`.
5. Label it honestly in the README: the data is synthetic.

## B. Single VM with HTTPS
1. A small Linux VM with Docker, a domain whose A/AAAA record points to it, ports 80/443 open.
2. ```bash
   git clone <your repo> && cd agentlens/deploy
   DOMAIN=agentlens.example.com docker compose -f docker-compose.prod.yml up -d --build
   ```
   Caddy obtains and renews the certificate by itself. Update: `git pull && docker compose -f docker-compose.prod.yml up -d --build`.
3. Check: `curl -I https://agentlens.example.com` and open a deep link such as `/runs`.

## C. Full stack on a VM
Overlay file `deploy/docker-compose.fullstack.yml` adds Caddy (HTTPS) in front of the root compose stack and un-publishes every other port.
The dashboard runs with `DEMO=false` and an empty `API_BASE_URL` (same origin); Caddy forwards `/api/*` to the gateway unchanged and disables buffering for SSE.
```bash
cp .env.example .env    # + ALLOWED_ORIGINS=https://$DOMAIN
DOMAIN=agentlens.example.com docker compose -f docker-compose.yml -f deploy/docker-compose.fullstack.yml up -d --build
```
Then:
- keep Postgres **unexposed** (no `ports:`), with a strong `POSTGRES_PASSWORD` and a volume backup;
- set `ADMIN_API_KEY` (`openssl rand -hex 32`), `DAILY_COST_CAP_USD`, and `ALLOWED_ORIGINS` to your domain;
- keep secrets in `.env` on the server, never in the repo; the pricing file goes in `config/pricing.yaml` on the server.

## D. Nightly regression gate (GitHub Actions)
Workflow: `.github/workflows/nightly-eval.yml` (direct mode: no gateway, DB or Docker).
1. Settings → Secrets → Actions: `ANTHROPIC_API_KEY`, `SEC_USER_AGENT`, `PRICING_YAML` (the full file contents).
2. Settings → Variables: `NIGHTLY_EVAL_ENABLED=true` (otherwise only manual runs execute; this prevents surprise bills).
3. Needs ≥ 1 verified golden case. It runs with `--verified-only`, a hard `--budget-usd`, `--gate`, and compares against
   `evals/baselines/baseline.results.jsonl` if you commit one (promote a good run by copying its `results.jsonl` there).
4. Artifacts (`report.md`, results, traces) are uploaded on every run. Set a provider-side spend limit as well; the in-code cap is a second line of defence.

## Production hygiene checklist
- [ ] Provider-side monthly spend limit set
- [ ] `DAILY_COST_CAP_USD` set (full stack)
- [ ] No secrets in git history; `.env`/`pricing.yaml` ignored
- [ ] Pin Docker base image tags and commit `uv.lock` (OPS-06; `uv.lock` is present, image tags are not pinned yet)
- [ ] HTTPS only; Postgres not published to the internet
- [ ] Dependabot/dependency alerts enabled
