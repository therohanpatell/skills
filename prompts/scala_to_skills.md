# Prompt: convert Scala DTF code into skill files

Use this prompt with any capable model (Claude, or your local Ollama) to turn
your Scala transformation framework into the Markdown skill files that
`app.py` feeds to the analyser.

Why it matters: the built-in parser reads SQL. If your DTF logic lives in a
Scala DSL, the parser will flag those expressions as *not understood* and hand
them to the model. The skill files are what teach the model to read your DSL,
so on a Scala codebase they carry most of the accuracy — not the parser.

---

## How to use

1. Paste the prompt below into a new chat.
2. Attach or paste your Scala transformation sources. Prioritise, in order:
   - the DTF/transformation base classes and traits
   - the DSL helpers (implicit classes, extension methods, operators)
   - 2–3 real transformation implementations
   - any enum / constants / lookup objects holding domain codes
3. Save each file it returns into your project's `skills/` folder.
4. In the app, press **Auto** in the sidebar to confirm they get selected.

---

## The prompt — copy everything below this line

---

You are documenting a Scala data-transformation framework so that a separate
test-data generator can understand it. That generator reads BigQuery DDL and a
DTF config, decides which source columns and branches a transformation
exercises, and emits minimal `INSERT` statements. It does **not** run Scala.

I will give you Scala source. Produce Markdown "skill" files that teach a model
to recognise this framework's transformation constructs and know what test data
each one needs.

## What to extract

For every transformation construct the code supports, capture:

1. **Recognition** — the literal Scala/DSL syntax, verbatim, that expresses it.
   This is the single most valuable thing you can write down. A model that has
   never seen our DSL must be able to match an unfamiliar expression against
   your examples. Quote real snippets from the source; do not paraphrase.
2. **Semantics** — which source column it reads, and what it does to a row.
3. **Paths that matter** — which distinct branches must be exercised, and
   which are pointless. State when a branch cannot produce output at all
   (e.g. an inner join's non-matching side drops the row, so only the
   matching path is worth generating).
4. **Domain values** — real codes from enums, constants, or match cases
   (`"ACTIVE"`, `"CLOSED"`, `"IN"`, `"WEB"`). Quote the actual literals.
5. **Conventions** — audit/technical columns the framework writes itself
   (`pipeline_run_id`, `insert_timestamp`, `batch_date`), which must never be
   treated as transformation inputs.

## Output files — use these exact names

Emit only the files whose construct actually appears in the code. Filenames are
matched on these keywords, so do not invent other names:

| Filename | Cover |
|---|---|
| `filter.md` | row filters, predicates, `where`-equivalents |
| `join.md` | joins and lookups, and which join types keep a non-matching row |
| `conditional.md` | `if`/`match`/`when` branching that changes an output value |
| `aggregation.md` | grouping and aggregate measures |
| `window.md` | window/analytic functions and partitioning |
| `null_handling.md` | null defaulting, `Option` handling, `getOrElse`, coalesce |
| `dedup.md` | deduplication and distinct logic |
| `scd.md` | SCD Type 2 / history columns, if present |
| `mapping.md` | plain 1:1 column copies and renames |

A file with any other name will not be auto-selected and must be ticked by
hand, so prefer these names.

## Format — this is a hard constraint

The consuming app extracts only **headings and bullet lines**, and keeps at
most **900 characters** per file. Anything past that is discarded before the
model ever sees it. Therefore:

- Put every fact in a `#` heading or a `- ` bullet. Prose paragraphs are dropped.
- Keep each file's headings + bullets **under 900 characters total**. Count them.
- Lead with the recognition patterns; they matter most if truncation happens.
- One fact per bullet. No sub-bullets, no tables, no fenced code blocks —
  put short code inline with backticks instead.
- No preamble, no closing summary. Facts only.

If a topic has more than 900 characters of genuinely load-bearing content, keep
the highest-value rules and drop the rest rather than going over.

## Rules

- Describe **only what the Scala actually does.** If the code does not
  implement SCD2, do not write `scd.md`.
- Prefer concrete literals over descriptions: `status === "ACTIVE"` beats
  "a status equality filter".
- Where the framework has a non-obvious behaviour a naive reader would get
  wrong, say so explicitly — that is the highest-value line in the file.
- Do not describe Scala language features. Describe *this framework*.
- Do not invent values, column names, or constructs not present in the source.
  If something is unclear, omit it rather than guessing.

## Output shape

For each file, output exactly:

```
=== FILENAME: filter.md ===
<file content>
```

Then, after all files, list under `=== UNCERTAIN ===` anything you could not
determine from the source and would want a human to confirm.

## Worked example of the required style

```
=== FILENAME: filter.md ===
# Filter — this framework

- Filters are declared as `.withFilter(Rule("status", Eq, "ACTIVE"))`, not as SQL.
- `Rule(col, op, value)` ops are `Eq`, `Neq`, `Gt`, `Gte`, `Lt`, `Lte`, `In`, `IsNull`.
- `RuleSet(...)` combines rules with AND; `AnyOf(...)` combines with OR.
- A filter on a nullable column also needs a NULL row: `Eq` is false for NULL.
- Valid `status` codes are `ACTIVE`, `INACTIVE`, `CLOSED`, `PENDING`.
- Rows failing a filter are written to the reject table, not dropped silently.
- `.withFilter` on `batch_date` is injected by the framework, not business logic.
```

---

## After you have the files

- Drop them in `skills/`, press **Auto**, and check the reasons shown on hover.
- Run once with **Use Ollama** unchecked to see what the parser understands
  alone; anything listed as *not understood* is what your skills must cover.
- If a construct is still misread, add its recognition pattern as a bullet at
  the **top** of the relevant file, where truncation cannot reach it.
