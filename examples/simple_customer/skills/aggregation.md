# Aggregation Skill

`GROUP BY k` with `SUM(x)` is only proven when at least one group holds more
than one source row.

- One multi-row group plus one single-row group is enough.
- Vary the aggregated measure between rows in the multi-row group, so an
  incorrect aggregate is visible in the output.
