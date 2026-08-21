-- Concurrent read load against the S3-backed table (phase: under_load).
SELECT region, status, count(), sum(amount_cents), avg(amount_cents), uniq(account_id)
FROM xycalc_s3.events
GROUP BY region, status
FORMAT Null;
