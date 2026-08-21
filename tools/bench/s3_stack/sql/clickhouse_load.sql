-- ClickHouse load for s3_stack performance runs.
-- Table uses storage_policy = 's3_main' so parts land on MinIO/S3, not local disk.
-- Row count is large enough that a GROUP BY scan touches real working set;
-- override via rewriting this file or injecting CH_ROWS before generation if needed.

CREATE DATABASE IF NOT EXISTS xycalc_s3;

CREATE TABLE IF NOT EXISTS xycalc_s3.events
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
ORDER BY (account_id, created_at)
SETTINGS storage_policy = 's3_main';

TRUNCATE TABLE xycalc_s3.events;

INSERT INTO xycalc_s3.events
SELECT
    number AS id,
    number % 500000 AS account_id,
    (rand() % 5000000) - 2500000 AS amount_cents,
    (number % 4) + 1 AS status,
    (number % 4) + 1 AS region,
    now() - (rand() % 31536000) AS created_at,
    repeat('x', 64) AS note
FROM numbers(__CH_ROWS__);

OPTIMIZE TABLE xycalc_s3.events FINAL;

-- Confirm parts are on the s3 disk before any timing claim is meaningful.
SELECT disk_name, sum(rows) AS rows, count() AS parts
FROM system.parts
WHERE active AND database = 'xycalc_s3' AND table = 'events'
GROUP BY disk_name
FORMAT PrettyCompact;

-- Warm-ish scan so "loaded" RSS includes ClickHouse's own tracked memory,
-- not only compressed parts sitting untouched on object storage.
SELECT region, status, count(), sum(amount_cents), avg(amount_cents)
FROM xycalc_s3.events
GROUP BY region, status
FORMAT Null;
