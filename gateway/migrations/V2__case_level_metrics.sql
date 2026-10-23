-- V2: headline pass rate is CASE-level, as docs/EVAL_DESIGN.md section 4 defines it.
-- A case passes when a strict majority of its repeats pass (so a 1-of-2 split is a FAIL; ties never pass).
-- V1's view averaged over individual results (run-level), which would not match the Wilson interval computed
-- over cases. Run-level pass rate is kept as `run_pass_rate` for reference; `pass_rate` is the case-level one.

CREATE VIEW eval_case_outcomes AS
SELECT eval_run_id,
       case_id,
       count(*)                                          AS n_repeats,
       count(*) FILTER (WHERE passed)                    AS n_passed,
       (count(*) FILTER (WHERE passed)) * 2 > count(*)   AS passed
FROM eval_results
GROUP BY eval_run_id, case_id;

DROP VIEW eval_run_metrics;
CREATE VIEW eval_run_metrics AS
WITH per_result AS (
    SELECT eval_run_id,
           count(*)                                                       AS n_results,
           avg(passed::int)::numeric(6,4)                                 AS run_pass_rate,
           sum(cost_usd)                                                  AS total_cost_usd,
           avg(cost_usd)                                                  AS avg_cost_usd,
           CASE WHEN count(*) FILTER (WHERE passed) = 0 THEN NULL
                ELSE sum(cost_usd) / count(*) FILTER (WHERE passed) END   AS cost_per_success_usd,
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms)       AS p50_latency_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)       AS p95_latency_ms
    FROM eval_results
    GROUP BY eval_run_id
), per_case AS (
    SELECT eval_run_id,
           count(*)                                AS n_cases,
           count(*) FILTER (WHERE passed)          AS n_cases_passed
    FROM eval_case_outcomes
    GROUP BY eval_run_id
)
SELECT r.eval_run_id,
       r.n_results,
       c.n_cases,
       c.n_cases_passed,
       (c.n_cases_passed::numeric / c.n_cases)::numeric(6,4) AS pass_rate,   -- case-level (headline)
       r.run_pass_rate,
       r.total_cost_usd,
       r.avg_cost_usd,
       r.cost_per_success_usd,
       r.p50_latency_ms,
       r.p95_latency_ms
FROM per_result r
JOIN per_case c USING (eval_run_id);
