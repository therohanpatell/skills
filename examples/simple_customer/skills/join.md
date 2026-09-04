# Join Skill

- `INNER JOIN`: only the MATCH path can reach the target; a non-matching row
  disappears, so no NO_MATCH row is needed.
- `LEFT JOIN`: both MATCH and NO_MATCH matter. The left row survives with NULLs
  on the right side.
- Generate the join key on the lookup side only for the rows that should match.
- Never generate lookup columns the transformation does not read.
