# DTF test data generator with local Ollama

Generate BigQuery source-table `INSERT` statements from JSON schemas, a DTF
configuration, and Markdown knowledge files. Python constructs the values and
SQL; your local Ollama model reviews transformation requirements using your
knowledge. No Copilot account or cloud LLM is needed.

## Streamlit workflow: permanent knowledge, changing inputs

1. Keep framework Markdown files in `knowledge/` beside `app.py`. This folder is
   resolved relative to the application, even if you launch it from another folder.
2. Run `streamlit run app.py` with your existing Streamlit installation.
3. In **Upload files**, upload one DTF JSON and multiple source DDL JSON files.
   Your permanent knowledge loads automatically; extra knowledge uploads are optional.
4. Select your installed Ollama model and **Use Ollama**, then click
   **Analyze and generate INSERTs**.
5. Review **Overview**, **Transformations**, and **SQL**. Download the combined
   SQL or **SQL + reports** ZIP, containing SQL, analysis, coverage, and a run summary.

You can select a different knowledge directory and press **Save knowledge folder**
to remember it across application restarts. No knowledge upload is needed each run.
The UI sends complete selected knowledge by default. You can use ranked excerpts
for smaller models; large full documents may exceed a model's context window.

By default every selected source table gets an INSERT, with all its columns.
Referenced tables get transformation scenarios; unreferenced tables and sources
without recognized branches get labelled baseline fixtures. Baseline fixtures do
not establish transformation coverage. The source table summary shows exactly
which tables got SQL. You can disable the all-source/all-column options for smaller fixtures.

For bare schema arrays, the filename supplies the table name. The **Table names
and BigQuery destination** expander lets you correct names/project/dataset to match
the DTF. Missing schemas, duplicate short table names, missing join keys, and
unsupported array/nested column types block generation with an actionable error.
Multi-step JSON must be adapted or supplied as combined transformation SQL; the
loader never silently processes just the first step. Invalid required NULLs and
scalar values block SQL downloads. Complex SQL semantics still need review.

Changing files, selected knowledge, model settings, row limits or column options
clears previous results, so downloads cannot silently use an earlier analysis.

## Run without installing packages

Requires Python 3.11+ and an installed Ollama model. The command-line JSON
workflow has **zero third-party runtime dependencies**: no PyYAML, Pydantic,
httpx, requests, Rich, pandas, Streamlit, or setuptools. HTTP uses Python's built-in urllib.
Do not run `pip install`, `setup.py`, or a build command. `pyproject.toml`
contains descriptive metadata only; `run.py` loads the source directly.
Copy this repository to the other laptop, open a terminal in it, and run:

```powershell
python run.py C:\path\to\my-project --dtf transform.json --knowledge C:\path\to\knowledge --model YOUR_INSTALLED_MODEL --out output
```

Replace `YOUR_INSTALLED_MODEL` with the exact name reported by `ollama list`.
If Ollama is not running, start it with `ollama serve`. No model download is
performed by this program. Use `--host http://localhost:11434` if you need to
specify the endpoint.

Run the included CLI example without Ollama:

```powershell
python -S run.py examples/simple_customer --dtf customer_transform.json --no-llm --no-cache --out output
```

`-S` disables site packages and demonstrates that no pip packages are needed.
To use Ollama for the example, omit `--no-llm` and pass `--model`.

## Files to copy from your existing Copilot setup

```text
my-project/
  ddl/
    customer.json
    order.json
  dtf/
    transform.json
  knowledge/
    operations-catalog.md
    transformation-rules.md
```

Your Markdown knowledge files can be reused as reference documents. There is
no required filename. `--knowledge` accepts a file or directory and can be
repeated; Markdown inside the project is also discovered automatically.
Unlike an IDE agent, the local model does not independently open files: this
program reads them and includes selected sections in the prompt.

In excerpt mode, sections are ranked against the DTF vocabulary and transformation
types with a shared 3,600-character budget. The CLI uses excerpt mode; the UI
defaults to full selected documents. Both report contributing knowledge files. Put operation names and relevant column names in headings.
The original JSON is included as context alongside the parsed transformation.

## Supported inputs and limits

DDL JSON accepts BigQuery schema arrays, table documents with `columns`,
BigQuery table resources, and multi-table documents. See `examples/simple_customer/ddl`.
DTF JSON supports SQL in `sql`/`query`, structured sources, mappings, filters,
joins and grouping, and supported nested transformation documents.

Project-specific JSON operation names may need a loader adapter. Knowledge
helps interpret expressions already extracted from the config; it does not
make arbitrary framework syntax executable. Unknown expressions are reported.
If no supported transformation paths are found, the CLI exits with code 2.
Review warnings even when recognized-path coverage is 100%.

Use JSON for schemas and DTF configurations. YAML parsing is not supported;
there is no YAML package dependency or import. Legacy YAML files are reported
and skipped. The included `order_summary.yaml` is a legacy example and produces
a skip warning; use `customer_transform.json` for the runnable example.

## Outputs and unit testing

Each run writes a timestamped directory with:

- `inserts.sql`: fixtures for source tables, including required columns.
- `coverage.json`: recognized paths and whether generated rows exercise them.
- `analysis.json`: transformations, column roles, and analysis warnings.

Run the INSERTs in your BigQuery test environment, execute your DTF, then assert
its actual target rows against your expected results. This tool does not run
BigQuery or your DTF, and does not generate a complete expected-output oracle.
Coverage is a check of input scenarios for recognized rules, not proof of full
SQL or framework correctness. Review joins, aggregates, windows and complex
expressions against your real pipeline.

The included customer example exercises filter pass/fail, LEFT JOIN
match/no-match, a CASE threshold, and COALESCE null/non-null behavior.

## Model handling

The model returns JSON requirements, never INSERT SQL. Responses are validated
with standard-library dataclass decoding. Invalid responses get one repair
attempt; connection or parsing failures fall back to static analysis with a
visible warning. Model findings are checked against source expressions and
schema names, but must still be reviewed for semantic correctness.

`--no-cache` bypasses cached reads. Static-only and model-assisted cache entries
are separate, and failed model requests are not cached. `--deep` includes extra
mapping expressions and asks for review notes in the same model request.

Settings are optional. Copy `config.json.example` to `config.json` to change the
host, default model, timeout, row budget, or output directory. CLI flags override
the corresponding settings. Configuration no longer requires YAML.

## Optional web UI

The existing `app.py` UI requires Streamlit and pandas to already be installed.
If they are available, run `streamlit run app.py`. Otherwise use `python run.py`;
the CLI is the supported path for a laptop where packages cannot be installed.

## Verification

```powershell
python -S -m unittest discover -s tests -v
```

Tests cover uploads, all-source and pass-through fixtures, missing/ambiguous schemas,
full knowledge prompts, changed-input identities, ZIP output, scalar SQL literals,
the example fixtures, coverage, JSON serialization/cache roundtrip,
static/model cache separation, retry after a failed model request, knowledge
prompt construction, malformed model responses, and the Ollama HTTP contract
using a local test server. These tests do not execute SQL in BigQuery.

With Streamlit already installed, also run UI tests:

```powershell
python -m unittest discover -s tests -p test_streamlit_ui.py -v
```

These use Streamlit's AppTest, simulated file uploads, and a mocked Ollama status;
they exercise the real application flow without requiring a working model.
