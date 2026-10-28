# Benchmark results, 2026-11-full

Every number in this file is generated from the committed `summary.csv`, `throughput.csv`, `index_on_off.csv`, `scaling.csv` and `env.json` files in this folder tree; none is typed by hand. The raw timings are in `f100/raw.jsonl` (and `f50/`, `f25/`).

## Conditions and limits

- Warm cache only: every call is repeated a number of times first and no cache is flushed between runs. No cold-cache result exists.
- One machine, Docker Desktop on Windows. The results say nothing about other hardware or a Linux host.
- Commit `09cbc0e`, PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2), MongoDB 8.3.11, psycopg 3.3.6 and pymongo as drivers. Docker was given 15.5 GiB of memory and the host reported 20 CPUs in `env.json`.
- Caches: PostgreSQL `shared_buffers` 1GB and MongoDB WiredTiger cache 1 GB (read from the running container), the same 1 GB for both. Container memory limits: PostgreSQL 1,500 MiB, MongoDB 1,500 MiB.
- Data: 241,357 sales in PostgreSQL and 241,357 in MongoDB at fraction 1; `bench.verify` passed (1,517 checks) before the reads, writes and throughput invocations; the index on and off invocation (`bench.explain`) does not run it.
- The `web` service was stopped for the whole run, so its healthcheck did not query either database during timing.
- Timed reads ran as PostgreSQL `app_ro` (`SELECT` only) and MongoDB `app`. These identities are not symmetric: MongoDB `app` can also write `buyers` and `counters`, which the harness never does. Write cases run as `app_admin` in both databases.
- MongoDB has an extra `{town, flat_type, txn_month}` index that PostgreSQL has no equivalent of. It favours MongoDB in `search_filtered`. Compare an index-on-off result of one database only with itself.
- MongoDB server times are whole milliseconds (`executionTimeMillis`, or the last stage's estimate for an aggregation), so a sub-millisecond call reads as 0 or 1. Server time is taken from `EXPLAIN` and is a different measurement from the client time (median, p95), which wraps the whole repo call including materialising the result. Write cases have client timing only.
- Each case has 3 parameter sets of 50 timed runs per database. Tables give every parameter set. Where a chart needs one number per case and database, the chart value is the median of the three parameter sets' medians, and the p95 whisker is the largest of the three p95 values. Each table below ends each case with a `chart value` row (median, p95) so every bar label can be found; tables print milliseconds to 4 dp exactly as the CSVs do, and the chart labels round the chart value to 1 dp.

## 1. Latency by pair type (full data, fraction 1)

![Median latency by case](latency_by_case.png)

The chart uses a log axis because the range of values exceeds 100 times; each bar is the chart value, the thin line runs to the largest p95. The refresh half of `admin_record_delete` is not charted (section 5).

### 1.1 Live queries (search, trends, rankings): same question, same result in both databases

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `search_filtered` | bishan-4room-2019-2021 | 5.0538 | 6.5715 | 7.5170 | 50 | 1.8084 | 2.3622 | 2 | 50 |
| `search_filtered` | marine-parade-3room-2017-2026 | 8.2138 | 8.9293 | 10.2180 | 50 | 1.7788 | 2.1001 | 1 | 50 |
| `search_filtered` | tampines-4room-2022-2024 | 4.5574 | 5.3166 | 6.1360 | 50 | 3.0268 | 3.4833 | 5 | 50 |
| `search_filtered` | **chart value** | **5.0538** | **8.9293** | - | - | **1.8084** | **3.4833** | - | - |
| `search_type_month` | 4room-2022-q1 | 3.6760 | 4.0155 | 4.7860 | 50 | 3.4829 | 4.6756 | 5 | 50 |
| `search_type_month` | 5room-2023-h2 | 2.9863 | 3.3619 | 4.1080 | 50 | 3.6506 | 4.2483 | 5 | 50 |
| `search_type_month` | 3room-2017-h1 | 2.7698 | 3.0867 | 3.6310 | 50 | 3.1951 | 3.7259 | 4 | 50 |
| `search_type_month` | **chart value** | **2.9863** | **4.0155** | - | - | **3.4829** | **4.6756** | - | - |
| `search_price_sort` | 4room-price-desc | 20.4653 | 23.3642 | 29.9840 | 50 | 8.0386 | 8.8773 | 7 | 50 |
| `search_price_sort` | 5room-price-asc | 13.5382 | 14.2036 | 25.2510 | 50 | 5.1185 | 5.7277 | 4 | 50 |
| `search_price_sort` | executive-price-desc | 5.9455 | 6.4552 | 9.2050 | 50 | 2.3591 | 2.5834 | 1 | 50 |
| `search_price_sort` | **chart value** | **13.5382** | **23.3642** | - | - | **5.1185** | **8.8773** | - | - |
| `search_unfiltered` | default-sort | 89.3298 | 96.7889 | 128.0060 | 50 | 97.1370 | 101.3702 | 100 | 50 |
| `search_unfiltered` | month-asc | 82.4370 | 89.6116 | 120.6300 | 50 | 73.8354 | 76.2335 | 74 | 50 |
| `search_unfiltered` | area-desc | 87.4375 | 97.9278 | 126.1220 | 50 | 84.3572 | 87.5584 | 83 | 50 |
| `search_unfiltered` | **chart value** | **87.4375** | **97.9278** | - | - | **84.3572** | **101.3702** | - | - |
| `trends_series` | bishan-4room | 68.2094 | 70.7705 | 115.1110 | 117 | 11.5038 | 13.8774 | 6 | 117 |
| `trends_series` | marine-parade-3room-2019-2024 | 25.6249 | 27.4492 | 42.1080 | 71 | 4.3589 | 5.2451 | 1 | 71 |
| `trends_series` | tampines-4room | 84.8229 | 86.4579 | 141.9620 | 117 | 30.6689 | 34.3946 | 25 | 117 |
| `trends_series` | **chart value** | **68.2094** | **86.4579** | - | - | **11.5038** | **34.3946** | - | - |
| `rankings_year` | 2022-all-types | 120.8439 | 123.5691 | 162.7810 | 127 | 77.6392 | 80.8150 | 78 | 127 |
| `rankings_year` | 2019-4room | 37.6246 | 38.9003 | 49.2110 | 26 | 15.9929 | 16.8431 | 12 | 26 |
| `rankings_year` | 2025-all-types | 110.3524 | 112.5388 | 138.3170 | 129 | 80.2666 | 84.0453 | 81 | 129 |
| `rankings_year` | **chart value** | **110.3524** | **123.5691** | - | - | **77.6392** | **84.0453** | - | - |

Reading: `search_filtered`: MongoDB lower, 2.8 times (PostgreSQL 5.0538 ms, MongoDB 1.8084 ms); MongoDB is lower in all 3 parameter sets; `search_type_month`: PostgreSQL lower, 1.2 times (PostgreSQL 2.9863 ms, MongoDB 3.4829 ms); the lower database differs between parameter sets (MongoDB, PostgreSQL, PostgreSQL in table order); `search_price_sort`: MongoDB lower, 2.6 times (PostgreSQL 13.5382 ms, MongoDB 5.1185 ms); MongoDB is lower in all 3 parameter sets; `search_unfiltered`: within 10 per cent (PostgreSQL 87.4375 ms, MongoDB 84.3572 ms); the lower database differs between parameter sets (PostgreSQL, MongoDB, MongoDB in table order); `trends_series`: MongoDB lower, 5.9 times (PostgreSQL 68.2094 ms, MongoDB 11.5038 ms); MongoDB is lower in all 3 parameter sets; `rankings_year`: MongoDB lower, 1.4 times (PostgreSQL 110.3524 ms, MongoDB 77.6392 ms); MongoDB is lower in all 3 parameter sets.

`search_filtered` runs against the extra MongoDB `{town, flat_type, txn_month}` index, which favours MongoDB; the PostgreSQL side has no equivalent.

### 1.2 Embedded document against join: `building_page`

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `building_page` | 275A-bishan-st-24 | 0.6454 | 1.0785 | 1.1330 | 1 | 0.3089 | 1.2492 | 0 | 1 |
| `building_page` | 66-marine-dr | 0.6196 | 0.9730 | 0.8770 | 1 | 0.2944 | 0.4085 | 0 | 1 |
| `building_page` | 4-queens-rd | 0.6348 | 1.9320 | 0.8820 | 1 | 0.2898 | 0.3835 | 0 | 1 |
| `building_page` | **chart value** | **0.6348** | **1.9320** | - | - | **0.2944** | **1.2492** | - | - |

Reading: `building_page`: MongoDB lower, 2.2 times (PostgreSQL 0.6348 ms, MongoDB 0.2944 ms); MongoDB is lower in all 3 parameter sets.

PostgreSQL joins the live tables; MongoDB reads one `building_profiles` document.

### 1.3 Embedded document against `$lookup`: `building_recent_lookup` (MongoDB only)

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `building_recent_lookup` | 275A-bishan-st-24 | - | - | - | - | 0.4868 | 0.6994 | 0 | 10 |
| `building_recent_lookup` | 66-marine-dr | - | - | - | - | 0.4774 | 0.7006 | 0 | 10 |
| `building_recent_lookup` | 4-queens-rd | - | - | - | - | 0.4675 | 0.5933 | 0 | 10 |
| `building_recent_lookup` | **chart value** | - | - | - | - | **0.4774** | **0.7006** | - | - |

Reading: `building_recent_lookup`: MongoDB only, chart value 0.4774 ms (p95 up to 0.7006 ms).

### 1.4 Precomputed summaries: `town_stats_read` and `stats_rebuild` (not result-equivalent)

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `town_stats_read` | bishan-4room | 0.1415 | 0.3317 | 0.1690 | 117 | 1.5084 | 1.7421 | 0 | 291 |
| `town_stats_read` | marine-parade-3room | 0.1552 | 0.2417 | 0.2080 | 116 | 0.3998 | 0.5689 | 0 | 78 |
| `town_stats_read` | tampines-4room | 0.1553 | 0.2421 | 0.2140 | 117 | 4.5171 | 19.9785 | 0 | 1,089 |
| `town_stats_read` | **chart value** | **0.1552** | **0.3317** | - | - | **1.5084** | **19.9785** | - | - |
| `stats_rebuild` | rep-1 | 165.0084 | 173.1188 | - | 1 | 2,355.5693 | 4,027.6164 | - | 1 |
| `stats_rebuild` | rep-2 | 164.0061 | 172.2618 | - | 1 | 2,203.6484 | 4,081.0217 | - | 1 |
| `stats_rebuild` | rep-3 | 166.1173 | 178.0214 | - | 1 | 2,360.8864 | 4,024.4336 | - | 1 |
| `stats_rebuild` | **chart value** | **165.0084** | **178.0214** | - | - | **2,355.5693** | **4,081.0217** | - | - |

Reading: `town_stats_read`: PostgreSQL lower, 9.7 times (PostgreSQL 0.1552 ms, MongoDB 1.5084 ms); PostgreSQL is lower in all 3 parameter sets; `stats_rebuild`: PostgreSQL lower, 14.3 times (PostgreSQL 165.0084 ms, MongoDB 2,355.5693 ms); PostgreSQL is lower in all 3 parameter sets.

Grain: these are not result-equivalent. `town_stats_read` in PostgreSQL reads the month rows of one materialised view for a town and flat type (about 117 town-month rows); in MongoDB it reads `stats.by_flat_type` of every block in the town, so its row count is the number of blocks in the town and varies by town (see the rows columns). `stats_rebuild` in PostgreSQL is `REFRESH ... CONCURRENTLY` of one materialised view; in MongoDB it is a reset and `$merge` over about 13k building profiles (13,358 buildings in `docs/import_report.md`). The two rebuilds do different amounts of work, so the ratio is not a like-for-like speed comparison.

### 1.5 Geo: `food_town`

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `food_town` | bishan | 51.2432 | 52.7100 | 51.3020 | 238 | 296.4208 | 312.1309 | 298 | 238 |
| `food_town` | marine-parade | 12.7974 | 13.8360 | 12.7250 | 59 | 85.6045 | 89.7484 | 88 | 59 |
| `food_town` | tampines | 196.0395 | 200.3669 | 197.0930 | 924 | 1,154.1767 | 1,363.3234 | 1,179 | 924 |
| `food_town` | **chart value** | **51.2432** | **200.3669** | - | - | **296.4208** | **1,363.3234** | - | - |

Reading: `food_town`: PostgreSQL lower, 5.8 times (PostgreSQL 51.2432 ms, MongoDB 296.4208 ms); PostgreSQL is lower in all 3 parameter sets.

### 1.6 Median: `median_exact` and `median_approx` (MongoDB only for the approximate one)

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `median_exact` | 4room-2022 | 1.7022 | 1.8873 | 2.3140 | 1 | 8.2638 | 9.0416 | 6 | 1 |
| `median_exact` | 5room-2023 | 0.9676 | 1.0893 | 1.3290 | 1 | 5.0292 | 5.5437 | 1 | 1 |
| `median_exact` | 3room-2019 | 0.9238 | 1.1445 | 1.1910 | 1 | 4.0833 | 4.4002 | 3 | 1 |
| `median_exact` | **chart value** | **0.9676** | **1.8873** | - | - | **5.0292** | **9.0416** | - | - |
| `median_approx` | 4room-2022 | - | - | - | - | 7.8368 | 11.4931 | 9 | 1 |
| `median_approx` | 5room-2023 | - | - | - | - | 4.9845 | 5.6677 | 3 | 1 |
| `median_approx` | 3room-2019 | - | - | - | - | 4.4333 | 5.3317 | 3 | 1 |
| `median_approx` | **chart value** | - | - | - | - | **4.9845** | **11.4931** | - | - |

Reading: `median_exact`: PostgreSQL lower, 5.2 times (PostgreSQL 0.9676 ms, MongoDB 5.0292 ms); PostgreSQL is lower in all 3 parameter sets; `median_approx`: MongoDB only, chart value 4.9845 ms (p95 up to 11.4931 ms).

`median_exact` returns the lower median, an observed price, from both databases. `median_approx` is MongoDB only and has no PostgreSQL pair.

### 1.7 Write: `admin_record_delete`

| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms | Mongo server ms | Mongo rows |
|---|---|---|---|---|---|---|---|---|---|
| `admin_record_delete` | 275A-bishan-st-24 | 4.9987 | 6.0721 | - | 1 | 11.5682 | 14.5950 | - | 1 |
| `admin_record_delete` | 66-marine-dr | 5.3276 | 6.9219 | - | 1 | 10.8278 | 13.7231 | - | 1 |
| `admin_record_delete` | 4-queens-rd | 5.1147 | 6.1887 | - | 1 | 12.5837 | 14.4169 | - | 1 |
| `admin_record_delete` | **chart value** | **5.1147** | **6.9219** | - | - | **11.5682** | **14.5950** | - | - |

Reading: `admin_record_delete`: write half only, PostgreSQL chart value 5.1147 ms and MongoDB 11.5682 ms. The two write halves do different work (MongoDB's includes the block stats recomputation, PostgreSQL's does not), so no verdict on which is lower is drawn from them.

The table is the write half only; the PostgreSQL refresh that follows each delete is in section 5.

## 2. Throughput (full data)

![Throughput by clients](throughput.png)

Each level ran for about 30 s with 1, 4 and 8 worker processes (one client each), on the same machine as the databases, so clients and servers share the CPUs.

| Case | Database | Clients | Calls | Calls/s | Median ms | p95 ms |
|---|---|---|---|---|---|---|
| `search_filtered` | PostgreSQL | 1 | 4,794 | 159.8 | 5.4080 | 8.8629 |
| `search_filtered` | PostgreSQL | 4 | 16,763 | 558.6 | 6.2793 | 10.2309 |
| `search_filtered` | PostgreSQL | 8 | 27,674 | 922.1 | 7.9286 | 13.0990 |
| `search_filtered` | MongoDB | 1 | 12,073 | 402.4 | 2.2229 | 3.5414 |
| `search_filtered` | MongoDB | 4 | 46,738 | 1,557.8 | 2.2419 | 3.6057 |
| `search_filtered` | MongoDB | 8 | 74,434 | 2,480.9 | 3.0659 | 4.9767 |
| `building_page` | PostgreSQL | 1 | 42,289 | 1,409.6 | 0.6638 | 0.9603 |
| `building_page` | PostgreSQL | 4 | 114,443 | 3,814.6 | 1.0243 | 1.2643 |
| `building_page` | PostgreSQL | 8 | 175,893 | 5,862.6 | 1.3527 | 1.5788 |
| `building_page` | MongoDB | 1 | 94,006 | 3,133.5 | 0.3002 | 0.4194 |
| `building_page` | MongoDB | 4 | 314,879 | 10,495.9 | 0.3747 | 0.4780 |
| `building_page` | MongoDB | 8 | 444,146 | 14,802.9 | 0.5180 | 0.7058 |

Reading: `search_filtered`: PostgreSQL 159.8 calls/s at 1 client and 922.1 at 8 (5.8 times); MongoDB 402.4 calls/s at 1 client and 2,480.9 at 8 (6.2 times). MongoDB calls/s over PostgreSQL calls/s: 2.5 at 1, 2.8 at 4, 2.7 at 8 clients. `building_page`: PostgreSQL 1,409.6 calls/s at 1 client and 5,862.6 at 8 (4.2 times); MongoDB 3,133.5 calls/s at 1 client and 14,802.9 at 8 (4.7 times). MongoDB calls/s over PostgreSQL calls/s: 2.2 at 1, 2.8 at 4, 2.5 at 8 clients. This is two read cases only, on one machine, with the extra MongoDB index favouring `search_filtered`; it is not a capacity figure.

## 3. Scaling with the amount of data

![Median latency at 25, 50 and 100 per cent of the sales](scaling.png)

Loaded sales: 60,339 in both databases at 25 per cent (fraction 0.249999), 120,678 at 50 per cent (fraction 0.499998) and 241,357 at 100 per cent. Only the sales are filtered; the buildings, stats and everything else are the full snapshot. Only the four cases run at all three fractions are charted and tabulated; `scaling.csv` also holds the other full-data rows.

| Case | Database | Chart value 25% ms | Chart value 50% ms | Chart value 100% ms | 100% over 25% | Set medians at 25% ms | Set medians at 50% ms | Set medians at 100% ms |
|---|---|---|---|---|---|---|---|---|
| `search_filtered` | PostgreSQL | **2.5675** | **4.4724** | **5.0538** | 1.97 | 1.6977 / 2.5675 / 2.6159 | 2.8227 / 4.9474 / 4.4724 | 5.0538 / 8.2138 / 4.5574 |
| `search_filtered` | MongoDB | **1.3557** | **1.4811** | **1.8084** | 1.33 | 1.3201 / 1.3557 / 1.6620 | 1.4811 / 1.4798 / 2.0976 | 1.8084 / 1.7788 / 3.0268 |
| `trends_series` | PostgreSQL | **19.4123** | **35.1424** | **68.2094** | 3.51 | 19.4123 / 6.8555 / 28.0271 | 35.1424 / 13.2599 / 46.3016 | 68.2094 / 25.6249 / 84.8229 |
| `trends_series` | MongoDB | **5.7794** | **7.6621** | **11.5038** | 1.99 | 5.7794 / 2.7542 / 10.7248 | 7.6621 / 3.4558 / 17.4970 | 11.5038 / 4.3589 / 30.6689 |
| `rankings_year` | PostgreSQL | **25.9348** | **50.0068** | **110.3524** | 4.25 | 28.4344 / 10.7744 / 25.9348 | 55.3481 / 20.8397 / 50.0068 | 120.8439 / 37.6246 / 110.3524 |
| `rankings_year` | MongoDB | **23.0972** | **42.3666** | **77.6392** | 3.36 | 23.0972 / 4.7340 / 23.3840 | 42.3666 / 8.6489 / 43.7928 | 77.6392 / 15.9929 / 80.2666 |
| `building_page` | PostgreSQL | **0.6371** | **0.6926** | **0.6348** | 1.00 | 0.6371 / 0.6438 / 0.5878 | 0.7361 / 0.6634 / 0.6926 | 0.6454 / 0.6196 / 0.6348 |
| `building_page` | MongoDB | **0.3301** | **0.3491** | **0.2944** | 0.89 | 0.3301 / 0.3006 / 0.3331 | 0.3296 / 0.3491 / 0.3672 | 0.3089 / 0.2944 / 0.2898 |

Parameter sets in the last three columns are in the order of the full-data tables in section 1 (`/` separates the three sets).

Reading: `search_filtered`: chart value at 100 per cent over chart value at 25 per cent is 1.97 for PostgreSQL and 1.33 for MongoDB; `trends_series`: chart value at 100 per cent over chart value at 25 per cent is 3.51 for PostgreSQL and 1.99 for MongoDB; `rankings_year`: chart value at 100 per cent over chart value at 25 per cent is 4.25 for PostgreSQL and 3.36 for MongoDB; `building_page`: chart value at 100 per cent over chart value at 25 per cent is 1.00 for PostgreSQL and 0.89 for MongoDB. Each set median is over 50 calls; the chart value is the median of the three.

## 4. Index on and off

![Median latency with and without the index](index_on_off.png)

`bench.explain` runs each case with and without its index, one method for both arms, as `neighboar_owner` (PostgreSQL, inside a transaction that is always rolled back) and `etl` (MongoDB, index recreated afterwards). Each database is compared only with itself; MongoDB also has the `{town, flat_type, txn_month}` index and the two sets of indexes are not equivalent. The chart value is again the median of the three parameter sets' medians. The index named in the table is the one dropped in the off arm.

| Case | Database | Index dropped | On: chart value ms | On: largest p95 ms | Off: chart value ms | Off: largest p95 ms | Off / on |
|---|---|---|---|---|---|---|---|
| `search_type_month` | PostgreSQL | `resale_transaction_type_month_idx` | **2.6159** | 3.8468 | **16.2331** | 23.0726 | 6.2 |
| `search_type_month` | MongoDB | `type_month` | **3.4618** | 4.3763 | **89.1778** | 156.9175 | 25.8 |
| `search_price_sort` | PostgreSQL | `resale_transaction_type_price_idx` | **12.9495** | 21.0977 | **40.0340** | 66.7469 | 3.1 |
| `search_price_sort` | MongoDB | `type_price` | **5.0927** | 8.1229 | **39.5878** | 68.8580 | 7.8 |
| `median_exact` | PostgreSQL | `resale_transaction_type_month_idx` | **0.9475** | 1.8746 | **5.5635** | 9.4075 | 5.9 |
| `median_exact` | MongoDB | `type_month` | **4.6036** | 8.8269 | **43.4958** | 79.8550 | 9.4 |

### Plans, per parameter set

Plans are from `EXPLAIN` of the captured statements (`f100/explain/`). `search_*` cases run two statements, a count and a page; `median_exact` runs one. PostgreSQL primary-key lookups are left out of the index list. A plan with no non-key index scans the table.

| Case | Database | Parameter set | Arm | Median ms | p95 ms | Server ms | Dropped index in plan | Indexes in plan |
|---|---|---|---|---|---|---|---|---|
| `search_type_month` | PostgreSQL | 4room-2022-q1 | on | 3.3351 | 3.8468 | 4.7600 | yes | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_month_idx` |
| `search_type_month` | PostgreSQL | 4room-2022-q1 | off | 21.5633 | 23.0726 | 14.9850 | no | count: no non-key index; page: no non-key index |
| `search_type_month` | PostgreSQL | 5room-2023-h2 | on | 2.6159 | 2.9818 | 4.1240 | yes | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_month_idx` |
| `search_type_month` | PostgreSQL | 5room-2023-h2 | off | 16.2331 | 16.9577 | 13.9860 | no | count: `resale_transaction_type_price_idx`; page: no non-key index |
| `search_type_month` | PostgreSQL | 3room-2017-h1 | on | 2.5193 | 2.8451 | 3.6630 | yes | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_month_idx` |
| `search_type_month` | PostgreSQL | 3room-2017-h1 | off | 15.8233 | 16.5762 | 16.2030 | no | count: `resale_transaction_type_price_idx`; page: `resale_transaction_type_price_idx` |
| `search_type_month` | MongoDB | 4room-2022-q1 | on | 3.4618 | 3.9049 | 6 | yes | count: `type_month`; page: `type_month` |
| `search_type_month` | MongoDB | 4room-2022-q1 | off | 146.9243 | 156.9175 | 153 | no | count: `type_price`; page: `type_price` |
| `search_type_month` | MongoDB | 5room-2023-h2 | on | 3.4787 | 4.3763 | 7 | yes | count: `type_month`; page: `type_month` |
| `search_type_month` | MongoDB | 5room-2023-h2 | off | 89.1778 | 95.0735 | 89 | no | count: `type_price`; page: `type_price` |
| `search_type_month` | MongoDB | 3room-2017-h1 | on | 3.1553 | 4.0052 | 7 | yes | count: `type_month`; page: `type_month` |
| `search_type_month` | MongoDB | 3room-2017-h1 | off | 79.8609 | 88.3634 | 78 | no | count: `type_price`; page: `type_price` |
| `search_price_sort` | PostgreSQL | 4room-price-desc | on | 19.6987 | 21.0977 | 30.0290 | yes | count: no non-key index; page: `resale_transaction_type_price_idx` |
| `search_price_sort` | PostgreSQL | 4room-price-desc | off | 64.6697 | 66.7469 | 72.4910 | no | count: no non-key index; page: no non-key index |
| `search_price_sort` | PostgreSQL | 5room-price-asc | on | 12.9495 | 13.3551 | 24.9620 | yes | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_price_idx` |
| `search_price_sort` | PostgreSQL | 5room-price-asc | off | 40.0340 | 41.6282 | 51.7720 | no | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_month_idx` |
| `search_price_sort` | PostgreSQL | executive-price-desc | on | 5.2458 | 5.8044 | 9.0840 | yes | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_price_idx` |
| `search_price_sort` | PostgreSQL | executive-price-desc | off | 14.8372 | 15.6057 | 23.2460 | no | count: `resale_transaction_type_month_idx`; page: `resale_transaction_type_month_idx` |
| `search_price_sort` | MongoDB | 4room-price-desc | on | 7.8387 | 8.1229 | 7 | yes | count: `type_price`; page: `type_price` |
| `search_price_sort` | MongoDB | 4room-price-desc | off | 66.8232 | 68.8580 | 66 | no | count: `type_month`; page: `type_month` |
| `search_price_sort` | MongoDB | 5room-price-asc | on | 5.0927 | 5.4732 | 4 | yes | count: `type_price`; page: `type_price` |
| `search_price_sort` | MongoDB | 5room-price-asc | off | 39.5878 | 42.1464 | 38 | no | count: `type_month`; page: `type_month` |
| `search_price_sort` | MongoDB | executive-price-desc | on | 2.3898 | 2.9715 | 1 | yes | count: `type_price`; page: `type_price` |
| `search_price_sort` | MongoDB | executive-price-desc | off | 14.2818 | 15.3408 | 12 | no | count: `type_month`; page: `type_month` |
| `median_exact` | PostgreSQL | 4room-2022 | on | 1.6739 | 1.8746 | 2.2510 | yes | statement: `resale_transaction_type_month_idx` |
| `median_exact` | PostgreSQL | 4room-2022 | off | 8.9722 | 9.4075 | 9.9840 | no | statement: `resale_transaction_type_price_idx` |
| `median_exact` | PostgreSQL | 5room-2023 | on | 0.9475 | 1.1322 | 1.2950 | yes | statement: `resale_transaction_type_month_idx` |
| `median_exact` | PostgreSQL | 5room-2023 | off | 5.5635 | 6.9774 | 6.1630 | no | statement: `resale_transaction_type_price_idx` |
| `median_exact` | PostgreSQL | 3room-2019 | on | 0.8501 | 0.9914 | 1.0930 | yes | statement: `resale_transaction_type_month_idx` |
| `median_exact` | PostgreSQL | 3room-2019 | off | 5.4182 | 5.6701 | 7.7410 | no | statement: `resale_transaction_type_price_idx` |
| `median_exact` | MongoDB | 4room-2022 | on | 8.3405 | 8.8269 | 6 | yes | statement: `type_month` |
| `median_exact` | MongoDB | 4room-2022 | off | 71.4666 | 79.8550 | 77 | no | statement: `type_price` |
| `median_exact` | MongoDB | 5room-2023 | on | 4.6036 | 4.8383 | 2 | yes | statement: `type_month` |
| `median_exact` | MongoDB | 5room-2023 | off | 43.4958 | 46.4059 | 42 | no | statement: `type_price` |
| `median_exact` | MongoDB | 3room-2019 | on | 4.0164 | 4.1847 | 1 | yes | statement: `type_month` |
| `median_exact` | MongoDB | 3room-2019 | off | 39.8040 | 41.8834 | 37 | no | statement: `type_price` |

Reading: `search_type_month`, PostgreSQL: `resale_transaction_type_month_idx` is in the plan in 3 of 3 sets with it and 0 of 3 without it; without it the plans name `resale_transaction_type_price_idx` and 1 set(s) name no non-key index at all; `search_type_month`, MongoDB: `type_month` is in the plan in 3 of 3 sets with it and 0 of 3 without it; without it the plans name `type_price`; `search_price_sort`, PostgreSQL: `resale_transaction_type_price_idx` is in the plan in 3 of 3 sets with it and 0 of 3 without it; without it the plans name `resale_transaction_type_month_idx` and 1 set(s) name no non-key index at all; `search_price_sort`, MongoDB: `type_price` is in the plan in 3 of 3 sets with it and 0 of 3 without it; without it the plans name `type_month`; `median_exact`, PostgreSQL: `resale_transaction_type_month_idx` is in the plan in 3 of 3 sets with it and 0 of 3 without it; without it the plans name `resale_transaction_type_price_idx`; `median_exact`, MongoDB: `type_month` is in the plan in 3 of 3 sets with it and 0 of 3 without it; without it the plans name `type_price`.

Without the `(flat_type, month)` index both databases fall back to the neighbouring price index where the plan still uses an index (`resale_transaction_type_price_idx` in PostgreSQL, `type_price` in MongoDB), as recorded in PROJECT_PROGRESS 4.1b; the rows above show which parameter sets did so. For `search_price_sort` the dropped index is the price index and the fallback is the month index.

Reading of the latencies: `search_type_month` PostgreSQL 6.2 times slower without the index (2.6159 ms to 16.2331 ms); `search_type_month` MongoDB 25.8 times slower without the index (3.4618 ms to 89.1778 ms); `search_price_sort` PostgreSQL 3.1 times slower without the index (12.9495 ms to 40.0340 ms); `search_price_sort` MongoDB 7.8 times slower without the index (5.0927 ms to 39.5878 ms); `median_exact` PostgreSQL 5.9 times slower without the index (0.9475 ms to 5.5635 ms); `median_exact` MongoDB 9.4 times slower without the index (4.6036 ms to 43.4958 ms).

## 5. `admin_record_delete`: write and refresh separately

Each timed run records one sale and then deletes it (`bench/cases.yaml`). PostgreSQL times the write and the materialised view refresh separately; MongoDB records and deletes in transactions that recompute the block stats, so it has no separate refresh part. The refresh is not charted. Client timing only.

| Parameter set | PG write median ms | PG write p95 ms | PG refresh median ms | PG refresh p95 ms | Mongo write median ms | Mongo write p95 ms |
|---|---|---|---|---|---|---|
| 275A-bishan-st-24 | 4.9987 | 6.0721 | 162.8449 | 167.1220 | 11.5682 | 14.5950 |
| 66-marine-dr | 5.3276 | 6.9219 | 164.5050 | 174.5086 | 10.8278 | 13.7231 |
| 4-queens-rd | 5.1147 | 6.1887 | 164.4209 | 178.8902 | 12.5837 | 14.4169 |
| **chart value (write); refresh is the same rule** | **5.1147** | **6.9219** | **164.4209** | **178.8902** | **11.5682** | **14.5950** |

Reading: the PostgreSQL write has a chart value of 5.1147 ms and MongoDB's write 11.5682 ms; the PostgreSQL refresh that follows has a chart value of 164.4209 ms. The two sides recompute different things: the PostgreSQL refresh is `REFRESH ... CONCURRENTLY` of the whole town-month view over all 241,357 sales, whereas MongoDB recomputes the stats of one block inside the record and delete transactions. Neither pairing (write against write, or MongoDB write against PostgreSQL write plus refresh) is a like-for-like speed comparison.

## 6. Files

- `f100/summary.csv`, `f100/throughput.csv`, `f100/index_on_off.csv`, `scaling.csv`: the tables above.
- `f100/raw.jsonl`, `f50/raw.jsonl`, `f25/raw.jsonl`: every timed call.
- `f100/env.json`, `f50/env.json`, `f25/env.json`: software, settings, invocations and verify results.
- `f100/explain/`: captured statements and plans (text and JSON), including the index-off arms.
- `latency_by_case`, `throughput`, `scaling`, `index_on_off` as `.png` (300 dpi, 16 cm wide) and `.svg`, from `python -m bench.charts`.
