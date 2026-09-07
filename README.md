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

## Knowledge folder

`knowledge/` starts empty. Drop your project's DTF documentation in it — **every
Markdown file there is treated as knowledge and is always eligible**, whatever
it is called. There is no required filename and no keyword gate.

```
knowledge/
├── operations-catalog.md
├── transformation-parameter-reference.md
├── json-config-reference.md
└── ... whatever you have
```

Files are split on their Markdown headings, and the sections matching what the
selected DTF actually does are sent to the model, up to a shared budget of
~3,600 characters per run. So:

- A large reference contributes only its relevant sections, never its contents page.
- A file with nothing to say about this DTF contributes nothing and costs no tokens.
- Sections compete across files, so three strong sections of one document beat
  one weak section from each of three documents.
- Sections headed *audit columns*, *domain codes*, *conventions* and similar
  always score, because they help whatever the transformation does.

Sections are scored two ways, so your own wording is enough:

1. **Against the DTF's own vocabulary** — the table names, column names,
   operation names and literals in the config you selected. A section headed
   *Row Restriction Rules* that never says "filter" still ranks, because it
   mentions `employment_status`.
2. Against generic transformation keywords, as a fallback.

Put the subject in the **heading** and the rules in bullets beneath it;
headings are weighted far more heavily than body text.

The **Knowledge sent to the model** expander on the Overview tab shows exactly
which files contributed and how much, so nothing is silently ignored.

## Wide tables

A source table with 200+ columns is the normal case, and only the columns the
transformation reads reach the `INSERT`. On a 223-column fixture the app emits
**18 columns**, omitting 205, at 100% coverage.

Of those 18, some are `NOT NULL FILLER` — columns the transformation never reads
but BigQuery would reject the INSERT without. Untick **Include NOT NULL columns**
in the sidebar to drop them too, which is right when your target test table
allows them to be empty and wrong when it does not.

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

## Grounding: why the model cannot invent a rule

The model is only ever asked to classify expressions the parser could not read.
Four gates stand between its answer and your SQL:

1. **Citation.** Every transformation it reports must quote the
   `source_expression` it was read from, and that quote must match one of the
   expressions actually sent. A rule citing nothing, or citing an expression
   that was never sent, is discarded — even when the table, column and operator
   are all real. If nothing was left unclassified, *no* model transformation is
   admissible, because the parser already understood the whole config.
2. **DDL check.** Unknown tables, unknown columns and unsupported operators are
   rejected and logged.
3. **Static wins ties.** A model rule contradicting a parsed one is dropped.
4. **Coverage verification.** Paths are checked against the rows actually
   generated, so a wrong value shows as a missing path rather than passing.

Values and SQL are generated by Python throughout; the model never writes
either. Knowledge files are supplied as reference data and cannot themselves
introduce a rule — a rule still has to come from the config.

Rejections are visible, not silent:

```
Rejected 2 model transformation(s): they cited no expression that was actually sent.
```

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
