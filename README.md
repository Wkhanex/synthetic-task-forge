# Synthetic Task Forge

Build reproducible evaluation input packages from a JSON manifest. Keep solver files separate from reviewer answers, check cross-file facts, and produce a deterministic delivery ZIP.

## Quick start

Python 3.11+; no third-party runtime dependencies.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
task-forge validate examples/manifest.json
task-forge build examples/manifest.json --output output/demo
task-forge check output/demo
```

Use `python -m task_forge` from a checkout to run without installation. The example builds three solver artifacts and one reviewer artifact. Five declared invariants check a GBP `1200.50` total, invoice IDs, customer links, the reviewer total and the currency note.

## Outputs

| Path | Purpose |
| --- | --- |
| `inputs/` | Solver CSV, JSON and text artifacts |
| `prompt.md` | Solver task instruction |
| `input_files.zip` | Flat delivery ZIP containing solver artifacts and prompt |
| `reviewer/manifest.json` | Artifact content and invariant configuration |
| `reviewer/checksums.json` | SHA-256 hashes of artifact and prompt bytes |
| `reviewer/check-results.json` | Build-time validation results |
| Other `reviewer/` files | Reviewer-only answers or notes |

Distribute `input_files.zip` to the solver. The entire build directory includes reviewer material.

## Manifest and checks

See [the example](examples/manifest.json) and [contract](docs/manifest-contract.md). Each artifact declares `name`, `format` and `audience`. CSV artifacts declare `columns` and `rows`; text and JSON artifacts declare `content`. File names are flat and case-insensitively unique. One to twenty solver artifacts are allowed.

| Invariant | Meaning |
| --- | --- |
| `csv_sum` | Exact Decimal sum compared to an explicit expected value |
| `csv_unique` | Nonempty, unique column values |
| `csv_foreign_key` | Every source value occurs in a target column |
| `json_equals` | JSON pointer resolves to the expected value and type |
| `text_contains` | Exact substring occurs |

Invariants never execute code or expressions. `forbidden_solver_terms` checks configured literal terms against solver artifacts and the prompt, ignoring case. It catches known markers; prose still needs review for implicit answer leakage.

`build` refuses failed checks and existing output directories. `check` detects changed artifact bytes, hashes and delivery ZIP members. The author controls the manifest and hashes; they are not proof against malicious edits.

ZIP timestamps and member order are fixed. Identical manifests produce identical delivery ZIPs in the same runtime; compression-library differences can affect bytes across environments.

## Codex skill

Portable `plugin.json` and the `.codex-plugin` compatibility manifest describe a skills-only plugin. Install the Python package before copying `skills/forge-task-package` into a supported skills location, or keep the complete repository as your local plugin source. No server or credentials are required.

Invoke `$forge-task-package` with a manifest and a new output directory. The bundled wrapper runs directly from the checkout:

```bash
python skills/forge-task-package/scripts/run.py build examples/manifest.json --output output/skill-demo
```

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q task_forge tests skills
```

GitHub Actions tests Python 3.11, 3.12 and 3.13 and runs the example workflow.

[Reference notes](docs/reference-notes.md). MIT license.
