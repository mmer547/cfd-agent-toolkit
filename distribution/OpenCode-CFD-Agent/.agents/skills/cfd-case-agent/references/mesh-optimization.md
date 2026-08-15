# Mesh optimization policy

Treat cell-count and quality requirements as hard constraints. A candidate is accepted only when all constraints pass. Never relax `meshQualityDict` automatically.

For `system/snappyHexMeshDict`, read `snappyhexmesh-dict.md` before choosing a
parameter family. Use its staged castellated/snap/layer diagnosis and geometry
checks; the allowlist and hard constraints in this policy still take precedence.

For every trial, preserve the starting dictionaries and record the proposed changes, applied values, dictionary diff, command plan, logs, metrics, and decision under `.cfd-runs/mesh/<run-id>/`. Change one parameter family at a time so that improvements remain attributable.

Use these parameter families in order:

1. Adjust block divisions and surface or region refinement to reach the cell-count range.
2. Adjust transition and snapping controls for localized non-orthogonality, face-tet, or face-twist failures.
3. Adjust feature capture only when logs and geometry evidence indicate missed or poorly snapped features.
4. Adjust layer controls only when layers are actually generated.

Stop when the maximum iteration count is reached or when the configured number of consecutive trials does not improve the hard-failure count and quality metrics. Preserve the best candidate even when no candidate passes.

Only files under `mutable_files`, entries under `tunable_entries`, and values within each entry's bounds or allowed values may change. Reject every other proposed change, including changes to `protected_files`.

Apply every candidate independently from the recorded baseline. If a candidate passes all hard constraints, leave its dictionaries applied and stop. If a candidate fails, restore the baseline before the next candidate. If execution errors, is interrupted, exhausts candidates, or reaches a stop limit, restore the baseline dictionaries. The generated mesh may represent the last attempted candidate when no candidate passes; do not describe it as accepted.

Rank non-passing candidates lexicographically by hard acceptance, excess cells, failed checks, face-tet errors, non-orthogonality errors, face-twist errors, concave cells, maximum non-orthogonality, and cell count. Store the best iteration in `state.json`; do not silently apply it without regenerating and validating its mesh.

Resume a failed or interrupted run from its `state.json`. Restore the verified baseline before retrying the active candidate. Use `recover-mesh-optimization` to restore baseline dictionaries without starting CFD commands.
