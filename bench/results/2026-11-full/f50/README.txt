Server time per call: PostgreSQL is Planning Time + Execution Time from EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON); MongoDB is executionStats.executionTimeMillis when the explain output has it, otherwise (aggregation pipelines) the largest executionTimeMillisEstimate among the stages, which is cumulative so it is the last stage's. MongoDB reports whole milliseconds, so a sub-millisecond call reads as 0 or 1. A repo call that runs several statements is the sum over its explained statements, in both databases. Write cases have client timing only.

Benchmark results (task 4.1a harness)

Warm cache only: every case is called `warmup` times before the timed calls, and no cache is flushed between runs.
Client timings use time.perf_counter_ns() around the whole repo call, including materialisation of the result.

Timed reads run as PostgreSQL app_ro (SELECT only) and MongoDB app. The two read identities are not symmetric in what
they could do: MongoDB app can write buyers and counters, but the harness never calls a write with it. Write cases
(--include-writes) run as app_admin in both databases, as the app's admin routes do.

Files
  raw.jsonl      every timed call: invocation, case, db, param_set, part, run, ms, rows, conn
  summary.csv    invocation, case, pair, db, param_set, part, n, rows, median_ms, p95_ms, min_ms, max_ms, server_ms, planning_ms, execution_ms
                 part is `call` for reads; `write` and `refresh` split an admin write (refresh is PostgreSQL only).
                 rows is the rows or documents the call returned (precomputed summaries have a different grain).
                 median_ms is the usual median, p95_ms is nearest rank.
  throughput.csv invocation, case, db, workers, seconds, calls, calls_per_s, median_ms, p95_ms
  index_on_off.csv  each indexed case with and without its index, per database (bench.explain); compare a database
                 only with itself
  env.json       software, settings and the list of invocations
  explain/       the captured statements and their plans (text and JSON)
