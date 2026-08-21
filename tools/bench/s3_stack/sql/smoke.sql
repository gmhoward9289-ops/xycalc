-- Smoke: MergeTree table whose parts must live on the s3 disk (policy s3_main).
CREATE DATABASE IF NOT EXISTS xycalc_s3;

CREATE TABLE IF NOT EXISTS xycalc_s3.events
(
    id UInt64,
    account_id UInt64,
    amount_cents Int64,
    created_at DateTime
)
ENGINE = MergeTree
ORDER BY (account_id, created_at)
SETTINGS storage_policy = 's3_main';

TRUNCATE TABLE xycalc_s3.events;

INSERT INTO xycalc_s3.events
SELECT
    number AS id,
    number % 1000 AS account_id,
    (rand() % 100000) AS amount_cents,
    now() - (rand() % 86400) AS created_at
FROM numbers(50000);

-- Force a merge so parts are stable enough to inspect.
OPTIMIZE TABLE xycalc_s3.events FINAL;

SELECT
    database,
    table,
    disk_name,
    sum(rows) AS rows,
    count() AS parts
FROM system.parts
WHERE active AND database = 'xycalc_s3' AND table = 'events'
GROUP BY database, table, disk_name
FORMAT PrettyCompact;
