# Manifest contract

Top-level fields: `schema_version: 1`, lowercase `task_id`, nonempty `prompt`, `files`, optional `invariants`, and optional `forbidden_solver_terms`.

Artifact `format` is `csv`, `json` or `text`; `audience` is `solver` or `reviewer`. CSV rows contain exactly the declared columns. Store decimal financial values as strings. Null CSV values become empty cells. Content is preserved as declared; review formula-like values before opening generated data in a spreadsheet application.

Every invariant needs `id`, `kind` and a declared `file`:

| Kind | Additional fields |
| --- | --- |
| `csv_sum` | `column`, decimal-string `expected` |
| `csv_unique` | `column` |
| `csv_foreign_key` | `column`, declared `target`, `target_column` |
| `json_equals` | `pointer` (empty means root), `expected` |
| `text_contains` | `text` |

Decimal operands are limited to 100 digits and exponents within 100; sums use a 220-digit local context. This is not an arbitrary-precision scientific format.

Manifest validation checks shape, names and references. Build-time evaluation checks values and operation-specific parameters. A manifest may pass `validate` but fail `build`; failed checks report their IDs.

Manifests are trusted author documents; the builder has no global content-size cap. Artifact names cannot escape the build root. Rechecking rejects artifact symlinks and checksum paths outside the package. Concurrent writers must use distinct destination folders.

Exit codes: `0` success, `1` a completed recheck found failures, `2` invalid input or operational failure. Build output is created after all checks pass.
