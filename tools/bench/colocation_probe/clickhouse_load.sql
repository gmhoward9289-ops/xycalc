-- Minimal ClickHouse footprint generator. Not trying to be realistic in the
-- way mongodb_load.js is (this harness measures RSS, not compression), so a
-- generated numbers() sequence is fine -- the only thing that matters is that
-- the table is big enough for a scan to actually pull data into ClickHouse's
-- own memory tracker, the thing max_server_memory_usage_to_ram_ratio bounds.

CREATE DATABASE IF NOT EXISTS xycalcbench;

CREATE TABLE IF NOT EXISTS xycalcbench.events
(
    id UInt64,
    account_id UInt64,
    amount_cents Int64,
    status Enum8('pending' = 1, 'settled' = 2, 'failed' = 3, 'refunded' = 4),
    region Enum8('us-east-1' = 1, 'us-west-2' = 2, 'eu-central-1' = 3, 'ap-southeast-2' = 4),
    created_at DateTime,
    note String
)
ENGINE = MergeTree
ORDER BY (account_id, created_at);

INSERT INTO xycalcbench.events
SELECT
    number AS id,
    number % 500000 AS account_id,
    (rand() % 5000000) - 2500000 AS amount_cents,
    (number % 4) + 1 AS status,
    (number % 4) + 1 AS region,
    now() - (rand() % 31536000) AS created_at,
    repeat('x', 64) AS note
FROM numbers(3000000);

-- A scan forces the working set into ClickHouse's own tracked memory rather
-- than leaving it to sit compressed-on-disk and untouched, which is the
-- state a freshly-inserted table can otherwise be in.
SELECT region, status, count(), sum(amount_cents), avg(amount_cents)
FROM xycalcbench.events
GROUP BY region, status
FORMAT Null;
