# Conditional Skill

`CASE WHEN <condition> THEN a ELSE b END` is two paths, not one.

- Boundary conditions (`> 100`) need one value above and one at or below.
- Do not add extra boundary values (99, 100, 101) unless the DTF has more than
  one threshold on the same column.
- Nested CASE branches each count as their own path.
