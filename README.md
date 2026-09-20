# DTF test data generator with local Ollama

Generate BigQuery source-table `INSERT` statements from JSON schemas, a DTF
configuration, and Markdown knowledge files. Python constructs the values and
SQL; your local Ollama model reviews transformation requirements using your
knowledge. No Copilot account or cloud LLM is needed.

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

Run the included example without Ollama:

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

Sections are ranked against the DTF vocabulary and transformation types, with
a shared 3,600-character knowledge budget. CLI output reports which files
contributed and which selected files were omitted. Large documents are not
sent in full. Put operation names and relevant column names in headings.
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

Tests cover the example fixtures, coverage, JSON serialization/cache roundtrip,
static/model cache separation, retry after a failed model request, knowledge
prompt construction, malformed model responses, and the Ollama HTTP contract
using a local test server. These tests do not execute SQL in BigQuery.
