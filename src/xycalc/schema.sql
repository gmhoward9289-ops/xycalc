-- xycalc : "How much X does it take to run Y?"
--
-- v0 answers this for MongoDB's WiredTiger cache. The schema is deliberately
-- general so ClickHouse, Redis, Celery, EBS and SSDs drop in as DATA rather
-- than as migrations. Nothing below hardcodes "mongodb" or "cache".
--
-- Design rules, enforced by structure rather than by convention:
--
--   1. Every coefficient carries a source_id, NOT NULL. There is no way to
--      insert a number without saying where it came from.
--
--   2. Every coefficient carries applies_to, NOT NULL. An infrastructure
--      figure without a version is a lie -- WiredTiger's eviction defaults,
--      ClickHouse's settings and EBS's per-volume limits all move between
--      releases and hardware generations. "80%" is not a fact; "80% on
--      MongoDB >=3.0" is.
--
--   3. Every estimated coefficient carries lo/mode/hi, never a bare point
--      value. Documented constants set all three equal.
--
--   4. Nothing is deleted. Superseded figures get valid_to set and stay
--      queryable, so an answer given last month reproduces exactly.
--
-- The three-part model, identical across every system:
--
--   FLOOR        - the irreducible requirement. Bytes that must be resident,
--                  IOPS the workload actually issues. You cannot go below it.
--   AMPLIFIER    - a chain of multipliers that raise the requirement above
--                  the floor: decompression, eviction headroom, write
--                  amplification, index overhead.
--   HEADROOM     - what the tail costs rather than the mean. Concurrency
--                  spikes, checkpoint bursts, per-connection memory. This is
--                  where "it works until it doesn't" lives.
--   CONSTRAINT   - a bound that does not enter the arithmetic but qualifies
--                  the answer ("cache should not exceed N% of RAM").
--
-- Two questions over one model:
--   sizing    - given a workload, how much do I need?
--   headroom  - given what I have, how much margin is left and where does it
--               break?

PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- Provenance
-- ---------------------------------------------------------------------------

CREATE TABLE source (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    publisher       TEXT    NOT NULL,
    url             TEXT,
    version         TEXT,            -- the doc/release the figure was read from
    published_on    TEXT,            -- ISO date; NULL if undated
    retrieved_on    TEXT    NOT NULL,
    source_type     TEXT    NOT NULL CHECK (source_type IN (
                        'vendor_doc',    -- MongoDB manual, AWS EBS docs
                        'source_code',   -- read out of the implementation
                        'vendor_blog',   -- engineering blog, release notes
                        'practitioner',  -- Percona, Jepsen, conference talks
                        'benchmark',     -- a benchmark we ran, harness committed
                        'measured',      -- observed on a real running system
                        'derived',       -- computed from other cited figures
                        'estimate'       -- our own reasoned estimate
                    )),
    notes           TEXT
);


-- ---------------------------------------------------------------------------
-- What we are sizing
-- ---------------------------------------------------------------------------

CREATE TABLE system (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,   -- mongodb, ebs, clickhouse
    label           TEXT    NOT NULL,
    category        TEXT    NOT NULL CHECK (category IN (
                        'database', 'storage', 'cache', 'queue', 'hardware'
                    )),
    notes           TEXT
);

-- A named quantity with a unit. The vocabulary of the whole corpus: every
-- coefficient, observation and model output is an instance of a parameter,
-- so two figures in the same unit are always comparable.
CREATE TABLE parameter (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,   -- cache.eviction_target_pct
    label           TEXT    NOT NULL,
    unit            TEXT    NOT NULL,          -- bytes, percent, ratio, iops
    dimension       TEXT    NOT NULL CHECK (dimension IN (
                        'bytes', 'percent', 'ratio', 'iops', 'count',
                        'seconds', 'bytes_per_second'
                    )),
    notes           TEXT
);


-- ---------------------------------------------------------------------------
-- The cited numbers
-- ---------------------------------------------------------------------------

CREATE TABLE coefficient (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,
    parameter_id    INTEGER NOT NULL REFERENCES parameter(id),
    system_id       INTEGER NOT NULL REFERENCES system(id),

    -- Gate 2. Free text on purpose: ">=3.0" is right for MongoDB, "gp3" is
    -- right for EBS, and forcing both into semver would produce a fiction.
    -- What matters is that it cannot be omitted.
    applies_to      TEXT    NOT NULL,

    value_lo        REAL    NOT NULL,
    value_mode      REAL    NOT NULL,
    value_hi        REAL    NOT NULL,

    confidence      TEXT    NOT NULL CHECK (confidence IN (
                        'documented',    -- stated outright by the vendor
                        'code',          -- read from the implementation
                        'measured',      -- observed on a real system
                        'practitioner',  -- trade knowledge, conference talks
                        'estimate'       -- our reasoning, flagged as such
                    )),

    -- Gate 1. NOT NULL is the whole project.
    source_id       INTEGER NOT NULL REFERENCES source(id),
    quote           TEXT,            -- the sentence the figure was read from
    valid_from      TEXT,
    valid_to        TEXT,            -- set, never deleted, when superseded
    notes           TEXT,

    CHECK (value_lo <= value_mode AND value_mode <= value_hi)
);

CREATE INDEX idx_coefficient_param  ON coefficient(parameter_id);
CREATE INDEX idx_coefficient_system ON coefficient(system_id);


-- ---------------------------------------------------------------------------
-- The models
-- ---------------------------------------------------------------------------

CREATE TABLE model (
    id                  INTEGER PRIMARY KEY,
    slug                TEXT    NOT NULL UNIQUE,   -- mongodb.wt-cache
    question            TEXT    NOT NULL,          -- as a person would ask it
    system_id           INTEGER NOT NULL REFERENCES system(id),
    output_parameter_id INTEGER NOT NULL REFERENCES parameter(id),
    summary             TEXT,
    reframe             TEXT,     -- when the question as asked is the wrong one
    notes               TEXT
);

-- What the caller must supply. Drives the CLI flags and the web form, so a
-- new model needs no argument-parsing code.
CREATE TABLE model_input (
    id              INTEGER PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES model(id),
    key             TEXT    NOT NULL,     -- data_size
    label           TEXT    NOT NULL,
    unit            TEXT    NOT NULL,
    required        INTEGER NOT NULL DEFAULT 1,
    default_value   REAL,
    help            TEXT,
    sequence        INTEGER NOT NULL,
    UNIQUE (model_id, key)
);

-- One step of the calculation, evaluated in sequence order.
--
-- FLOOR terms are summed into the base. AMPLIFIER terms are then applied in
-- order. HEADROOM terms are added after. CONSTRAINT terms never enter the
-- arithmetic -- they annotate and bound the result.
--
-- `apply` decides how the coefficient enters the arithmetic. It exists so the
-- corpus can store the number the source actually states: MongoDB documents an
-- eviction target of 80 percent, so the coefficient is 80, and the model says
-- divide_by_fraction. Storing 1.25 instead would be storing our arithmetic as
-- though it were their documentation.
CREATE TABLE model_term (
    id              INTEGER PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES model(id),
    key             TEXT    NOT NULL,
    label           TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN (
                        'floor', 'amplifier', 'headroom', 'constraint'
                    )),
    apply           TEXT    NOT NULL CHECK (apply IN (
                        'input',                -- straight from the caller
                        'multiply',             -- value is a ratio >= 1
                        'divide_by_fraction',   -- value is a percent; /(v/100)
                        'add_bytes',            -- fixed addition
                        'add_fraction',         -- percent of the running total
                        'note'                  -- constraints; no arithmetic
                    )),
    input_key       TEXT,           -- for apply='input'
    coefficient_id  INTEGER REFERENCES coefficient(id),
    optional        INTEGER NOT NULL DEFAULT 0,
    rationale       TEXT    NOT NULL,   -- why this term exists at all
    sequence        INTEGER NOT NULL,

    UNIQUE (model_id, key),
    -- A term either reads an input or cites a coefficient. A term that does
    -- neither is a number from nowhere, which is the one thing this schema
    -- exists to prevent.
    CHECK (
        (apply = 'input' AND input_key IS NOT NULL AND coefficient_id IS NULL)
        OR (apply <> 'input' AND coefficient_id IS NOT NULL)
    )
);

CREATE INDEX idx_model_term_model ON model_term(model_id);


-- ---------------------------------------------------------------------------
-- Evidence
-- ---------------------------------------------------------------------------

-- Something actually observed on a running system: a benchmark here, a
-- production metric at work. Rows merged from local/ land here too, which is
-- how a deployment validates the models against its own reality without
-- forking the code or publishing its telemetry.
CREATE TABLE observation (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,
    system_id       INTEGER NOT NULL REFERENCES system(id),
    parameter_id    INTEGER NOT NULL REFERENCES parameter(id),
    value           REAL    NOT NULL,
    unit            TEXT    NOT NULL,
    workload        TEXT,            -- read-heavy, bulk load, mixed
    machine_class   TEXT,            -- r6i.4xlarge, m2 macbook
    system_version  TEXT,            -- what was actually running
    observed_on     TEXT,
    origin          TEXT    NOT NULL DEFAULT 'corpus'
                    CHECK (origin IN ('corpus', 'local')),
    source_id       INTEGER NOT NULL REFERENCES source(id),
    notes           TEXT
);

CREATE INDEX idx_observation_system ON observation(system_id);

-- How wrong the model was. A model with no rows here is unvalidated, and the
-- audit, the CLI and the web page all have to say so out loud -- that is the
-- difference between a corpus and a blog post with a formula in it.
CREATE TABLE validation (
    id              INTEGER PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES model(id),
    case_slug       TEXT    NOT NULL,
    observation_id  INTEGER REFERENCES observation(id),
    inputs_json     TEXT    NOT NULL,   -- what was fed in

    -- Compare against the running total after this term rather than against
    -- the final answer. Not a convenience: most measurements observe an
    -- INTERMEDIATE quantity, and comparing them to the final one measures the
    -- gap between two different questions.
    --
    -- The case that forced it: `mongodb.wt-cache` outputs the cache size to
    -- CONFIGURE, while serverStatus reports the bytes currently RESIDENT.
    -- Those differ by exactly the eviction-headroom divisor, so validating one
    -- against the other reported a 25% error for a model that was working
    -- perfectly. A validation that is wrong in a flattering direction would be
    -- bad; one wrong in either direction is useless.
    at_term         TEXT,
    predicted_lo    REAL    NOT NULL,
    predicted_mode  REAL    NOT NULL,
    predicted_hi    REAL    NOT NULL,
    actual          REAL    NOT NULL,
    within_band     INTEGER NOT NULL,   -- did the band contain reality?
    error_pct       REAL    NOT NULL,   -- signed, against mode
    notes           TEXT,
    UNIQUE (model_id, case_slug)
);


-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

CREATE VIEW v_coefficient AS
SELECT c.id, c.slug, c.applies_to, c.value_lo, c.value_mode, c.value_hi,
       c.confidence, c.quote, c.notes,
       p.slug AS parameter, p.label AS parameter_label, p.unit, p.dimension,
       sy.slug AS system, sy.label AS system_label,
       s.slug AS source, s.title AS source_title, s.publisher, s.url,
       s.source_type
FROM coefficient c
JOIN parameter p ON p.id = c.parameter_id
JOIN system    sy ON sy.id = c.system_id
JOIN source    s  ON s.id  = c.source_id
WHERE c.valid_to IS NULL;

-- Validation status per model, including the models that have none. The LEFT
-- JOIN is the point: a model with zero cases must still appear.
CREATE VIEW v_model_validation AS
SELECT m.slug          AS model,
       COUNT(v.id)     AS cases,
       SUM(COALESCE(v.within_band, 0)) AS within_band,
       AVG(ABS(v.error_pct))           AS mean_abs_error_pct
FROM model m
LEFT JOIN validation v ON v.model_id = m.id
GROUP BY m.slug;
