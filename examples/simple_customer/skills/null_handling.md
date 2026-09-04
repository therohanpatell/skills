# NULL and Default Handling Skill

`COALESCE(col, 'DEFAULT')` and `IFNULL` both branch on nullability.

- Generate one row with `col IS NULL` to exercise the default.
- Generate one row with a real value to exercise the pass-through.
- Only do this for columns actually wrapped in COALESCE/IFNULL -- a NULL in an
  unrelated column proves nothing.
