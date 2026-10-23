-- AgentLens schema v1. PostgreSQL 16. Managed by Flyway (gateway module).
-- Conventions: uuid PKs, timestamptz everywhere, CHECK constraints instead of PG enums
-- (cheaper to evolve in migrations), jsonb for payloads that are stored but not queried deeply.

-- ============================================================ auth
CREATE TABLE api_keys (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name               text        NOT NULL,
    key_prefix         text        NOT NULL,                 -- first 8 chars, shown in UI
    key_hash           text        NOT NULL UNIQUE,          -- SHA-256 hex of the full key; never store the key
    rate_limit_per_min integer     NOT NULL DEFAULT 60 CHECK (rate_limit_per_min > 0),
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_used_at       timestamptz,
    revoked_at         timestamptz
);

-- ============================================================ configuration snapshots
-- Immutable: an eval result must always be traceable to the exact agent variant that produced it.
CREATE TABLE agent_configs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name              text        NOT NULL,                  -- human label, NOT unique
    config_hash       text        NOT NULL UNIQUE,           -- sha256 of canonical JSON of the fields below (computed by gateway only)
    provider          text        NOT NULL,
    model             text        NOT NULL,
    system_prompt_id  text        NOT NULL,
    system_prompt_sha text        NOT NULL,
    temperature       numeric(3,2) NOT NULL DEFAULT 0 CHECK (temperature BETWEEN 0 AND 2),
    max_steps         integer     NOT NULL DEFAULT 12 CHECK (max_steps BETWEEN 1 AND 50),
    max_cost_usd      numeric(10,4) CHECK (max_cost_usd > 0),
    max_output_tokens integer     NOT NULL DEFAULT 2048,
    tool_allowlist    text[]      NOT NULL DEFAULT '{}',
    params            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Prices used for cost metrics, kept for transparency/reproducibility (agent computes cost at run time).
CREATE TABLE model_pricing (
    provider                text          NOT NULL,
    model                   text          NOT NULL,
    input_usd_per_mtok      numeric(12,6) NOT NULL CHECK (input_usd_per_mtok > 0),
    output_usd_per_mtok     numeric(12,6) NOT NULL CHECK (output_usd_per_mtok > 0),
    cache_read_usd_per_mtok numeric(12,6),
    effective_from          date          NOT NULL,
    PRIMARY KEY (provider, model, effective_from)
);

-- ============================================================ evaluation datasets
CREATE TABLE datasets (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text        NOT NULL,
    version      text        NOT NULL,
    description  text,
    content_hash text        NOT NULL,                       -- sha256 of the canonical JSONL: detects silent edits
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE TABLE eval_cases (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id     uuid NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    case_key       text NOT NULL,
    question       text NOT NULL,
    category       text NOT NULL CHECK (category IN ('lookup','calc','text_extract','multi_hop','unanswerable')),
    difficulty     text NOT NULL CHECK (difficulty IN ('easy','medium','hard')),
    expected       jsonb NOT NULL,                           -- see contracts/eval_case.schema.json
    required_tools text[] NOT NULL DEFAULT '{}',
    provenance     jsonb,
    status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','verified')),
    tags           text[] NOT NULL DEFAULT '{}',
    UNIQUE (dataset_id, case_key)
);

-- ============================================================ eval runs (created before runs: runs may reference them)
CREATE TABLE eval_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id  uuid NOT NULL REFERENCES datasets(id),
    config_id   uuid NOT NULL REFERENCES agent_configs(id),
    label       text NOT NULL DEFAULT 'manual',
    trigger     text NOT NULL DEFAULT 'manual' CHECK (trigger IN ('manual','ci','nightly')),
    git_sha     text,
    repeats     integer NOT NULL DEFAULT 1 CHECK (repeats BETWEEN 1 AND 10),
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed','cancelled')),
    is_baseline boolean NOT NULL DEFAULT false,
    gate_passed boolean,
    notes       text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
-- One baseline per dataset (enforced in the DB):
CREATE UNIQUE INDEX uq_eval_runs_one_baseline_per_dataset ON eval_runs (dataset_id) WHERE is_baseline;
CREATE INDEX ix_eval_runs_started ON eval_runs (started_at DESC);

-- ============================================================ runs + spans (the trace store)
CREATE TABLE runs (
    id                 uuid PRIMARY KEY,                     -- generated by the gateway before dispatch
    config_id          uuid NOT NULL REFERENCES agent_configs(id),
    api_key_id         uuid REFERENCES api_keys(id),
    eval_run_id        uuid REFERENCES eval_runs(id) ON DELETE SET NULL,
    source             text NOT NULL DEFAULT 'interactive' CHECK (source IN ('interactive','eval')),
    question           text NOT NULL,
    status             text NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued','running','completed','failed','cancelled')),
    termination_reason text CHECK (termination_reason IN
                         ('answered','step_limit','budget_exceeded','tool_failure','provider_error',
                          'timeout','cancelled','gateway_restart')),
    final_answer       jsonb,                                -- Answer (abstained, value, unit, period, text, citations)
    error              text,
    input_tokens       integer     NOT NULL DEFAULT 0,
    output_tokens      integer     NOT NULL DEFAULT 0,
    cache_read_tokens  integer     NOT NULL DEFAULT 0,
    cost_usd           numeric(12,6) NOT NULL DEFAULT 0,
    latency_ms         integer,
    step_count         integer     NOT NULL DEFAULT 0,
    tool_call_count    integer     NOT NULL DEFAULT 0,
    created_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,
    finished_at        timestamptz,
    CHECK ((status IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL))
);
CREATE INDEX ix_runs_created ON runs (created_at DESC);
CREATE INDEX ix_runs_status ON runs (status) WHERE status IN ('queued','running');
CREATE INDEX ix_runs_eval_run ON runs (eval_run_id) WHERE eval_run_id IS NOT NULL;
CREATE INDEX ix_runs_config ON runs (config_id);

CREATE TABLE spans (
    id             uuid PRIMARY KEY,                         -- client-generated by the agent => idempotent upsert
    run_id         uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    parent_span_id uuid,                                     -- intentionally NO FK: the root span finishes (and is persisted) last
    seq            integer NOT NULL CHECK (seq >= 0),
    type           text NOT NULL CHECK (type IN ('agent','llm_call','tool_call')),
    name           text NOT NULL,
    status         text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
    model          text,
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz,
    latency_ms     integer GENERATED ALWAYS AS
                     ((EXTRACT(EPOCH FROM (ended_at - started_at)) * 1000)::integer) STORED,
    input          jsonb NOT NULL DEFAULT '{}'::jsonb,
    output         jsonb NOT NULL DEFAULT '{}'::jsonb,
    error          text,
    input_tokens   integer,
    output_tokens  integer,
    cost_usd       numeric(12,6),
    attributes     jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, seq)
);
CREATE INDEX ix_spans_run_seq ON spans (run_id, seq);
CREATE INDEX ix_spans_tool_name ON spans (name) WHERE type = 'tool_call';

-- ============================================================ eval results
CREATE TABLE eval_results (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_id      uuid NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    case_id          uuid NOT NULL REFERENCES eval_cases(id),
    run_id           uuid REFERENCES runs(id) ON DELETE SET NULL,
    repeat_idx       integer NOT NULL DEFAULT 0 CHECK (repeat_idx >= 0),
    passed           boolean NOT NULL,
    scores           jsonb   NOT NULL DEFAULT '{}'::jsonb,   -- task_success, unit_ok, period_ok, citation_validity, numeric_grounded, required_tool_recall, redundant_call_rate, error_call_rate
    failure_category text CHECK (failure_category IN
                       ('wrong_tool','bad_args','retrieval_miss','wrong_period','unit_error','calc_error',
                        'hallucinated_number','false_abstain','missed_abstain','format_error',
                        'step_limit','budget_exceeded','tool_error_unrecovered','provider_error','other')),
    judge_rationale  text,
    latency_ms       integer,
    cost_usd         numeric(12,6) NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (eval_run_id, case_id, repeat_idx),
    CHECK (passed = (failure_category IS NULL))              -- every failure must be categorised; passes must not be
);
CREATE INDEX ix_eval_results_run ON eval_results (eval_run_id);
CREATE INDEX ix_eval_results_failure ON eval_results (eval_run_id, failure_category) WHERE NOT passed;

-- ============================================================ aggregates
CREATE VIEW eval_run_metrics AS
SELECT
    eval_run_id,
    count(*)                                                        AS n_results,
    avg(passed::int)::numeric(6,4)                                  AS pass_rate,
    sum(cost_usd)                                                   AS total_cost_usd,
    avg(cost_usd)                                                   AS avg_cost_usd,
    CASE WHEN count(*) FILTER (WHERE passed) = 0 THEN NULL
         ELSE sum(cost_usd) / count(*) FILTER (WHERE passed) END    AS cost_per_success_usd,
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms)        AS p50_latency_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)        AS p95_latency_ms
FROM eval_results
GROUP BY eval_run_id;

-- Daily spend (drives the gateway's DAILY_COST_CAP_USD guard). UTC day.
CREATE VIEW daily_spend AS
SELECT (created_at AT TIME ZONE 'UTC')::date AS day, sum(cost_usd) AS cost_usd, count(*) AS runs
FROM runs
GROUP BY 1;
