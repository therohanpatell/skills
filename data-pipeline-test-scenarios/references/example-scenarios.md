# Example Test Scenarios — Data Pipeline QA Reference
## GCP Batch Platform · Ingestion / FDP / CDP Layers

Fully worked examples illustrating the required level of detail. All examples
use the project's actual technology stack: GCS → BigQuery, IBM TWS → Airflow,
batch processing only.

---

## Example Context (Hypothetical Pipeline)

**Pipeline**: Customer Sales Data Pipeline  
**Ingestion**: `gs://raw-data-bucket/sales/sales_YYYYMMDD.csv` → `raw_dataset.sales_raw`  
**FDP**: `raw_dataset.sales_raw` → `fdp_dataset.sales_fdp` (with SCD Type 2 on customer dimension)  
**CDP**: `fdp_dataset.sales_fdp` → `cdp_dataset.sales_summary` (daily aggregation by region)  
**Orchestration**: IBM TWS job `TWS_SALES_INGEST` → Airflow DAG `dag_sales_ingestion`  
               → IBM TWS job `TWS_SALES_FDP` → Airflow DAG `dag_sales_fdp`  
               → IBM TWS job `TWS_SALES_CDP` → Airflow DAG `dag_sales_cdp`  
**SLA**: Ingestion by 06:00 UTC · FDP by 07:00 UTC · CDP by 08:00 UTC  

---

## Ingestion Layer Examples

---

```
TEST SCENARIO ID    : ING-FHP-001
TITLE               : Valid CSV file is loaded end-to-end into BQ raw table with correct field mapping

LAYER               : Ingestion Layer
CATEGORY            : Functional / Happy Path
COMPONENT UNDER TEST: GCS File Pickup → BigQuery Raw Load (dag_sales_ingestion, full flow)

PRIORITY            : Critical

PRECONDITIONS       :
  - IBM TWS job TWS_SALES_INGEST is active and scheduled for 05:00 UTC.
  - Airflow DAG dag_sales_ingestion is unpaused in Cloud Composer.
  - GCS bucket gs://raw-data-bucket/sales/ is accessible by service account sa-pipeline@project.iam.
  - BQ table raw_dataset.sales_raw exists with correct schema (see design doc Appendix A).
  - No rows exist in raw_dataset.sales_raw for batch_date = '2024-06-01'.

TEST DATA           :
  - File name: sales_20240601.csv
  - GCS path: gs://raw-data-bucket/sales/sales_20240601.csv
  - File contents (pipe-delimited, UTF-8, with header row):
      sale_id|customer_id|region|product_code|sale_amount|sale_date
      1001|C-500|SOUTH|PRD-A|1500.00|2024-06-01
      1002|C-501|NORTH|PRD-B|250.75|2024-06-01
      1003|C-502|EAST|PRD-A|980.00|2024-06-01
  - Row count: 3 data rows (excluding header)

TEST STEPS          :
  1. Upload sales_20240601.csv to gs://raw-data-bucket/sales/ using gsutil or GCS console.
  2. Submit IBM TWS job TWS_SALES_INGEST manually (or wait for 05:00 UTC scheduled trigger).
  3. Confirm TWS job triggers Airflow DAG dag_sales_ingestion in Cloud Composer UI.
  4. Monitor Cloud Composer UI for DAG dag_sales_ingestion until all tasks show "success".
  5. Query BQ:
       SELECT sale_id, customer_id, region, product_code, sale_amount, sale_date,
              source_file_name, pipeline_run_id, insert_timestamp
       FROM raw_dataset.sales_raw
       WHERE batch_date = '2024-06-01'
       ORDER BY sale_id;
  6. Verify IBM TWS job TWS_SALES_INGEST shows status "Successful" in TWS console.

EXPECTED RESULT     :
  - Cloud Composer DAG dag_sales_ingestion all tasks = "success".
  - IBM TWS job TWS_SALES_INGEST status = "Successful".
  - BQ query returns exactly 3 rows.
  - Row 1: sale_id=1001, customer_id='C-500', region='SOUTH', product_code='PRD-A',
            sale_amount=1500.00, sale_date=DATE '2024-06-01'
  - Row 2: sale_id=1002, customer_id='C-501', region='NORTH', product_code='PRD-B',
            sale_amount=250.75, sale_date=DATE '2024-06-01'
  - Row 3: sale_id=1003, customer_id='C-502', region='EAST', product_code='PRD-A',
            sale_amount=980.00, sale_date=DATE '2024-06-01'
  - source_file_name = 'sales_20240601.csv' for all 3 rows.
  - insert_timestamp is non-null and within 30 seconds of DAG completion time.
  - pipeline_run_id is non-null and consistent across all 3 rows.

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : Baseline smoke test — must pass before executing any other scenarios.
                      Failure here blocks all downstream FDP and CDP testing.
```

---

```
TEST SCENARIO ID    : ING-RECON-001
TITLE               : Recon passes when file row count matches BQ raw table row count

LAYER               : Ingestion Layer
CATEGORY            : Reconciliation
COMPONENT UNDER TEST: Recon Task in Airflow DAG dag_sales_ingestion

PRIORITY            : Critical

PRECONDITIONS       :
  - Airflow DAG dag_sales_ingestion is unpaused.
  - BQ table raw_dataset.sales_raw exists.
  - BQ recon/audit table raw_dataset.pipeline_recon exists (schema: pipeline_name, run_id,
    batch_date, source_count, target_count, recon_status, recon_timestamp).
  - No rows exist in raw_dataset.sales_raw for batch_date = '2024-06-03'.

TEST DATA           :
  - File name: sales_20240603.csv
  - GCS path: gs://raw-data-bucket/sales/sales_20240603.csv
  - File contains exactly 1000 data rows (header excluded).

TEST STEPS          :
  1. Upload sales_20240603.csv (1000 data rows) to gs://raw-data-bucket/sales/.
  2. Submit IBM TWS job TWS_SALES_INGEST manually.
  3. Monitor DAG dag_sales_ingestion to completion.
  4. Query BQ raw table:
       SELECT COUNT(*) AS bq_count FROM raw_dataset.sales_raw
       WHERE batch_date = '2024-06-03';
  5. Query BQ recon table:
       SELECT pipeline_name, batch_date, source_count, target_count, recon_status
       FROM raw_dataset.pipeline_recon
       WHERE pipeline_name = 'SALES_INGESTION' AND batch_date = '2024-06-03';

EXPECTED RESULT     :
  - DAG dag_sales_ingestion all tasks = "success" including the recon task.
  - bq_count = 1000.
  - Recon table row: source_count = 1000, target_count = 1000, recon_status = 'PASS'.
  - IBM TWS job TWS_SALES_INGEST = "Successful", and downstream TWS_SALES_FDP job
    is triggered (not held).

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : [ASSUMPTION] Recon counts file data rows excluding the header row.
                      Confirm with data engineer whether recon uses a footer record count
                      (some source systems embed record count in the last row) or a
                      physical file row count minus 1 (header).
```

---

```
TEST SCENARIO ID    : ING-RECON-002
TITLE               : Recon failure when BQ row count is less than file row count — downstream FDP job is held

LAYER               : Ingestion Layer
CATEGORY            : Reconciliation
COMPONENT UNDER TEST: Recon Task + IBM TWS Dependency Hold

PRIORITY            : Critical

PRECONDITIONS       :
  - Airflow DAG dag_sales_ingestion is unpaused.
  - Ability to simulate a partial BQ load (e.g., by injecting a bad row that causes
    a load error on row 500 of 1000, or by using a pre-arranged test file with 1 row
    that will fail schema validation while 999 pass).
  - IBM TWS job TWS_SALES_FDP is configured as a dependent successor of TWS_SALES_INGEST.

TEST DATA           :
  - File name: sales_20240604.csv
  - Total rows in file: 1000
  - Row 500: sale_amount = 'INVALID' (non-numeric — will be rejected by BQ)
  - Remaining 999 rows: all schema-valid

TEST STEPS          :
  1. Upload sales_20240604.csv to gs://raw-data-bucket/sales/.
  2. Submit IBM TWS job TWS_SALES_INGEST manually.
  3. Monitor DAG dag_sales_ingestion.
  4. After DAG run ends (success or failed), query BQ raw table:
       SELECT COUNT(*) FROM raw_dataset.sales_raw WHERE batch_date = '2024-06-04';
  5. Query recon table:
       SELECT source_count, target_count, recon_status
       FROM raw_dataset.pipeline_recon
       WHERE pipeline_name = 'SALES_INGESTION' AND batch_date = '2024-06-04';
  6. In IBM TWS console, verify status of job TWS_SALES_FDP.

EXPECTED RESULT     :
  - BQ raw table count for batch_date='2024-06-04' = 999 (1 row rejected).
  - Recon table: source_count = 1000, target_count = 999, recon_status = 'FAIL'.
  - DAG dag_sales_ingestion recon task = "failed" (or "success" with an alert raised
    per design doc — confirm expected task status with data engineer).
  - IBM TWS job TWS_SALES_INGEST = "Failed" (or "Error").
  - IBM TWS job TWS_SALES_FDP = NOT triggered; remains in "Hold" or "Waiting" state.
  - Alert/notification sent per configured alerting mechanism (email / monitoring tool).

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : [DESIGN GAP] Design doc does not specify whether the Airflow recon
                      task should be marked as "failed" (causing DAG failure) or "success"
                      with a downstream alert. Clarify with architect — this determines
                      whether TWS sees a failure signal. Until clarified, test both behaviors.
```

---

```
TEST SCENARIO ID    : ING-FILE-001
TITLE               : Pipeline raises correct error when source CSV file is not present in GCS at trigger time

LAYER               : Ingestion Layer
CATEGORY            : File Handling
COMPONENT UNDER TEST: GCS File Existence Check Task in dag_sales_ingestion

PRIORITY            : Critical

PRECONDITIONS       :
  - Airflow DAG dag_sales_ingestion is unpaused.
  - GCS path gs://raw-data-bucket/sales/ contains NO file matching pattern sales_20240605.csv.
  - IBM TWS job TWS_SALES_FDP is a dependent successor of TWS_SALES_INGEST.

TEST DATA           :
  - No file — test verifies behavior when expected file is absent.
  - batch_date for this run: 2024-06-05.

TEST STEPS          :
  1. Confirm gs://raw-data-bucket/sales/sales_20240605.csv does NOT exist.
  2. Submit IBM TWS job TWS_SALES_INGEST manually.
  3. Monitor Cloud Composer DAG dag_sales_ingestion.
  4. Check Airflow task logs for the file-check task.
  5. Check IBM TWS console for status of TWS_SALES_INGEST and TWS_SALES_FDP.
  6. Check configured alerting channel for failure notification.

EXPECTED RESULT     :
  - Airflow task responsible for checking file existence = "failed".
  - DAG dag_sales_ingestion overall status = "failed".
  - Airflow task logs contain error message referencing the missing file path:
    e.g., "File not found: gs://raw-data-bucket/sales/sales_20240605.csv"
  - NO rows inserted into raw_dataset.sales_raw for batch_date = '2024-06-05'.
  - IBM TWS job TWS_SALES_INGEST = "Failed".
  - IBM TWS job TWS_SALES_FDP = NOT triggered (remains in Hold/Waiting state).
  - Alert notification sent to configured channel within 5 minutes of failure.

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : This is the most common production failure mode. Alert routing
                      must be verified — confirm who receives the alert (operations team,
                      on-call engineer) and via which channel per design doc Section 6.
```

---

## FDP Layer Examples

---

```
TEST SCENARIO ID    : FDP-SCD2-001
TITLE               : SCD2 — New business key loaded for the first time creates a single active row

LAYER               : FDP Layer
CATEGORY            : SCD Type 2 Logic
COMPONENT UNDER TEST: SCD2 MERGE Logic in Airflow DAG dag_sales_fdp

PRIORITY            : Critical

PRECONDITIONS       :
  - Airflow DAG dag_sales_fdp is unpaused.
  - BQ table fdp_dataset.sales_fdp exists with SCD2 columns:
    customer_sk (surrogate key), customer_id (business key), customer_name,
    customer_region, effective_from (DATE), effective_to (DATE), is_current (BOOL).
  - No rows exist in fdp_dataset.sales_fdp for customer_id = 'C-NEW-001'.
  - BQ raw table raw_dataset.sales_raw contains one row for customer_id = 'C-NEW-001':
      customer_id='C-NEW-001', customer_name='Acme Corp', customer_region='SOUTH',
      batch_date='2024-06-01'

TEST DATA           :
  - Source row in raw_dataset.sales_raw:
      customer_id = 'C-NEW-001'
      customer_name = 'Acme Corp'
      customer_region = 'SOUTH'
      batch_date = 2024-06-01

TEST STEPS          :
  1. Confirm no rows for customer_id='C-NEW-001' exist in fdp_dataset.sales_fdp:
       SELECT COUNT(*) FROM fdp_dataset.sales_fdp WHERE customer_id = 'C-NEW-001';
     Expected COUNT = 0.
  2. Submit IBM TWS job TWS_SALES_FDP manually for batch_date = 2024-06-01.
  3. Monitor DAG dag_sales_fdp until all tasks = "success".
  4. Query fdp_dataset.sales_fdp:
       SELECT customer_sk, customer_id, customer_name, customer_region,
              effective_from, effective_to, is_current
       FROM fdp_dataset.sales_fdp
       WHERE customer_id = 'C-NEW-001';

EXPECTED RESULT     :
  - Exactly 1 row returned for customer_id = 'C-NEW-001'.
  - customer_name = 'Acme Corp'
  - customer_region = 'SOUTH'
  - effective_from = DATE '2024-06-01'
  - effective_to = DATE '9999-12-31' (or NULL per design doc — confirm max-date convention)
  - is_current = TRUE
  - customer_sk is non-null and unique (auto-generated surrogate key).
  - DAG dag_sales_fdp all tasks = "success".
  - IBM TWS job TWS_SALES_FDP = "Successful".

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : [ASSUMPTION] effective_to uses '9999-12-31' as the sentinel max date
                      for active records. Confirm this convention with the data engineer —
                      some implementations use NULL instead.
```

---

```
TEST SCENARIO ID    : FDP-SCD2-002
TITLE               : SCD2 — Changed attribute on existing customer closes old row and inserts new active row

LAYER               : FDP Layer
CATEGORY            : SCD Type 2 Logic
COMPONENT UNDER TEST: SCD2 MERGE Logic in Airflow DAG dag_sales_fdp

PRIORITY            : Critical

PRECONDITIONS       :
  - fdp_dataset.sales_fdp contains exactly 1 existing row for customer_id = 'C-500':
      customer_sk = 9001 (example), customer_id = 'C-500',
      customer_name = 'Beta Industries', customer_region = 'NORTH',
      effective_from = '2024-05-01', effective_to = '9999-12-31', is_current = TRUE
  - raw_dataset.sales_raw contains an updated record for customer_id = 'C-500'
    for batch_date = '2024-06-10':
      customer_id = 'C-500', customer_name = 'Beta Industries Ltd',
      customer_region = 'NORTH', batch_date = '2024-06-10'
      (customer_name changed from 'Beta Industries' to 'Beta Industries Ltd')

TEST DATA           :
  - Pre-existing FDP row: customer_sk=9001, customer_id='C-500',
    customer_name='Beta Industries', is_current=TRUE, effective_to='9999-12-31'
  - Incoming change: customer_name='Beta Industries Ltd' (region unchanged)
  - batch_date of change: 2024-06-10

TEST STEPS          :
  1. Confirm the pre-existing row exists in fdp_dataset.sales_fdp with is_current=TRUE.
  2. Submit IBM TWS job TWS_SALES_FDP manually for batch_date = 2024-06-10.
  3. Monitor DAG dag_sales_fdp until all tasks = "success".
  4. Query fdp_dataset.sales_fdp for all versions of customer_id = 'C-500':
       SELECT customer_sk, customer_name, customer_region,
              effective_from, effective_to, is_current
       FROM fdp_dataset.sales_fdp
       WHERE customer_id = 'C-500'
       ORDER BY effective_from;

EXPECTED RESULT     :
  - Exactly 2 rows returned for customer_id = 'C-500'.
  - Row 1 (old version, now closed):
      customer_sk = 9001, customer_name = 'Beta Industries',
      effective_from = '2024-05-01', effective_to = '2024-06-09', is_current = FALSE
  - Row 2 (new version, now active):
      customer_name = 'Beta Industries Ltd', customer_region = 'NORTH',
      effective_from = '2024-06-10', effective_to = '9999-12-31', is_current = TRUE
      customer_sk = [new unique value, different from 9001]
  - No date gap between old row's effective_to and new row's effective_from
    (old effective_to = new effective_from minus 1 day).
  - No overlap: old row closed before new row opens.
  - DAG dag_sales_fdp all tasks = "success".

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : [ASSUMPTION] effective_to of old row = batch_date - 1 day (i.e., 2024-06-09).
                      Some implementations use batch_date itself as effective_to of old row.
                      Confirm the exact closing date convention with the data engineer before
                      running — incorrect assumption will cause this test to fail.
```

---

```
TEST SCENARIO ID    : FDP-SCD2-003
TITLE               : SCD2 — Unchanged record re-processed does not create a new version row

LAYER               : FDP Layer
CATEGORY            : SCD Type 2 Logic / Idempotency
COMPONENT UNDER TEST: SCD2 MERGE change-detection logic in dag_sales_fdp

PRIORITY            : Critical

PRECONDITIONS       :
  - fdp_dataset.sales_fdp contains 1 active row for customer_id = 'C-501':
      customer_name = 'Gamma LLC', customer_region = 'EAST',
      effective_from = '2024-06-01', effective_to = '9999-12-31', is_current = TRUE
  - raw_dataset.sales_raw contains a record for customer_id = 'C-501' with
    batch_date = '2024-06-10' but with IDENTICAL attribute values:
      customer_name = 'Gamma LLC', customer_region = 'EAST'

TEST DATA           :
  - Source row (same attributes, no change):
      customer_id = 'C-501', customer_name = 'Gamma LLC', customer_region = 'EAST'
  - FDP before run: 1 row with is_current=TRUE for C-501.

TEST STEPS          :
  1. Confirm 1 row exists for customer_id='C-501' in fdp_dataset.sales_fdp with is_current=TRUE.
  2. Submit IBM TWS job TWS_SALES_FDP for batch_date = 2024-06-10.
  3. Monitor DAG dag_sales_fdp to completion.
  4. Query fdp_dataset.sales_fdp:
       SELECT COUNT(*) AS version_count FROM fdp_dataset.sales_fdp
       WHERE customer_id = 'C-501';
  5. Query existing row details:
       SELECT effective_from, effective_to, is_current FROM fdp_dataset.sales_fdp
       WHERE customer_id = 'C-501';

EXPECTED RESULT     :
  - version_count = 1 (no new row created).
  - existing row: effective_from = '2024-06-01', effective_to = '9999-12-31', is_current = TRUE
    (row is completely unchanged).
  - DAG dag_sales_fdp all tasks = "success".
  - IBM TWS job TWS_SALES_FDP = "Successful", downstream CDP job triggered.

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : This tests that the SCD2 change detection correctly compares all
                      tracked attributes. If the comparison is missing a field, every
                      re-run will create a false new version, causing table bloat and
                      incorrect historical data.
```

---

## CDP Layer Examples

---

```
TEST SCENARIO ID    : CDP-TL-001
TITLE               : CDP daily sales aggregation by region produces correct SUM and COUNT

LAYER               : CDP Layer
CATEGORY            : Transformation Logic
COMPONENT UNDER TEST: Aggregation Task in Airflow DAG dag_sales_cdp

PRIORITY            : Critical

PRECONDITIONS       :
  - FDP table fdp_dataset.sales_fdp contains exactly 5 rows for sale_date = '2024-06-01':
      SOUTH: sale_amount=1500.00, 250.00 → SUM=1750.00, COUNT=2
      NORTH: sale_amount=980.00 → SUM=980.00, COUNT=1
      EAST:  sale_amount=300.00, 450.00 → SUM=750.00, COUNT=2
  - CDP table cdp_dataset.sales_summary exists (region, sale_date, total_sales, order_count).
  - No rows in cdp_dataset.sales_summary for sale_date = '2024-06-01'.

TEST DATA           :
  - FDP source rows (sale_date='2024-06-01'):
      {region='SOUTH', sale_amount=1500.00}
      {region='SOUTH', sale_amount=250.00}
      {region='NORTH', sale_amount=980.00}
      {region='EAST',  sale_amount=300.00}
      {region='EAST',  sale_amount=450.00}
  - Expected CDP aggregated output:
      SOUTH: total_sales=1750.00, order_count=2
      NORTH: total_sales=980.00,  order_count=1
      EAST:  total_sales=750.00,  order_count=2

TEST STEPS          :
  1. Confirm FDP table has exactly the 5 rows described above for sale_date='2024-06-01'.
  2. Submit IBM TWS job TWS_SALES_CDP manually for batch_date = 2024-06-01.
  3. Monitor DAG dag_sales_cdp until all tasks = "success".
  4. Query CDP table:
       SELECT region, sale_date, total_sales, order_count
       FROM cdp_dataset.sales_summary
       WHERE sale_date = '2024-06-01'
       ORDER BY region;

EXPECTED RESULT     :
  - Exactly 3 rows returned (one per region).
  - EAST:  total_sales = 750.00, order_count = 2
  - NORTH: total_sales = 980.00, order_count = 1
  - SOUTH: total_sales = 1750.00, order_count = 2
  - No other rows for sale_date='2024-06-01'.
  - DAG dag_sales_cdp all tasks = "success".
  - IBM TWS job TWS_SALES_CDP = "Successful".

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : Always validate aggregations against a manually calculated expected
                      value, never compare two BQ queries against each other — both could
                      have the same bug. Use the exact row values listed here as the
                      ground truth.
```

---

```
TEST SCENARIO ID    : CDP-DEP-001
TITLE               : CDP IBM TWS job does not trigger when upstream FDP job has failed

LAYER               : CDP Layer
CATEGORY            : Orchestration (IBM TWS / Airflow)
COMPONENT UNDER TEST: IBM TWS Job Dependency — TWS_SALES_CDP depends on TWS_SALES_FDP

PRIORITY            : Critical

PRECONDITIONS       :
  - IBM TWS job TWS_SALES_CDP is configured as a dependent successor of TWS_SALES_FDP.
  - Simulate FDP pipeline failure: cause DAG dag_sales_fdp to fail (e.g., by
    temporarily revoking BQ write permissions for the FDP service account, or by
    placing a deliberately corrupt source row in the raw table).

TEST DATA           :
  - FDP pipeline deliberately fails for batch_date = 2024-06-07.
  - CDP table cdp_dataset.sales_summary should remain unchanged for sale_date='2024-06-07'.

TEST STEPS          :
  1. Introduce the FDP failure condition (revoke BQ write permission or inject bad data).
  2. Submit IBM TWS job TWS_SALES_FDP manually for batch_date = 2024-06-07.
  3. Confirm DAG dag_sales_fdp fails in Cloud Composer UI.
  4. Confirm IBM TWS job TWS_SALES_FDP = "Failed" in TWS console.
  5. Wait 15 minutes and verify IBM TWS job TWS_SALES_CDP status in TWS console.
  6. Query CDP table:
       SELECT COUNT(*) FROM cdp_dataset.sales_summary WHERE sale_date = '2024-06-07';

EXPECTED RESULT     :
  - IBM TWS job TWS_SALES_FDP = "Failed".
  - IBM TWS job TWS_SALES_CDP = NOT triggered; status = "Waiting" or "Hold"
    (NOT "Running", NOT "Successful").
  - Airflow DAG dag_sales_cdp = NOT triggered; no DAG run created for batch_date=2024-06-07.
  - COUNT in cdp_dataset.sales_summary for sale_date='2024-06-07' = 0
    (no partial or stale CDP data written).
  - Alert notification sent for FDP failure within 5 minutes of failure.

ACTUAL RESULT       :
PASS / FAIL         :
NOTES / RISK        : This verifies the most important cross-layer dependency. If TWS
                      dependency is misconfigured, the CDP layer would run on stale or
                      incomplete FDP data silently. Must be tested before go-live.
                      Restore BQ permissions after test and re-run FDP manually to
                      clean up the failed state.
```

---

*End of examples. Real scenarios generated from a specific Confluence PDF will use
the actual GCS bucket names, BQ dataset/table names, TWS job names, Airflow DAG names,
field names, and business rules described in that document.*
