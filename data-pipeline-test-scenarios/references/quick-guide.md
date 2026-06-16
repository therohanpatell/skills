# How to Use This Skill — Quick Guide for Microsoft Copilot

## What This Skill Does
This skill instructs you (Copilot) to read a Confluence PDF describing a GCP batch
data pipeline and generate comprehensive, detailed test scenarios for QA testers.

## Platform This Skill Is Built For
| What | Technology |
|---|---|
| Source (files) | CSV / TXT files in Google Cloud Storage (GCS) |
| Source (tables) | BigQuery tables |
| All destinations | BigQuery tables (always) |
| Processing | Batch only (no streaming, no Kafka, no APIs) |
| Orchestration | IBM TWS → triggers → Apache Airflow DAGs on GCP |
| Pipeline layers | Ingestion · FDP (Foundation Data Product) · CDP (Consumption Data Product) |

## How to Invoke
Upload the Confluence PDF and say:
- "Generate test scenarios from this pipeline design document."
- "Create test cases for this pipeline using the skill."
- "What should testers verify for this pipeline?"

## Three Pipeline Layers Covered

**Ingestion Layer**: GCS file (CSV/TXT) → BigQuery raw table. No transformations.
Recon between file row count and BQ row count.

**FDP Layer**: BQ ingestion table → BQ FDP table. Transformations + optional SCD
Type 2 + recon.

**CDP Layer**: BQ FDP table → BQ CDP table. Aggregations, filters, transformations
+ recon.

## What Copilot Will Produce

For each layer in the design doc, scenarios across these categories:

| Category | ID Prefix |
|---|---|
| Happy Path | ING/FDP/CDP-FHP |
| Schema / File Contract | ING-SCH |
| Data Quality | ING/FDP/CDP-DQ |
| File Handling | ING-FILE |
| Reconciliation | ING/FDP/CDP-RECON |
| Idempotency | ING/FDP/CDP-IEO |
| Error Handling | ING/FDP/CDP-EHR |
| Orchestration (TWS+Airflow) | ING/FDP/CDP-DEP |
| Performance & SLA | ING/FDP/CDP-PSLA |
| Security | ING-SEC |
| Transformation Logic | FDP/CDP-TL |
| SCD Type 2 | FDP-SCD2 |

## Each Scenario Includes
- Unique ID (e.g., FDP-SCD2-003, ING-RECON-002)
- Layer, Category, Component under test
- Priority (Critical / High / Medium / Low)
- Preconditions (GCS state, BQ table state, TWS job status, Airflow DAG status)
- Test data (exact GCS file content or BQ source rows with field names and values)
- Numbered test steps (exact SQL queries, TWS job names, Airflow DAG names)
- Expected result (exact BQ counts, field values, TWS/Airflow statuses, recon table entries)
- Blank Actual Result and Pass/Fail for the tester

## Reference
See `references/example-scenarios.md` for 8 fully worked examples covering all 3 layers.
