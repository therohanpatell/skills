# Filter Skill

Row-level filters decide which source rows reach the target.

- A `WHERE` clause needs a passing row **and** a failing row to be proven.
- Equality filters on status/type codes: use a real code from the DDL description,
  never a placeholder.
- A filter on a NULLABLE column also needs a NULL row, because `col = 'X'` is
  false for NULL in BigQuery.
