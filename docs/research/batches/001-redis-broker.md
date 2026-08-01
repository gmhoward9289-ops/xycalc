# Batch 001 — Redis as a Celery broker: maxmemory and eviction

Feeds ROADMAP T7, which has a documented contradiction at its core: Celery's
docs say to set `maxmemory-policy` to `noeviction` **or** `allkeys-lru`,
practitioner guidance says `allkeys-lru` silently drops queued tasks, and
celery#5716 reports workers deadlocking under `noeviction`. Before the T7
experiment runs, the corpus needs the *documented* figures both sides rest on.

## Wanted

- Redis `maxmemory` default and the eviction policy default, per version
- The eviction policies and what each may evict
- Celery's Redis broker settings: visibility timeout default, what it binds
- Any figure in celery#5716 describing the OOM/deadlock conditions

## Sources fetched (2026-08-01)

| doc | what | version pin |
|---|---|---|
| 01-redis-conf-7.2 | redis.conf shipped with Redis 7.2 | URL tag `7.2` |
| 02-redis-conf-6.2 | redis.conf shipped with Redis 6.2 | URL tag `6.2` |
| 03-redis-eviction-docs | redis.io eviction reference | "latest", unpinned |
| 04-celery-redis-broker | Celery stable docs, Redis broker page | "stable", unpinned |
| 05-celery-issue-5716 | celery/celery#5716 + comments via GitHub API | n/a (issue) |

## Notes for the human gate

The two redis.conf files are the vendor's own shipped defaults — candidates
for promotion to `documented` (or `code`). The issue thread is practitioner
testimony at best. Report the docs-vs-practitioner conflict; do not resolve it.
