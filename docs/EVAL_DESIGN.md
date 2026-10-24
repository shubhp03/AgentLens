# AgentLens — Evaluation Design

The evaluation is the product. If this document is sloppy, the project is a demo; if it is rigorous, the project is evidence.

## 1. Questions the eval must answer
1. **How often is the agent right** on questions with a checkable answer? (pass rate, with a confidence interval)
2. **How does it fail?** (failure taxonomy: every failure gets exactly one category)
3. **What does being right cost?** (cost per *successful* task, p95 latency)
4. **Is that better than not using tools at all?** (no-tools control arm; public filings are in pretraining data)
5. **Did this change make it better or worse?** (paired comparison + regression gate)

## 2. Golden set (`evals/datasets/golden_*.jsonl`)
Target: **80 cases, ≥60 verified before any number is published.** Seed file has 6 *draft* cases with deliberately empty gold values.

| Category | Target n | What it tests | Example shape |
|---|---|---|---|
| `lookup` | 20 | Retrieve one XBRL fact for a pinned period | "Total net sales, fiscal 2023" |
| `calc` | 20 | Retrieve ≥2 facts, then compute (growth, margin, ratio) | "% change in revenue FY22→FY23" |
| `text_extract` | 12 | Read a 10-K item and extract a fact stated only in text | "Reportable segments", "principal risk wording" |
| `multi_hop` | 14 | Cross-company or cross-period reasoning | "Higher operating margin: A or B?" |
| `unanswerable` | 14 | Correct behaviour is to **abstain** | future figures, missing/ambiguous company or period, non-public data |

Spread across: ≥10 companies, at least 3 non-September/non-December fiscal year ends, easy/medium/hard roughly 30/45/25.
Include deliberate traps: companies whose revenue concept differs (`Revenues` vs `RevenueFromContractWithCustomer…`), restated
figures, a share-class company, a company with an amended filing.

### Scoring risk found live, and the decision taken: period labels
`score_numeric` requires `Answer.period` to equal `expected.period` after stripping spaces/hyphens/underscores and upper-casing.
Under the original wording ("e.g. 'FY2023'") the first live recordings had the model write `FY2024 (ended 2024-01-28)` for correct
NVIDIA answers, which would have scored `wrong_period` with value and unit right.
**Decision:** keep the scorer strict (a lenient scorer changes what the metric means) and tighten the field description the model
sees (`Answer.period`: label only, company's own fiscal-year label, no dates or explanation; guarded by
`agent/tests/test_events.py::test_period_field_is_specified_as_a_label_only`). Re-recording the same 8 questions afterwards gave a bare
label every time (8 of 8, one run each: encouraging, not a measured rate). The `system_v1` prompt hash is unchanged, but the
tool schema changed, so state the date/commit of this change next to any results; earlier recordings used the old wording.
The risk is not gone: measure `wrong_period` in the failure breakdown, and watch non-calendar fiscal years.

### Authoring protocol (this is where credibility is won or lost)
1. **Gold comes from the source, by hand.** Open the filing/XBRL frame yourself, read the number, record `provenance`
   (`accession`, `xbrl_concept`, `retrieved_at`, `verified_by`). Do **not** take gold from an LLM, including this agent.
2. **Pin everything.** Fiscal period as a normalized label (`FY2023`), explicit unit, explicit tolerance. Phrase questions so a human
   expert would reach the same single answer.
3. **Second pass.** Re-verify every gold value ≥24 h later (fresh eyes, from the filing again). Record disagreements; fix the *question*
   if it was ambiguous. If a friend/colleague (outside your employer's confidential context) can double-check a sample, even better.
4. **Draft vs verified.** `status: draft` cases never count in published numbers (`load_dataset(verified_only=True)`).
5. **Freeze + version.** Publish results against a named, hash-pinned dataset version. Changing a gold value = new version. The
   gateway rejects changed content under the same version (`content_hash`).
6. **Dev/test discipline.** Reserve ~25% of cases as a **held-out test split** you do not look at while tuning prompts/tools. Report
   both; the held-out number is the honest one. (With n≈80 the split is small — say so.)
7. **Leakage check.** The `no-tools` control quantifies how much is answerable from memory. Report it next to the headline number.

### Case schema
`evals/src/agentlens_evals/case.py` (exported to `contracts/eval_case.schema.json`). Verified cases require provenance and complete
expectations; `unanswerable ⇔ expected.kind=abstain` is enforced.

## 3. Scoring (deterministic first)
| Expectation | Rule | Notes |
|---|---|---|
| numeric | Convert answer and gold to canonical base units (`USD_millions`→USD; `percent`↔`ratio`), compare with relative tolerance (default 0.5%); period label must match if the case pins one | Unit-scale errors (answering 383,285 as "billions") are caught |
| text | Every `must_contain` string present (case/whitespace-insensitive) | Brittle by design; LLM judge (EV-13) is P2 and never overrides |
| abstain | Pass iff `abstained=true` | Penalises guessing on unanswerables |

Tolerance is per-case. Rounded figures in filings ("$383.3 billion") are why tolerance exists; set it deliberately, do not widen it to
rescue a failing result.

**Groundedness signals (diagnostic, not part of pass/fail):** citation validity (cited accession was retrieved in this run);
numeric grounding (the answer value appears in some tool output or calculator result — otherwise it came from memory or mental math).

## 4. Metrics
| Metric | Definition |
|---|---|
| Pass rate | Case-level: strict majority of repeats pass. Reported with **Wilson 95% CI** |
| Consistency | Fraction of cases where all repeats agree (flakiness) |
| Cost per successful task | total cost ÷ passes (cheap-but-wrong is not cheap) |
| Latency | p50 / p95 wall-clock per run |
| Steps / tool calls | mean per run; redundant-call rate; tool-error rate |
| Failure breakdown | counts per category (§5) and per case category |
| Abstention quality | false-abstain rate (on answerable) and missed-abstain rate (on unanswerable), reported separately |

Cost uses `config/pricing.yaml` (`as_of` date printed in every report). Never report a cost computed with placeholder prices.

## 5. Failure taxonomy and classifier (EV-06)
Categories are the DB enum in `eval_results.failure_category`. A failed result gets **exactly one** category; apply rules **in order,
first match wins**. A passed result gets none (DB CHECK enforces this).

| # | Category | Rule (evaluated on trace + answer + expected) |
|---|---|---|
| 1 | `provider_error` | Run terminated `provider_error` or `timeout` |
| 2 | `step_limit` | Terminated `step_limit` |
| 3 | `budget_exceeded` | Terminated `budget_exceeded` |
| 4 | `format_error` | Root span `no_submit=true`, or answer failed validation after the repair attempt |
| 5 | `missed_abstain` | Expected abstain; answer not abstained |
| 6 | `false_abstain` | Expected an answer; agent abstained |
| 7 | `tool_error_unrecovered` | The last tool call(s) before the answer errored and the answer is wrong |
| 8 | `unit_error` | Answer value is wrong but matches gold after correcting scale, or dimension mismatch (e.g. percent vs USD) |
| 9 | `wrong_period` | Value is correct for a different period (matches a retrieved fact whose period ≠ the pinned period), or period label mismatched |
| 10 | `calc_error` | The needed input facts appear in tool outputs, but the final value is wrong (bad arithmetic/formula/wrong inputs combined) |
| 11 | `hallucinated_number` | Answer value is numeric-ungrounded (matches no tool/calculator output) |
| 12 | `wrong_tool` | `required_tool_recall < 1` (a required tool was never called) |
| 13 | `bad_args` | ≥2 tool calls errored on invalid arguments (e.g. unknown concept, malformed dates) |
| 14 | `retrieval_miss` | Tools succeeded but the gold value never appeared in any tool output |
| 15 | `other` | None matched — **review these by hand; a large `other` bucket means the taxonomy needs work** |

Rules 8–14 are **heuristics**. Validate the classifier: hand-label ≥25 failures, report agreement, fix rules that disagree. Publish the
agreement number; it makes the failure breakdown trustworthy. Ordering is a design choice — if you reorder, record why.

## 6. Statistics (what you can and cannot claim)
- **Repeats:** 3 per case by default (temperature 0 is *not* deterministic). Majority vote → case-level outcome.
- **Intervals:** Wilson score interval on the case-level pass rate.
- **Comparing two configs:** *paired* by case; **exact McNemar** test on discordant pairs (`b`: base-only passes, `c`: candidate-only passes).
  Implemented in `evals/src/agentlens_evals/{stats,compare}.py`, tested against hand-computed values.
- **Power reality:** at n≈80, a difference below ~10 percentage points is usually indistinguishable from noise. Say "no significant
  difference" when that is the finding. Do not rank configs whose intervals overlap heavily and whose McNemar p ≥ 0.05.
- **Multiple comparisons:** if you compare many configs, state it and treat borderline p-values as exploratory.
- **No peeking:** decide the primary comparison (baseline vs each variant) before running; report all runs, not the winners.

## 7. Experiment plan
| Arm | Purpose |
|---|---|
| `no-tools` (control) | Memory-only ceiling → contamination/leakage estimate |
| `baseline` (frontier model + tools) | Headline number |
| `small-model` (cheaper model + tools) | Cost/quality trade-off |
| `baseline` + `system_v2` prompt | Prompt ablation with a **written hypothesis before the run** |
| Optional: tool ablations (drop `search_xbrl_concepts`) | Value of each tool |

**Process loop (the story for the write-up):** run baseline → classify failures → pick the top category → make **one** change (tool
description, prompt rule, or tool fix) → re-run on the dev split → confirm on held-out → record before/after in `docs/reports/`.
One change at a time, or the attribution is meaningless.

**Budget:** cost ≈ cases × repeats × arms × cost-per-run. Measure cost-per-run on a 5-case pilot, multiply, add 30% headroom, set
`--budget-usd` accordingly. Do not guess in advance; prices and token counts are measured, not assumed.

## 8. Regression gate (`agentlens-eval run --gate`)
Fails (exit 1) if any of: pass rate below an absolute floor; cost/task above a cap; statistically significant regression vs the
stored baseline (paired McNemar, α=0.05). Gate logic: `compare.gate`. Baseline is an explicit, human-promoted eval run (one per dataset).
CI never runs live evals on PRs; the nightly job does, with a hard `--budget-usd`.

## 9. Threats to validity (put a condensed version in the README)
- **Small n.** Wide intervals; limit claims.
- **Contamination.** Filings are in pretraining data; mitigated by the control arm and by preferring multi-step/calc questions.
- **Author bias.** One person writing questions and gold; mitigated by source-only gold, second pass, held-out split.
- **Classifier heuristics.** Mitigated by reporting classifier agreement on a hand-labelled sample.
- **Provider drift.** Hosted models change; record model id + date for every run and re-run the baseline before claiming a comparison.
- **Tolerance choices.** Fixed per case before running; never adjusted after seeing results.
- **Single domain.** SEC filings only; no claim of generality.

## 10. Reporting
Every report states: dataset version + content hash, config hash + prompt sha, model id, git sha, pricing `as_of`, date, n, repeats,
and the arm table (pass rate ± CI, cost/success, p95, top failure categories). Reports live in `docs/reports/` and are the **only**
source for any number that appears in the README, a LinkedIn post, or a resume bullet.


## Implementation notes (as built)
- **Case-level pass = strict majority of repeats; a tie fails.** The DB view `eval_run_metrics` (V2) reports `pass_rate`
  (case-level), `run_pass_rate` (per-attempt), `n_cases`, `n_cases_passed`. CIs are Wilson over cases; paired test is exact McNemar.
  (V1 originally reported run-level only; that contradicted this document, hence V2.)
- **Invalid final answers:** one repair attempt, then the run is accepted with root attribute `invalid_answer=True`
  (classified `format_error`). Plain text without a tool call: one nudge, then accepted with `no_submit=True`.
- **Root span is emitted last** (final `seq`), so `spans.parent_span_id` has no FK.
- **`split: dev|test`** on dataset cases: tune on `dev`, report once on `test`.
- Classifier: 15 ordered rules; the category list is asserted equal to the DB CHECK constraint by a test.
