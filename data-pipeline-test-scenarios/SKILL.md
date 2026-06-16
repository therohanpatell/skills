---
name: data-pipeline-test-scenario-generator
description: >
  Use this skill whenever a tester or QA engineer provides a Confluence PDF
  describing a single data pipeline design and wants to generate comprehensive,
  detailed test scenarios from it. Always one PDF at a time — it will describe
  exactly one pipeline belonging to exactly one layer: Ingestion Layer
  (CSV/TXT from GCS → BigQuery raw), FDP — Foundation Data Product (BigQuery →
  BigQuery with transformations and optional SCD Type 2), or CDP — Consumption
  Data Product (FDP BigQuery → BigQuery with transformations). Orchestration is
  IBM TWS triggering Apache Airflow DAGs on GCP. Always use this skill when a
  Confluence PDF describes a pipeline design and the user wants test scenarios,
  test cases, or QA coverage — even if they just say "test this", "what should
  I test", or "generate test cases". CRITICAL: after identifying which layer the
  PDF belongs to, generate scenarios ONLY for that layer — never for all three.
---

# Data Pipeline Test Scenario Generator
## GCP Batch Platform · One Pipeline · One Layer at a Time

A skill for reading a Confluence PDF describing **one specific pipeline** within
a GCP batch data platform, detecting which of the three layers it belongs to,
and generating exhaustive test scenarios scoped exclusively to that pipeline.

---

## Platform Architecture Context

### Fixed Technology Stack — Always Apply
| Component | Technology |
|---|---|
| Source (Ingestion) | CSV / TXT files in a Google Cloud Storage (GCS) bucket |
| Source (FDP / CDP) | BigQuery table |
| All targets | BigQuery tables — always |
| Processing mode | Batch only — no streaming, no real-time |
| Orchestration | IBM TWS (Tivoli Workload Scheduler) → triggers → Apache Airflow DAG on GCP |
| No Kafka · No REST APIs · No JSON payloads · No Snowflake · No message queues |

### Three Layer Types — One PDF Covers Exactly One

Each Confluence PDF describes **one pipeline** belonging to **one layer**. The
three layers are distinct and each has its own separate design document.

| Layer | Source | Target | Key characteristics |
|---|---|---|---|
| **Ingestion** | CSV / TXT file in GCS | BigQuery raw table | No transformations. Raw landing only. Recon = file row count vs BQ row count. |
| **FDP** (Foundation Data Product) | BigQuery ingestion/raw table | BigQuery FDP table | Transformation logic. Optional SCD Type 2. Recon. Error/rejection table. |
| **CDP** (Consumption Data Product) | BigQuery FDP table | BigQuery CDP table | Aggregations, filters, joins. Recon. Consumer-facing output. |

---

## Step 1 — Detect the Layer and Lock Scope

**Read the entire Confluence PDF first.** Do not generate a single scenario until
you have completed all sub-steps below.

### 1a. Identify the Layer — REQUIRED BEFORE ANY OUTPUT

Apply this decision table to the PDF:

| The PDF describes... | → Detected Layer |
|---|---|
| Reading a CSV or TXT file from GCS, loading rows into a BQ raw/staging table with no business transformations | **Ingestion** |
| Reading from a BQ raw/ingestion table, applying field mappings and business transformations (and possibly SCD Type 2), writing to a BQ FDP table | **FDP** |
| Reading from a BQ FDP table, applying aggregations / filters / joins, writing to a BQ CDP/consumption table | **CDP** |

⚠️ **SCOPE LOCK**: Once you identify the layer, you must generate scenarios
**exclusively** for that layer using only the categories defined for it in Step 2.
Do not reference or generate scenarios for the other two layers.

Print this block at the very top of your output before any scenarios:

```
═══════════════════════════════════════════════════════════
DETECTED LAYER   : [Ingestion / FDP / CDP]
PIPELINE NAME    : [Full name from the design document]
PIPELINE SHORT ID: [2-4 uppercase letters for scenario IDs, e.g. CUST / PROD / FIN]
DESIGN DOCUMENT  : [Confluence page title if visible]
═══════════════════════════════════════════════════════════
```

### 1b. Extract All Design Details for the Detected Layer

**If Ingestion Layer — extract:**
- GCS bucket name and file path/prefix
- File format: delimiter, encoding, whether header row is present, quote character
- File naming convention and expected arrival schedule
- Column-to-BigQuery field mapping (source column name → BQ field name, BQ data type)
- Which fields are required vs optional; which BQ fields are nullable
- Target BigQuery dataset name and table name
- Recon logic: what is being reconciled (row count? sum of a field?) and where the result is logged
- IBM TWS job name and scheduled trigger time
- Airflow DAG name and the sequence of tasks within it
- Error/alert behavior: what happens if the file is missing, corrupt, or wrong schema?
- Retry policy on the Airflow DAG

**If FDP Layer — extract:**
- Source BQ dataset and table name (the ingestion/raw table)
- Target BQ FDP dataset and table name
- Every transformation rule described (document each one individually)
- Whether SCD Type 2 is implemented for this pipeline:
  - If yes → identify: business/natural key field(s), surrogate key field,
    effective_from field, effective_to field, is_current flag field, and the sentinel max-date value used
- Recon logic: source count vs target count, or metric-based (SUM/etc.), and where logged
- IBM TWS job name and which upstream TWS job it depends on
- Airflow DAG name and task sequence
- Any lookup or reference BQ tables joined in transformations
- Error/rejection table: name, schema, what triggers a row to land there
- Retry policy

**If CDP Layer — extract:**
- Source BQ FDP dataset and table name
- Target BQ CDP dataset and table name
- Every transformation, aggregation (GROUP BY / SUM / COUNT / AVG), and filter rule
- Any joins between multiple FDP tables (join type: INNER / LEFT / RIGHT)
- Recon logic and where logged
- IBM TWS job name and which upstream FDP TWS job it depends on
- Airflow DAG name and task sequence
- Retry policy

**Extract for any layer:**
- SLA window (the time by which this pipeline must complete)
- Expected data volumes (normal daily row count, peak row count, file sizes)
- PII fields and how they must be handled
- BigQuery partition key and clustering key if defined
- Audit/metadata columns written by the pipeline (e.g., `pipeline_run_id`, `insert_timestamp`, `batch_date`, `source_file_name`)
- GCP service account name used by the pipeline
- BQ dataset-level or table-level IAM access controls

---

## Step 2 — Generate Test Scenarios for the Detected Layer Only

⚠️ **Generate scenarios ONLY for the layer identified in Step 1a.**
Skip the category blocks for the other two layers entirely — they are listed
here for reference but must NOT produce any output in this session.

Use the scenario template in Step 3 for every scenario you generate.
Minimum 5 scenarios per category that applies to the detected pipeline.

---

### ▶ IF DETECTED LAYER = INGESTION — Use These Categories Only

#### ING-FHP: Functional / Happy Path
- Valid CSV/TXT file is picked up from GCS, all rows loaded to BQ raw table with correct field mapping
- File with maximum expected columns all populated loads correctly
- File with only mandatory columns (all optional columns empty) loads correctly

#### ING-SCH: Schema & File Contract Validation
- File has extra columns not in mapping — pipeline handles gracefully (rejects or ignores per spec)
- File has fewer columns than expected — correct error raised
- File has columns in different order than expected — mapping still correct (name-based not position-based)
- Column data type mismatch (e.g., alphabetic value in a numeric BQ field) — error handling
- Header row missing — pipeline detects and errors correctly
- Header row present when not expected — data treated as header or correct error raised
- Wrong delimiter used (e.g., pipe `|` instead of comma) — pipeline errors correctly

#### ING-DQ: Data Quality
- NULL values in non-nullable BQ fields — rejected or error-logged per spec
- Empty string in a required field — treated as null or raises error per spec
- Numeric field containing special characters (e.g., `#N/A`, `--`)
- Date field with incorrect format (e.g., `31-13-2024`, `20240132`)
- String field exceeding maximum BQ column length
- Special characters in string fields (quotes, backslashes, newlines within a field)
- File encoding mismatch (e.g., file is Latin-1 but pipeline expects UTF-8)
- Duplicate rows within the same source file

#### ING-FILE: File Handling
- Source file not present in GCS at trigger time — correct error/alert raised
- Source file is zero bytes (empty file) — correct handling per spec
- Source file contains only the header row, no data rows — pipeline handles gracefully
- Source file is corrupted / unreadable — pipeline errors correctly
- Multiple files match the pickup pattern — all are processed or only the correct one is
- File already processed (same filename re-appears) — duplicate processing prevention
- File with Windows line endings (CRLF) vs Unix (LF) — loads correctly
- Very large file (peak volume per SLA) — loads within SLA

#### ING-RECON: Reconciliation
- Recon passes: row count in file matches row count in BQ raw table
- Recon fails: file has 1000 rows but only 998 loaded — recon detects discrepancy and alerts
- Recon metric (if sum-based): sum of key numeric column in file matches sum in BQ
- Recon result is logged to the expected recon/audit table with correct metadata

#### ING-IEO: Idempotency
- Re-running the Ingestion pipeline for the same file does not duplicate rows in BQ raw table
- Re-run after a partial failure loads all rows exactly once

#### ING-EHR: Error Handling & Resilience
- BQ load job fails mid-way — pipeline retries per configured retry policy
- GCS bucket permission denied for service account — correct error raised and alerted
- BQ dataset/table does not exist — correct error raised (no silent failure)
- Airflow task fails — IBM TWS receives failure status and does not trigger downstream FDP job
- Pipeline resumes correctly after infrastructure error is resolved

#### ING-DEP: Orchestration & Scheduling
- IBM TWS triggers Airflow DAG at the correct scheduled time
- Airflow DAG task sequence executes in the correct dependency order
- If IBM TWS job fails to trigger, correct alert is raised
- Airflow DAG succeeds → IBM TWS marks job complete and triggers downstream FDP job
- Airflow DAG fails → IBM TWS does NOT trigger downstream FDP job (dependency hold)
- Manual re-trigger via Airflow UI works correctly

#### ING-PSLA: Performance & SLA
- Pipeline completes within the agreed SLA window for normal daily volume
- Pipeline completes within SLA for peak volume (per design doc)
- BQ raw table is queryable (no table-lock issues) immediately after pipeline completes

#### ING-SEC: Security & Compliance
- Only the designated GCP service account has read access to the GCS source bucket
- Only the designated GCP service account has write access to the BQ raw target table
- PII fields (if any) are handled per spec from point of ingestion
- Source file is deleted/archived from GCS after successful load per retention policy

---

### ▶ IF DETECTED LAYER = FDP — Use These Categories Only

#### FDP-FHP: Functional / Happy Path
- Valid records from BQ ingestion table are transformed and written to FDP table with correct values
- All transformation rules produce correct output for a known input dataset
- Single known source row produces exact expected output row (field-by-field verification)

#### FDP-TL: Transformation Logic
- For **each** business rule described in the design doc, generate a dedicated scenario with concrete input and expected output
- Derived/calculated field produces correct value (e.g., `net_amount = gross_amount - discount`)
- String transformation applied correctly (e.g., UPPER, TRIM, concatenation)
- Date/timestamp transformation correct (e.g., format conversion, timezone adjustment)
- Conditional logic / CASE WHEN produces correct branch for each condition
- Lookup/join with reference table enriches records correctly
- Lookup join finds no match — correct default value or null handling per spec
- Field mapping: source BQ field correctly maps to target FDP field (name, type, value)
- NULL propagation: null in source field results in correct null or default in FDP field

#### FDP-SCD2: SCD Type 2 Logic
⚠️ Generate this category ONLY if the design document explicitly states SCD Type 2
is implemented for this pipeline. If SCD2 is not mentioned, skip this category entirely.
- **New record**: First-ever load of a business key — single active row created with `is_current = TRUE`, `effective_from = load_date`, `effective_to = <max date>`
- **Updated record**: Existing active row receives a changed attribute — old row closed (`is_current = FALSE`, `effective_to = batch_date - 1`), new active row inserted
- **Unchanged record**: Re-processing a record with identical attributes — no new row created, existing row untouched
- **Multiple updates same key in same batch**: Only the latest version becomes current; all intermediate versions have correct date ranges with no gaps or overlaps
- **Reprocess historical batch**: Re-running an older batch does not corrupt current active records or create phantom versions
- **Business key uniqueness**: At any point in time, exactly one row per business key has `is_current = TRUE`
- **Date range integrity**: No gaps and no overlaps in `effective_from`/`effective_to` date ranges for any business key
- **Deleted source record**: Handled per spec (logical delete flag / close current row / no action — confirm from doc)
- **First run ever on empty table**: All incoming records inserted as new active rows correctly

#### FDP-RECON: Reconciliation
- Source BQ ingestion row count matches FDP target row count (or difference is explainable by filters/deduplication per spec)
- Recon on key metric (e.g., SUM of amount field matches between source and target)
- Recon failure is logged to audit/recon table with pipeline run ID, expected count, actual count, delta
- Recon failure triggers correct alert and does NOT allow downstream CDP job to trigger
- Recon passes → downstream CDP job is unblocked

#### FDP-DQ: Data Quality
- Records failing transformation validation are written to the error/rejection table with correct error reason
- Error table captures: source row key, error type, error description, pipeline_run_id, timestamp
- Valid records are not rejected; only invalid ones go to the error table
- Total counts: source count = FDP count + error table count (no silent drops)

#### FDP-IEO: Idempotency
- Re-running FDP pipeline for the same batch date produces identical FDP output (no duplicates)
- For SCD2 pipelines: re-running does not create extra versions for unchanged records
- Re-run after partial failure results in exactly-once delivery to FDP table

#### FDP-EHR: Error Handling & Resilience
- Source BQ ingestion table is empty (Ingestion failed upstream) — FDP pipeline detects and errors correctly, does not create empty FDP output
- BQ query/write job fails mid-way — retry behavior per config
- Reference/lookup table is unavailable or empty — correct error raised
- Airflow task failure in FDP DAG → IBM TWS does not trigger downstream CDP job

#### FDP-DEP: Orchestration & Scheduling
- FDP IBM TWS job only triggers after Ingestion IBM TWS job completes successfully
- FDP IBM TWS job does NOT trigger if Ingestion job failed or is still running
- Airflow FDP DAG task order is correct (e.g., truncate staging → transform → load → recon)
- Manual re-trigger of FDP DAG works without re-running Ingestion

#### FDP-PSLA: Performance & SLA
- FDP pipeline completes within SLA window for normal volume
- FDP pipeline completes within SLA for peak volume

---

### ▶ IF DETECTED LAYER = CDP — Use These Categories Only

#### CDP-FHP: Functional / Happy Path
- Valid FDP records are correctly transformed/aggregated and written to CDP table
- Single known set of FDP source rows produces the exact expected CDP output (field-by-field verification)

#### CDP-TL: Transformation Logic
- For each transformation rule in the CDP design doc, generate a dedicated scenario with concrete input/expected output
- Aggregations: SUM, COUNT, AVG produce correct values — always verify against a manually calculated expected value, not another query
- Filters: only records matching the filter condition appear in CDP; excluded records are confirmed absent
- Joins between FDP tables produce correct output (specify join type: INNER / LEFT / RIGHT per doc)
- Derived fields calculated correctly

#### CDP-RECON: Reconciliation
- Source FDP row count vs CDP target row count (accounting for aggregation/filtering)
- Recon on key metric between FDP and CDP
- Recon failure logged and downstream processes alerted

#### CDP-DQ: Data Quality
- CDP output contains no unexpected nulls in mandatory fields
- CDP output contains no duplicate rows on the primary key
- CDP output values fall within valid business ranges

#### CDP-IEO: Idempotency
- Re-running CDP pipeline for the same batch produces identical output
- Re-run after partial failure results in correct final state with no duplicates

#### CDP-EHR: Error Handling & Resilience
- Source FDP table is empty or not yet populated — CDP pipeline detects and errors correctly
- CDP pipeline failure → correct alert raised, downstream consumers notified

#### CDP-DEP: Orchestration & Scheduling
- CDP IBM TWS job only triggers after FDP IBM TWS job completes successfully
- CDP IBM TWS job does NOT trigger if FDP job failed or is still running
- End-to-end dependency chain: TWS Ingestion job → TWS FDP job → TWS CDP job all chain correctly

#### CDP-PSLA: Performance & SLA
- CDP pipeline completes within SLA window

---

## Step 3 — Test Scenario Template

Use this exact template for **every** scenario.

### Scenario ID Format

```
[LAYER]-[PIPELINE_SHORT_NAME]-[CATEGORY]-[NNN]
```

- **LAYER**: ING / FDP / CDP
- **PIPELINE_SHORT_NAME**: A short identifier derived from the pipeline name in the
  document. Use 2–4 uppercase letters or an abbreviation. Examples:
  - "Customer Sales Ingestion Pipeline" → CUST
  - "Product Master FDP" → PROD
  - "Finance Summary CDP" → FIN
  This ensures scenario IDs from different pipelines within the same layer do not clash.
- **CATEGORY**: Abbreviation from the reference table in Step 5
- **NNN**: 3-digit sequence number starting at 001

**Examples:** `ING-CUST-FHP-001`, `FDP-PROD-SCD2-003`, `CDP-FIN-RECON-002`

### Template

```
---
TEST SCENARIO ID    : [LAYER]-[PIPELINE_SHORT_NAME]-[CATEGORY]-[NNN]
                      e.g. ING-CUST-RECON-001 / FDP-PROD-SCD2-002 / CDP-FIN-TL-003

PIPELINE NAME       : [Full pipeline name from design document]
TITLE               : [Short descriptive name — specific enough to distinguish from other scenarios]

LAYER               : [Ingestion Layer / FDP Layer / CDP Layer]
CATEGORY            : [Full category name, e.g. "SCD Type 2 Logic" / "Reconciliation" / "File Handling"]
COMPONENT UNDER TEST: [Specific component, e.g. "GCS File Pickup Task",
                       "SCD2 MERGE Logic in FDP DAG",
                       "CDP Aggregation Transformation",
                       "IBM TWS Job Dependency Chain"]

PRIORITY            : [Critical / High / Medium / Low]
                      Critical = pipeline cannot function / data correctness blocker
                      High     = SLA risk or significant data quality impact
                      Medium   = edge case with moderate business impact
                      Low      = rarely triggered path, cosmetic, or nice-to-have

PRECONDITIONS       :
  - [IBM TWS environment: job name, schedule status]
  - [Airflow DAG name, unpaused, correct GCP project/composer environment]
  - [GCS bucket state: file present / absent, specific filename — Ingestion only]
  - [BigQuery state: table exists, row counts before test, partition state]
  - [For SCD2: existing rows in FDP table if testing update scenarios]
  - [GCP service account permissions in place]
  - [Reference/lookup tables populated if used]

TEST DATA           :
  - [For Ingestion: exact file content — column names and sample values, row count,
     file name, GCS path]
  - [For FDP/CDP: exact BQ source table rows — list field names and values]
  - [For SCD2: current state of FDP table before the test (existing rows if any)]
  - [Volume: exact number of rows / file size]

TEST STEPS          :
  1. [Exact, unambiguous action — name the tool, command, or UI step]
  2. [For IBM TWS: "Submit IBM TWS job [JOB_NAME] manually" or "Confirm TWS scheduler
     triggers job [JOB_NAME] at [scheduled time]"]
  3. [For Airflow: "Monitor Airflow DAG [DAG_NAME] in Cloud Composer UI until all
     tasks show status 'success' or 'failed'"]
  4. [For BQ verification: write the exact SQL query to run]
  5. [For recon: specify exactly which table/log to check]
  6. ...

EXPECTED RESULT     :
  - [Exact BigQuery row count in target table]
  - [Exact field values for specific test rows — use SQL SELECT statement]
  - [Airflow DAG final status: success / failed]
  - [IBM TWS job completion status: successful / failed, and whether it triggered/held downstream]
  - [Recon table: expected row with run_id, source_count, target_count, status]
  - [GCS bucket: file archived/deleted per spec — Ingestion only]
  - [For SCD2: exact state of rows — is_current values, effective dates]
  - [Error/rejection table: expected row count and error reason for negative tests]

ACTUAL RESULT       : [Tester fills in]
PASS / FAIL         : [Tester fills in]
NOTES / RISK        : [Assumptions made, design doc section reference, known risks,
                       dependency on other scenarios, open questions for dev/architect]
---
```

---

## Step 4 — Organize and Summarize

### 4a. Output Header (always first)
```
═══════════════════════════════════════════════════════════
PIPELINE NAME    : [Full name from design document]
DETECTED LAYER   : [Ingestion / FDP / CDP]
PIPELINE SHORT ID: [2–4 uppercase letters used in scenario IDs]
DESIGN DOCUMENT  : [Confluence page title or document reference if visible]
TOTAL SCENARIOS  : [count — fill in after generating all scenarios]
═══════════════════════════════════════════════════════════
```

### 4b. Group Scenarios by Category — Detected Layer Only

Output categories in the order shown for the detected layer. Do not include
categories from the other two layers.

**If Ingestion Layer:**
Happy Path → Schema Validation → Data Quality → File Handling →
Reconciliation → Idempotency → Error Handling → Orchestration →
Performance & SLA → Security

**If FDP Layer:**
Happy Path → Transformation Logic → SCD Type 2 *(if applicable)* →
Reconciliation → Data Quality → Idempotency → Error Handling →
Orchestration → Performance & SLA

**If CDP Layer:**
Happy Path → Transformation Logic → Reconciliation → Data Quality →
Idempotency → Error Handling → Orchestration → Performance & SLA

### 4c. Coverage Summary Table

Include only the categories relevant to the detected layer:

| Category | # Scenarios | Critical | High | Medium | Low |
|---|---|---|---|---|---|
| [Each applicable category] | N | N | N | N | N |
| **TOTAL** | **N** | **N** | **N** | **N** | **N** |

### 4d. Design Gaps — `[DESIGN GAP]`

List every area where the PDF lacked enough detail to write an unambiguous test.
For each gap state:
- Which category it affects
- What specific information is missing
- Who should provide it (data engineer / architect)

### 4e. Assumptions — `[ASSUMPTION]`

List every assumption made while interpreting the document. Flag these clearly so
developers and testers can validate them before test execution begins.

---

## Step 5 — Output Format Rules

- **Scope**: Generate scenarios only for the one pipeline in the PDF. Do not
  generate scenarios for other layers or other pipelines.
- **Never say** "verify data is correct" — always specify the exact column, value,
  row count, or SQL query.
- **Always name** the exact GCS bucket path, BQ dataset.table, Airflow DAG name,
  and IBM TWS job name as found in the design document.
- **Always provide concrete test data** — actual column names and sample values
  from the document, not generic placeholders like "valid input".
- **Each scenario tests exactly one thing** — do not combine multiple assertions.
- **Minimum 5 scenarios per category** that applies to the detected layer.
- **Every SLA, data volume, business rule, and SCD2 attribute** mentioned in the
  document must produce at least one dedicated scenario.
- **For recon scenarios**: always specify what the recon count/metric is, where
  it is logged, and what action (alert / TWS hold) occurs on failure.
- **For SCD2 scenarios**: always state the exact expected is_current value,
  effective_from, effective_to, and surrogate key behavior.
- **For TWS/Airflow orchestration scenarios**: always specify the TWS job name,
  the Airflow DAG name, and the exact trigger/hold behavior expected.
- **Read the entire PDF before generating** — do not generate scenarios mid-read.

---

## Reference: Scenario ID Category Abbreviations

| Category | Abbreviation |
|---|---|
| Happy Path | FHP |
| Schema Validation (Ingestion) | SCH |
| Data Quality | DQ |
| File Handling (Ingestion) | FILE |
| Reconciliation | RECON |
| Idempotency | IEO |
| Error Handling | EHR |
| Orchestration (TWS + Airflow) | DEP |
| Performance & SLA | PSLA |
| Security | SEC |
| Transformation Logic | TL |
| SCD Type 2 (FDP) | SCD2 |

---

## Important Notes

**Scope rules — read these before generating any output:**
- **One PDF = one pipeline = one layer.** Detect the layer in Step 1a first. Generate scenarios only for that layer using only the categories defined for it in Step 2.
- **Never generate scenarios for a layer the PDF doesn't describe.** If the PDF is about an FDP pipeline, do not generate ING or CDP scenarios.
- **Use the pipeline name** in every scenario ID so IDs stay unique across all pipelines in the platform.

**Technology rules — always apply:**
- Orchestration is always IBM TWS → Airflow. Every orchestration scenario must reference both the TWS job name and the Airflow DAG name.
- Sources are GCS CSV/TXT files (Ingestion) or BigQuery tables (FDP / CDP). Never reference Kafka, REST APIs, JSON payloads, or message queues.
- All targets are BigQuery tables — always.
- Processing is batch only — never streaming or real-time.

**SCD Type 2 rule:**
- Generate FDP-SCD2 scenarios **only** if the design document explicitly states SCD Type 2 is implemented for this specific pipeline. If the doc does not mention SCD2, skip that category completely.

**Quality rules:**
- Never say "verify data is correct" — always specify exact column values, row counts, or SQL queries.
- Always use the actual GCS paths, BQ dataset/table names, TWS job names, and Airflow DAG names from the design doc.
- Always provide concrete test data — actual field names and sample values, not placeholders like "valid input".
- Mark any area where the design doc is ambiguous or incomplete as **[DESIGN GAP]** and flag it in Section 4d.
- Mark any inference made beyond what the doc explicitly states as **[ASSUMPTION]** and list it in Section 4e.
