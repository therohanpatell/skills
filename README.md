# DTF Test Data Generator

A local web app that reads your BigQuery DDL and a DTF transformation config,
works out which source columns and which branches the transformation actually
exercises, and generates the **smallest** set of `INSERT` statements that covers
every one of them.

You copy the SQL, run it in BigQuery yourself, and run your DTF pipeline
yourself. The app never touches BigQuery and never runs your pipeline.

```
DDL + DTF + skills  ──▶  Python analysis  ──▶  one small Ollama call
                                                      │
                              minimal rows  ◀── deterministic generation
                                     │
                              BigQuery INSERT SQL
```

## What makes it small

- **Column selection.** Only columns the transformation reads, plus `REQUIRED`
  columns BigQuery would reject the insert without. Unused columns and plain 1:1
  mappings are omitted from the `INSERT` entirely.
- **Row packing.** Non-conflicting requirements share a row, so one row can cover
  `status = ACTIVE` *and* `country IS NULL` *and* `amount <= 100` *and* a join match.
- **One model call.** Parsing, path expansion, value generation and SQL rendering
  are all deterministic Python. Ollama is only asked to reason about expressions
  the parser could not classify, and it answers in JSON — never SQL.

## Installation

**macOS / Linux**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Ollama

```bash
ollama serve
ollama pull qwen3:7b
```

`qwen3:7b` is the default. `qwen3:14b` and `qwen3:32b` also work — the model is
read from `config.yaml` and can be changed in the sidebar. Installed models are
detected automatically.

The app runs without Ollama too: uncheck **Use Ollama** and it falls back to
deterministic analysis only.

## Run

```bash
streamlit run app.py
```

The app opens at <http://localhost:8501>.

## Workflow

```
Load DDL
   ↓
Load DTF
   ↓
Load skills          (auto-selected from the DTF's own features)
   ↓
Analyze              (one Ollama call, cached)
   ↓
Review coverage      Transformations tab — every path, PASS and FAIL
   ↓
Review data          Generated Data tab — the actual rows
   ↓
Copy / download SQL  SQL tab
   ↓
Run in BigQuery      manually
   ↓
Run your DTF         manually
```

Press **Example** in the sidebar to load `examples/simple_customer`, then
**Analyze DTF**. It demonstrates a filter, a `CASE WHEN` conditional, a
`LEFT JOIN` with both match and no-match paths, and `COALESCE` NULL handling.

## Project layout the app expects

Filenames are never assumed — everything is discovered by inspecting the files.

```
my-dtf-project/
├── ddl/       *.json / *.yaml   BigQuery schemas
├── dtf/       *.json / *.yaml   transformation configs
└── skills/    *.md              DTF conventions and knowledge
```

You can also upload files directly instead of pointing at a directory.

### DDL formats accepted

| Shape | Example |
|---|---|
| `bq show --schema` output | `[{"name": "id", "type": "INT64", "mode": "REQUIRED"}]` |
| Table document | `{"table": "customer", "dataset": "raw", "columns": [...]}` |
| BigQuery table resource | `{"tableReference": {...}, "schema": {"fields": [...]}}` |
| Multi-table document | `{"tables": [ ... ]}` |
| Name → columns mapping | `{"customer": [...], "order": [...]}` |

### DTF formats accepted

| Shape | Example |
|---|---|
| SQL-carrying | `{"name": "...", "sql": "SELECT ... WHERE ..."}` |
| Structured | `{"source": ..., "target": ..., "filters": [...], "joins": [...], "group_by": [...]}` |
| Nested | `{"transformation": { <either of the above> }}` |
| Bare `.sql` file | the query itself |

Both families can be mixed in one file; the loader merges what it finds.

## What the analysis produces

Every source column is classified:

| Role | Generated? | Meaning |
|---|---|---|
| `JOIN KEY` | yes | joins two tables |
| `FILTER` | yes | gates rows in `WHERE` / `HAVING` / `QUALIFY` |
| `CONDITION` | yes | branches a `CASE WHEN` |
| `NULL/DEFAULT` | yes | wrapped in `COALESCE` / `IFNULL` |
| `GROUP BY`, `AGGREGATE` | yes | grouping key or aggregated measure |
| `ORDER BY`, `WINDOW`, `DEDUP KEY` | yes | ordering, partitioning, deduplication |
| `NOT NULL FILLER` | yes | unused, but BigQuery requires a value |
| `1:1 MAPPING` | no | copied to the target unchanged |
| `UNUSED` | no | the transformation never reads it |

Each transformation becomes explicit paths — `PASS`/`FAIL`, `NULL`/`NON_NULL`,
`MATCH`/`NO_MATCH`, `MULTI_ROW`/`SINGLE_ROW` — and coverage is verified against
the rows that were actually generated, not against the intent behind them. Gaps
trigger an automatic repair pass before the SQL is shown.

Two rules keep the generated rows meaningful:

- A row that a `WHERE` clause rejects gets its own scenario, because a discarded
  row cannot demonstrate anything downstream of the filter.
- Every other scenario inherits the happy path — each filter's passing value and
  each join's matching key — so the row it produces actually reaches the branch
  under test.

## Fast vs Deep

| | Ollama calls | Use when |
|---|---|---|
| **Fast** (default) | 1 | almost always |
| **Deep** | 1, plus a validation pass | the DTF has expressions the parser flags as not understood |

## Caching

Analysis is cached on a hash of DDL + DTF + selected skills + model + mode. An
unchanged input never calls Ollama again. **Clear cache** in the sidebar resets it.

## Output

**Save run** writes a timestamped folder:

```
output/
└── 20260904_120000/
    ├── inserts.sql
    ├── coverage.json
    └── analysis.json
```

## Configuration

Copy `config.yaml.example` to `config.yaml` and edit. Sidebar settings override it.

```yaml
ollama:
  host: http://localhost:11434
  model: qwen3:7b
  temperature: 0.1

generation:
  max_rows: 50
  max_retries: 2

output:
  directory: ./output
```

## Architecture

Streamlit handles UI, session state and display only. All transformation
reasoning lives in the engine.

```
app.py                        Streamlit UI
src/dtf_test_gen/
├── engine.py                 the two calls the UI makes: analyse, generate
├── config.py                 config.yaml
├── models/                   Pydantic contracts between every layer
├── loaders/                  DDL / DTF / skill discovery and parsing
├── analysis/                 SQL reading, predicate → path expansion, LLM merge
├── llm/                      Ollama client, prompt building, JSON validation
├── generation/               scenario packing, value generation, row building
├── validation/               coverage verification and repair
├── sql/                      BigQuery INSERT rendering
└── cache/                    on-disk analysis cache
prompts/analyzer.md           the system prompt
examples/simple_customer/     worked example
```

## Not included, by design

- No BigQuery execution — you run the SQL.
- No DTF execution — you run your pipeline.
- No cloud LLM calls — everything is local.
- No pytest or automated test framework. Validation is a runtime feature of the
  app (coverage verification and repair), not a separate test suite.
