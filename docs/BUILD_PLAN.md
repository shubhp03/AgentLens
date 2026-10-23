# AgentLens — Build Plan, AI-Assist Workflow, and Launch Playbook

Today is Sun Oct 18, 2026. Plan assumes ~10 h/week on this project (plus ~6 h/week LeetCode per the career plan). If your Purdue
workload spikes (check deadlines/finals now), **slip dates, don't cut the quality gates** — see the cut line in REQUIREMENTS.md.

## 0. First 48 hours (do these before anything else)
1. **Employer policy check (OPS-08).** Read Fidelity's outside-business-activity / IP / open-source policy; confirm in writing how
   ScopeTwo and public side projects are handled. Use only public data, your own machine/accounts, and never mention internal systems.
2. `cd agentlens && make setup && make test` → expect green (58 tests) with 5 `xfail` (the loop spec).
3. Create `.env` (Anthropic key, `SEC_USER_AGENT` with your real name + email) and `config/pricing.yaml` **from the provider pricing page**
   with today's date as `as_of` (OPS-07).
4. **Live tool check (TL-04):** run the five EDGAR tools against Apple/Microsoft/Alphabet; save a transcript; turn any parse failure into a
   fixture test. Check `get_filing_section` on real 10-Ks (TL-05) — that heuristic is the most likely thing to break.
5. **One real LLM call with a tool round-trip (AG-10)** through `AnthropicClient`. Confirm usage fields and non-zero cost.
6. `git init`, first commit (check `.gitignore`), commit `uv.lock` (OPS-06). Keep the repo **private** until M2.

## 1. Schedule
| Window | Milestone | You do | AI-assist does |
|---|---|---|---|
| Oct 18–21 | **M0** foundations | steps above | — |
| Oct 22–27 | **M1** agent works | **AG-01** loop (TDD against `test_loop.py`), AG-02/03/06/07/08, **AG-05** MCP registry (read the MCP v2 migration guide first) | review your loop; draft AG-04 only if time |
| Oct 28–Nov 8 | **M2 / Release A** | **EV-02** author ≥40 verified cases (then continue to 60–80), **EV-06** failure classifier | EV-05 runner, EV-07, EV-08 report generator, EV-11 wiring |
| Nov 9–29 | **M3** platform | **GW-06/07** (run executor + trace persister), review everything else | GW-01..05, 08..18; UI-01..03, 07, 08; you + AI pair on UI-04 and UI-09 |
| Nov 30–Dec 13 | **M4 / Release B** | ablations + write-up (DOC-04), demo video, replay mode (UI-12) | OPS-04/05, EV-10/12/15, polish |
| Dec 14–Jan 12 | maintenance | LeetCode/system design ramp up; project only for fixes + one follow-up post | — |

**Thanksgiving week (Nov 23–29)** is likely light: plan M3 slack there rather than assuming full hours.

## 2. How to hand work to your AI coding assistant
Your workflow ("core by me, rest by the assistant") is sound **if you keep ownership of the decisions and the verification**.
- Give it: `AGENTS.md`, the one requirement row (ID + AC), and the files it must touch. Ask for **tests first**.
- One requirement per session/PR. Reject diffs that touch unrelated files.
- **You run** `make lint test contracts` (and `make gateway-test` / `make stack-smoke` / `npm run build` when relevant). Never accept "should work" — see output.
- Review for the failure modes AI code actually has: invented API usage (esp. **MCP SDK v2**, FastAPI/psycopg 3 changes, Angular
  signals APIs), swallowed exceptions, missing idempotency, N+1 queries, secrets in logs, tests that assert nothing.
- Anything that produces **numbers for the README** (reports, tables) comes from real runs only — the assistant must not "fill in" results.
- Keep a short `docs/DECISIONS.md`: each time you or the assistant deviates from ARCHITECTURE.md, log it (you will need this for interviews).
- **Why you write AG-01, AG-05, GW-07, EV-02, EV-06 yourself:** these are what interviewers probe (the loop, the protocol, the persistence
  semantics, the dataset, the failure analysis). If you cannot whiteboard them, the project hurts rather than helps.

Suggested prompt skeleton for a requirement:
```
Read AGENTS.md and docs/REQUIREMENTS.md. Implement requirement <ID> only.
Files you may change: <list>. Write/extend tests first. Run: <commands>. Show me the command output.
If anything in the requirement is ambiguous or conflicts with docs/ARCHITECTURE.md, stop and ask.
```

## 3. Risks and mitigations
| Risk | Likelihood | Mitigation |
|---|---|---|
| Scope creep (UI/gateway swallow the schedule) | High | Release A ships results **before** the gateway/UI exist; cut line in REQUIREMENTS.md |
| Gold-set errors undermine credibility | Medium | Source-only gold, second pass, provenance, held-out split, verified/draft gate |
| Section extraction fails on real 10-Ks | High | Test on ≥5 real filings early (TL-05); fall back to XBRL-only questions for text categories if needed |
| LLM spend overruns | Medium | Per-run budget, daily cap, `--budget-usd`, pilot cost measurement, small model for dev iterations |
| MCP v2 / FastAPI / Angular API drift → assistant writes stale code | Medium | Pin versions, read migration guides, require tests + command output |
| Burnout (≈16 h/week extra on top of work + MS) | High | Hard stop Dec 13; maintenance mode after; cut scope not sleep |
| Employer-policy problem | Low–Medium, high impact | OPS-08 before any public push; public data only |
| Results are unflattering (small model ≈ big model, tools don't help) | Possible | That is still a publishable finding **if rigorous**; the story is the method, not a winning number |
| Interview probes beyond your depth | Medium | Own the five core pieces; prepare the "what I'd do at scale" talking points |

## 4. Launch playbook
**Principle:** publish early, publish measured numbers only, show the failure analysis — that is the differentiator.

**Release A (≈ Nov 8) — LinkedIn post #1 + repo public**
- Pre-flight: README has a real results table; Limitations section present; no secrets (run a secret scan); repo license chosen; employer
  check done; every number traceable to `docs/reports/`.
- Post structure (≈150–250 words + 1 image): (1) the problem in one sentence ("agent demos tell you it *can*; I wanted to know how often it's
  right"), (2) what you built in one line, (3) **one surprising finding** with a number and CI (e.g. which failure category dominated, or what the
  no-tools control scored), (4) honest limitation (n, single domain), (5) link. Image = results table or failure-breakdown chart.
- Don't: claim "production-grade", name Fidelity systems, post unverified percentages, or tag companies you are not affiliated with.

**Release B (≈ Dec 13) — post #2 + write-up**
- Write-up (DOC-04): "What broke when I evaluated my agent" — the failure taxonomy, the one-change-at-a-time loop, before/after numbers, what the
  statistics did and did not let you claim. Publish on your site/Medium; link from README and LinkedIn.
- Post #2: the dashboard (trace-waterfall screenshot or 60–90 s video), the compare view, and the lesson. Offer the repo + demo (replay mode) link.
- Reuse: pin the repo on GitHub; add a "Projects" entry on LinkedIn; update `shubham-patel.com`.

## 5. Resume bullets (templates — fill only from measured reports)
Target two bullets plus the project line; keep each to one line of impact + method:
- `AgentLens — LLM agent evaluation & observability platform (Python, FastAPI, Angular, PostgreSQL, MCP).`
- Built a tool-using SEC-filings agent and a trace + eval platform; scored **[N]** hand-verified cases × **[R]** trials with paired statistics
  (Wilson CIs, exact McNemar) and a CI regression gate.
- Cut failure category **[X]** from **[a]%** to **[b]%** of failures via **[specific change]**; reduced cost per successful task **[c]%** with **[small model/prompt]**
  at no significant quality loss (p = **[p]**).
Do not include a number you cannot trace to a report. If a result is a null result, say so honestly ("no significant difference, p = …").

## 6. Interview narrative (STAR) and whiteboard checklist
- **Situation/Task:** needed evidence an LLM agent works, not a demo. **Action:** designed agent + trace + eval + gate; wrote the loop, protocol, dataset
  and failure classifier myself. **Result:** the measured numbers, the failure category you fixed, what you'd change.
- Be able to whiteboard without notes: the agent loop (termination reasons, budgets, retries), the NDJSON event protocol and why spans are idempotent,
  the DB integrity rules (`passed ⇔ failure_category IS NULL`), Wilson/McNemar and when they apply, and the "at scale" section of ARCHITECTURE.md.
- Prepare 3 honest "what I'd do differently" answers (e.g. larger independent gold set, judge calibration, OTLP export).

## 7. Definition of done for the weekly check-in
Each Sunday: update REQUIREMENTS.md statuses, note slips, decide cuts using the cut line, and book next week's hours. If two consecutive weeks slip,
cut scope immediately rather than extending the deadline.
