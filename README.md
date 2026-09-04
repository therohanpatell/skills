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
ollama pull qwen3:8b
```

`qwen3:8b` is the default. Qwen3 ships 0.6b / 1.7b / 4b / 8b / 14b / 30b / 32b —
there is no 7b tag. The model is read from `config.yaml` and can be changed in
the sidebar; installed models are detected automatically.

### Choosing a model

The app sends one small call (~1.3k prompt tokens, a few hundred tokens of JSON
back), so this is a light workload — model choice is about accuracy on the
unclassified expressions, not throughput.

On a **CPU-only machine** speed is bound by memory bandwidth, not RAM capacity,
so a model that *fits* is not necessarily a model you want to wait for:

| Model | ~Disk | Fits in 32 GB | CPU-only feel |
|---|---|---|---|
| `qwen3:4b` | ~2.5 GB | easily | fastest; fine when the parser handles most of the DTF |
| `qwen3:8b` | ~5 GB | easily | **recommended default** — good accuracy, seconds per call |
| `qwen3:14b` | ~9 GB | yes | noticeably slower, modest accuracy gain |
| `qwen3:30b` (MoE) | ~18 GB | yes | 30B total but only ~3B active per token, so it runs far closer to 8b speed than its size suggests — the best accuracy-per-second option on CPU |
| `qwen3:32b` | ~20 GB | yes, but tight | dense 32B on CPU is slow; use only with a GPU |

If you have a discrete GPU, whatever fits in VRAM wins. Without one, start at
`qwen3:8b` and try `qwen3:30b` if you want more reasoning power.

You can also skip the model entirely — uncheck **Use Ollama** for deterministic
analysis only, which handles a DTF whose expressions the parser already
understands.

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
  model: qwen3:8b
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
