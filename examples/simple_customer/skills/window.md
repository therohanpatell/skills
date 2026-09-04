# Window Function Skill

`ROW_NUMBER() OVER (PARTITION BY k ORDER BY t)` needs at least two rows sharing
the partition key with different ordering values, otherwise ranking is untested.

Only relevant when the DTF actually contains an `OVER (...)` clause.
