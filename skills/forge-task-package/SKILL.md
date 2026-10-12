---
name: forge-task-package
description: Build and recheck synthetic evaluation input packages from JSON manifests. Use when creating solver artifacts, separating reviewer material, checking cross-file invariants or preparing a reproducible delivery ZIP.
---

# Forge a task package

Read [the manifest contract](references/contract.md) before editing a manifest.
Treat supplied source values and audience boundaries as authoritative. Keep answer keys reviewer-only. Generate fictional records within the user's authorized synthetic-data scope.

1. Resolve the manifest and a new output directory. Inspect artifacts and invariants.
2. Run `python <skill-directory>/scripts/run.py validate <manifest>`.
3. Run `python <skill-directory>/scripts/run.py build <manifest> --output <new-directory>`.
4. Run `python <skill-directory>/scripts/run.py check <new-directory>`.
5. Correct underlying failed checks rather than disabling them.
6. Report the solver artifact count, check results and delivery ZIP. Explain that the full directory contains reviewer material.

Preserve existing output folders. Use a new folder for each revision. Keep publication within the user's requested destination. Do not claim compatibility with an unrelated evaluation platform.
