# Provider and command registry

Use the workspace `.cfd-agent.json` for provider profiles, reusable workflow
sets, and optional case overrides. A case-local file remains an explicit full
configuration override. Keep reusable command definitions in
`registry/commands.json`.

Match a profile exactly from the `vendor` and `version` banner in
`system/controlDict`. Use `workflow_set` when several releases share the same
workflow. Use `case_rules` to match specialized workflows from immutable case
structure (`application`, required dictionaries, or required data files), not
from directory names. A rule may discover exactly one sibling source case for
a fixed registered command such as `mapFields`; zero or multiple matches must
stop as ambiguous. Use `case_overrides` only for a genuinely path-specific
exception. Do not fall through to a different installed release
when an exact profile is absent. Provider `bashrc` paths are deployment values:
verify them once for the target WSL installation rather than searching during a run.

Each external command requires a provider, fixed executable, fixed argument list, phase, and parallel flag. A provider requires a WSL distribution and a Linux environment script. Commands from different providers may appear in one workflow when their case formats are compatible.

Parallel-capable commands are rendered as `mpirun -np N <executable> <args>
-parallel` only when `system/decomposeParDict` exists and its
`numberOfSubdomains` is greater than one. A case override may select an
alternative file such as `system/decomposeParDict.6`; commands marked
`uses_decompose_dict` then receive the profile's fixed `decompose_option`.
Obtain `N` only from the selected dictionary. When
the dictionary is absent or specifies one subdomain, render the command serially
and skip entries marked `when_parallel`; never choose `N` from CPU count. Keep
decomposition and reconstruction as explicit configured workflow steps so the
resolver can report why they were included or skipped.

Registry entries may declare fixed case-file conditions:

- `requires`: run only when every listed case-relative file exists;
- `unless`: run only when none of the listed files exists;
- `when_parallel`: run only when the case enables parallel execution.

Use these conditions to select between registered utilities, not to construct
executables or arguments dynamically. For example, the quality-aware checkMesh
entry requires `system/meshQualityDict`, while the basic entry is its fixed
fallback. A configured workflow may contain both and resolve to one.

For a tutorial copy shown in `Allrun`, configure `resource_copies` on a
structurally matched case rule. Each entry must contain an allowlisted provider
environment variable (`source_env`), a fixed relative `source`, a
case-relative `destination`, and the `Allrun` reference. Put
`internal.stageResources` before the utility that consumes the file. The
controller copies missing destinations only, validates that destinations stay
inside the case, and records the plan under `.cfd-runs/`. Never parse or run the
Allrun script, accept `..` or absolute paths, download data, or translate its
arbitrary shell statements.

For `source_env: FOAM_TUTORIALS` only, an entry may explicitly set
`provider_tutorial_fallback: true`. Resolve the fixed source in this order:
`$FOAM_TUTORIALS`, `$WM_PROJECT_DIR/tutorials`, then the `tutorials` directory
adjacent to the configured provider project root derived from its bashrc path.
Do not search other directories. Log all supported roots when none contains
the fixed source.

Use `{application}` only for controller-resolved solver selection from
`system/controlDict`. Do not add shell operators, redirections, substitutions,
or user-provided fragments to registry fields.

The controller also supports Foundation-style solver selection. Resolve a
literal `application` first. If it is absent and `solver` exists, execute
`foamRun`. If `regionSolvers` exists, execute `foamMultiRun`. This matches the
Foundation tutorial `RunFunctions` behavior while retaining Keysight/iconCFD
cases that specify an application executable.

For a Keysight/iconCFD solve, configure an `iconcfd` provider with its actual
WSL distribution and environment script, then use `iconcfd.decomposePar.solve`,
`iconcfd.solver`, and `iconcfd.reconstructPar.solve` in both `solve` and
`rerun`. Keep `internal.prepareRerun` first in `rerun`. Do not register
`cleanCase` as an executable: tutorial clean functions differ by distribution,
and a full case clean may delete an existing mesh. The internal rerun operation
implements the common, mesh-preserving subset deterministically.

To add iconCFD later, first confirm its WSL environment script and exact utility syntax. Then add an `iconcfd` provider to `.cfd-agent.json`, register each approved utility, and reference its command ID from a workflow. Do not assume that an executable exists merely because its output format is OpenFOAM-compatible.
